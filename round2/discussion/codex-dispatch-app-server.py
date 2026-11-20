"""Dispatch through the Codex app-server that already owns desktop threads."""

import json
import os
from pathlib import Path
import sys
import time

from websockets.exceptions import WebSocketException
from websockets.sync.client import unix_connect


class RpcError(RuntimeError):
    pass


def emit(kind, **data):
    print(json.dumps({"type": kind, **data}, ensure_ascii=False, separators=(",", ":")), flush=True)


class AppServer:
    def __init__(self, connection):
        self.connection = connection
        self.request_id = 0
        self.completed = {}
        self.goals = {}
        self.thread_id = None
        self.call("initialize", clientInfo={
            "name": "codex-dispatch", "title": "Codex Dispatch", "version": "2.0",
        })
        self.send(method="initialized")

    def send(self, **message):
        self.connection.send(json.dumps(message, ensure_ascii=False))

    def receive(self, timeout=None):
        message = json.loads(self.connection.recv(timeout=timeout))
        if message.get("method") == "turn/completed":
            params = message["params"]
            if params["threadId"] == self.thread_id:
                self.completed[(params["threadId"], params["turn"]["id"])] = params["turn"]
        elif message.get("method") == "thread/goal/updated":
            params = message["params"]
            if params["threadId"] == self.thread_id:
                goal = params["goal"]
                self.goals[params["threadId"]] = goal
                emit("goal.event", thread_id=params["threadId"], status=goal["status"])
        elif message.get("method") == "thread/goal/cleared":
            if message["params"]["threadId"] == self.thread_id:
                emit("goal.cleared", thread_id=self.thread_id)
        elif "method" in message and "id" in message:
            self.send(id=message["id"], error={
                "code": -32601, "message": "Unattended dispatch client cannot answer requests",
            })
        return message

    def call(self, method, **params):
        self.request_id += 1
        request_id = self.request_id
        self.send(id=request_id, method=method, params=params)
        deadline = time.monotonic() + 45
        while True:
            reply = self.receive(max(0, deadline - time.monotonic()))
            if reply.get("id") != request_id or "method" in reply:
                continue
            if "error" in reply:
                raise RpcError(f"{method}: {reply['error']['message']}")
            return reply["result"]

    def idle(self, thread_id, cwd):
        waiting = False
        while True:
            thread = self.call("thread/read", threadId=thread_id)["thread"]
            if Path(thread["cwd"]).resolve() != Path(cwd):
                raise RpcError("目标会话工作目录与 --cd 不一致")
            state = thread["status"]["type"]
            if state in ("idle", "notLoaded"):
                return
            if state != "active":
                raise RpcError(f"目标会话状态为 {state}")
            if not waiting:
                emit("dispatch.waiting", thread_id=thread_id, reason="目标会话已有一轮在运行")
                waiting = True
            time.sleep(3)

    def wait_for_turn(self, thread_id, turn_id):
        key = (thread_id, turn_id)
        while key not in self.completed:
            self.receive()
        return self.completed.pop(key)


def dispatch(server, mode, thread_id, cwd, name, model, effort, sandbox, approval, message_id, goal_path):
    prompt = sys.stdin.read()
    objective = Path(goal_path).read_text(encoding="utf-8").strip() if goal_path else ""
    if goal_path and not 0 < len(objective) <= 4000:
        raise RpcError("Goal 文件必须含 1 到 4000 个字符")

    settings = dict(cwd=cwd, model=model, approvalPolicy=approval, sandbox=sandbox,
                    config={"model_reasoning_effort": effort})
    if mode == "new":
        result = server.call("thread/start", **settings)
        thread_id = result["thread"]["id"]
        emit("thread.started", thread_id=thread_id)
        server.call("thread/name/set", threadId=thread_id, name=name)
        sections = server.call("threadSection/list")["data"]
        pinned = next((item for item in sections if item["name"] == "Pinned"), None)
        if pinned:
            server.call("thread/section/move", threadId=thread_id, sectionId=pinned["id"])
    else:
        server.idle(thread_id, cwd)
        server.call("thread/resume", threadId=thread_id, excludeTurns=True, **settings)
        emit("thread.started", thread_id=thread_id)

    server.thread_id = thread_id
    activate_goal = False
    server.idle(thread_id, cwd)
    if objective:
        goal = server.call("thread/goal/get", threadId=thread_id).get("goal")
        if goal and goal["status"] != "complete":
            if goal["objective"] != objective or goal["status"] != "active":
                raise RpcError(f"目标会话已有未结束 Goal（{goal['status']}）")
        else:
            server.call("thread/goal/set", threadId=thread_id, objective=objective,
                        status="paused", origin="user")
            activate_goal = True
        prompt += "\n\n本轮原生 Goal：\n" + objective

    server.idle(thread_id, cwd)
    emit("turn.submitting", thread_id=thread_id, client_message_id=message_id)
    started = server.call("turn/start", threadId=thread_id, cwd=cwd, model=model, effort=effort,
                          approvalPolicy=approval, clientUserMessageId=message_id,
                          input=[{"type": "text", "text": prompt, "text_elements": []}])
    turn_id = started["turn"]["id"]
    emit("turn.started", thread_id=thread_id, turn_id=turn_id)
    if activate_goal:
        goal = server.call("thread/goal/get", threadId=thread_id).get("goal")
        if goal and goal["status"] == "paused" and goal["objective"] == objective:
            goal = server.call("thread/goal/set", threadId=thread_id, status="active",
                               origin="user")["goal"]
        if not goal or goal["objective"] != objective:
            raise RpcError("任务已交付，但 Goal 在激活前发生变化")
        emit("goal.updated", status=goal["status"], objective=objective)
    turn = server.wait_for_turn(thread_id, turn_id)
    if turn["status"] != "completed":
        emit("turn.failed", turn=turn)
        return 1
    emit("turn.completed", turn=turn, thread_id=thread_id)
    if objective:
        goal = server.call("thread/goal/get", threadId=thread_id).get("goal")
        if not goal and server.goals.get(thread_id, {}).get("status") == "complete":
            goal = server.goals[thread_id]
        emit("goal.status", goal=goal)
        if not goal or goal["objective"] != objective:
            raise RpcError("任务轮已完成，但原生 Goal 回读不匹配")
    return 0


def main(args):
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    control = home / "app-server-control" / "app-server-control.sock"
    try:
        with unix_connect(str(control), uri="ws://localhost/", open_timeout=5, max_size=None) as connection:
            server = AppServer(connection)
            if args[0] == "read":
                thread_id = args[1]
                payload = server.call("thread/read", threadId=thread_id)
                pagination = {"cursor": args[3]} if args[3] else {}
                payload["turns"] = server.call("thread/turns/list", threadId=thread_id,
                                               limit=int(args[2]), itemsView="full", **pagination)
                payload["goal"] = server.call("thread/goal/get", threadId=thread_id).get("goal")
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                return 0
            return dispatch(server, *args)
    except (OSError, RpcError, WebSocketException) as error:
        emit("error", message=str(error))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
