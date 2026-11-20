"""Dispatch a task card through Tencent WorkBuddy Managed Agents OpenAPI and ACP."""

import fcntl
import json
import os
from pathlib import Path
from queue import Empty, Queue
import re
import sys
from threading import Thread
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MODEL_IDS = {"deepseek-v4.1-flash", "deepseek-v4.1-flash-ioa"}
PREFIX = "CODEX_DISPATCH_WORKBUDDY_"
ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
ACP_PATH = "/v1/agents/acp"


class DispatchError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def emit(kind, **fields):
    print(json.dumps({"type": kind, **fields}, ensure_ascii=False), flush=True)


def require_id(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise DispatchError("invalid WorkBuddy identifier")
    return value


def origin(url):
    parsed = urlsplit(url)
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback))
        or not parsed.hostname or parsed.username or parsed.password
        or parsed.path not in ("", "/") or parsed.query or parsed.fragment
        or any(char.isspace() for char in url)
    ):
        raise DispatchError("WorkBuddy endpoint must be an HTTPS origin (HTTP is allowed on loopback)")
    return f"{parsed.scheme}://{parsed.netloc.lower()}"


class WorkBuddy:
    def __init__(self):
        required = ("ENTERPRISE_ID", "AGENT_ID", "VERSION_ID", "API_KEY")
        missing = [PREFIX + key for key in required if not os.environ.get(PREFIX + key)]
        if missing:
            raise DispatchError("missing WorkBuddy configuration: " + ", ".join(missing))
        enterprise, self.agent, self.version = (
            require_id(os.environ[PREFIX + key]) for key in required[:3]
        )
        api_key = os.environ[PREFIX + "API_KEY"]
        if not re.fullmatch(r"(?:ck|pt)_[^\s\x00-\x1f\x7f]+", api_key, re.ASCII):
            raise DispatchError("WorkBuddy API key must start with ck_ or pt_")
        self.headers = (
            {"X-Api-Key": api_key} if api_key.startswith("ck_")
            else {"Authorization": f"Bearer {api_key}"}
        )
        host = origin(os.environ.get(PREFIX + "API_ORIGIN", "https://www.workbuddy.cn"))
        self.base = f"{host}/v2/enterprises/{enterprise}/managedagent"
        self.agent_path = f"/agents/{self.agent}"
        self.agent_origin = origin(os.environ.get(
            PREFIX + "AGENT_ORIGIN", f"https://{self.agent}.ma.workbuddy.cn"
        ))
        try:
            self.timeout = int(os.environ.get(PREFIX + "TIMEOUT", "180"))
        except ValueError:
            self.timeout = 0
        if not 1 <= self.timeout <= 3600:
            raise DispatchError("WorkBuddy TIMEOUT must be an integer from 1 to 3600 seconds")
        self.opener = build_opener(NoRedirect())

    def request(self, url, method, headers, data=None, timeout=15):
        if data is not None:
            headers = {**headers, "Content-Type": "application/json"}
            data = json.dumps(data, ensure_ascii=False).encode("utf-8")
        request = Request(url, data=data, headers=headers, method=method)
        try:
            return self.opener.open(request, timeout=timeout)
        except HTTPError as exc:
            exc.close()
            raise DispatchError(f"WorkBuddy {method} returned HTTP {exc.code}") from None
        except (OSError, URLError):
            raise DispatchError(f"WorkBuddy {method} transport error") from None

    def control(self, method, path, data=None):
        headers = {**self.headers, "Accept": "application/json"}
        with self.request(self.base + path, method, headers, data) as response:
            message = json.load(response)
        if message.get("code") != 0 or not isinstance(message.get("data"), dict):
            raise DispatchError(f"WorkBuddy control API rejected {method} {path}")
        return message["data"]

    def preflight(self):
        version = self.control("GET", f"{self.agent_path}/versions/{self.version}")
        if (
            version.get("agentId") != self.agent or version.get("versionId") != self.version
            or version.get("model") not in MODEL_IDS or version.get("status") != "ENABLED"
        ):
            raise DispatchError("WorkBuddy version must be enabled and use DeepSeek V4.1 Flash")
        token = self.control("POST", "/user-access-token").get("accessToken")
        if not isinstance(token, str) or not token or any(char.isspace() for char in token):
            raise DispatchError("WorkBuddy control API did not issue an ACP access token")
        return token

    def endpoint(self, session):
        for entry in session.get("endpointSet", []):
            if entry.get("endpointType") == "PUBLIC" and origin(entry["url"]) == self.agent_origin:
                return self.agent_origin + ACP_PATH
        raise DispatchError("WorkBuddy did not return the configured ACP origin")

    def run(self, session_id, lock_dir, prompt):
        token = self.preflight()
        sessions = f"{self.agent_path}/sessions"
        session = None
        if not session_id:
            emit("workbuddy.creating")
            session = self.control("POST", sessions, {"AgentId": self.agent, "VersionId": self.version})
            session_id = require_id(session.get("sessionId"))
        lock_file = Path(lock_dir) / f"wb:{session_id}.lock"
        with lock_file.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise DispatchError("this WorkBuddy session already has an in-flight dispatch") from None
            emit("workbuddy.session", session_id=session_id)
            if session is None:
                session = self.control("GET", f"{sessions}/{session_id}")
                if (
                    session.get("sessionId") != session_id or session.get("agentId") != self.agent
                    or session.get("versionId") != self.version or session.get("status") != "IDLE"
                ):
                    raise DispatchError("WorkBuddy session must be idle and bound to the configured version")
            acp = ACP(self, self.endpoint(session), token, session_id)
            return acp.run(prompt)


def stream_sse(response):
    data = []
    for raw in response:
        line = raw.decode("utf-8").rstrip("\r\n")
        if not line:
            if data:
                message = json.loads("\n".join(data))
                if isinstance(message, dict):
                    yield message
                data.clear()
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))


class ACP:
    def __init__(self, client, url, token, session):
        self.client = client
        self.url = url
        self.session = session
        self.headers = {
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {token}",
            "X-Agent-Session-Id": session,
        }
        self.events = Queue()
        self.text = []
        self.models = set()
        self.usage = {}

    def receive(self, response, source):
        try:
            with response:
                for message in stream_sse(response):
                    self.events.put((source, message))
        except (OSError, ValueError) as exc:
            self.events.put((source, DispatchError(f"WorkBuddy ACP stream failed ({type(exc).__name__})")))
        finally:
            self.events.put((source, None))

    def send(self, payload, timeout=5):
        response = self.client.request(self.url, "POST", self.headers, payload, timeout)
        if response.headers.get_content_type() == "text/event-stream":
            Thread(target=self.receive, args=(response, "POST"), daemon=True).start()
        else:
            response.close()

    def handle_update(self, message):
        params = message.get("params", {})
        if params.get("sessionId") != self.session:
            return
        update = params.get("update", {})
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            content = update.get("content", {})
            if content.get("type") == "text" and isinstance(content.get("text"), str):
                self.text.append(content["text"])
        elif kind == "usage_update":
            if isinstance(update.get("model"), str):
                self.models.add(update["model"])
            self.usage.update({
                key: update[key] for key in ("inputTokens", "outputTokens", "cachedReadTokens")
                if type(update.get(key)) is int and update[key] >= 0
            })

    def await_response(self, request_id, deadline, capture=False):
        while monotonic() < deadline:
            try:
                source, message = self.events.get(timeout=max(0, deadline - monotonic()))
            except Empty:
                break
            if message is None:
                if source == "GET":
                    raise DispatchError("WorkBuddy ACP GET stream ended before a terminal response")
                continue
            if isinstance(message, DispatchError):
                raise message
            method = message.get("method")
            if method and "id" in message:
                reply = {"jsonrpc": "2.0", "id": message["id"]}
                if method == "session/request_permission":
                    reply["result"] = {"outcome": {"outcome": "cancelled"}}
                else:
                    reply["error"] = {"code": -32601, "message": "Client cannot perform this action"}
                self.send(reply)
                raise DispatchError("WorkBuddy requested an interactive action")
            if capture and method == "session/update":
                self.handle_update(message)
            if not method and message.get("id") == request_id:
                if "error" in message or not isinstance(message.get("result"), dict):
                    raise DispatchError(f"WorkBuddy ACP rejected request {request_id}")
                return message["result"]
        raise DispatchError(f"WorkBuddy ACP request {request_id} timed out")

    def call(self, request_id, method, params, timeout, capture=False):
        deadline = monotonic() + timeout
        self.send(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
            timeout=timeout,
        )
        return self.await_response(request_id, deadline, capture)

    def run(self, prompt):
        response = self.client.request(self.url, "GET", self.headers, timeout=max(30, self.client.timeout))
        connection = response.headers.get("Acp-Connection-Id")
        if not connection or response.headers.get_content_type() != "text/event-stream":
            response.close()
            raise DispatchError("WorkBuddy did not establish an ACP SSE connection")
        self.headers["Acp-Connection-Id"] = connection
        Thread(target=self.receive, args=(response, "GET"), daemon=True).start()
        try:
            initialized = self.call(1, "initialize", {
                "protocolVersion": 1,
                "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}},
            }, 15)
            if initialized.get("protocolVersion") != 1:
                raise DispatchError("WorkBuddy did not negotiate ACP version 1")
            self.call(2, "session/load", {
                "sessionId": self.session, "cwd": "/workspace", "mcpServers": [],
            }, 30)
            emit("workbuddy.prompt_submitting", session_id=self.session)
            try:
                result = self.call(3, "session/prompt", {
                    "sessionId": self.session, "prompt": [{"type": "text", "text": prompt}],
                }, self.client.timeout, capture=True)
            except DispatchError:
                try:
                    self.send({"jsonrpc": "2.0", "method": "session/cancel",
                               "params": {"sessionId": self.session}})
                except DispatchError:
                    emit("workbuddy.cleanup_warning", action="cancel")
                raise
            if result.get("stopReason") != "end_turn":
                raise DispatchError("WorkBuddy did not report a normal end_turn")
            if not self.models or not self.models <= MODEL_IDS:
                raise DispatchError("WorkBuddy did not confirm DeepSeek V4.1 Flash for this turn")
            return {
                "model": ", ".join(sorted(self.models)), "usage": self.usage,
                "stop_reason": "end_turn", "text": "".join(self.text),
            }
        finally:
            try:
                self.client.request(self.url, "DELETE", self.headers, timeout=5).close()
            except DispatchError:
                emit("workbuddy.cleanup_warning", action="disconnect")


def main(args):
    try:
        client = WorkBuddy()
        if args == ["--check-config"]:
            return 0
        session_id, lock_dir = args
        prompt = sys.stdin.buffer.read().decode("utf-8")
        if not prompt.strip():
            raise DispatchError("empty WorkBuddy task card")
        if session_id:
            require_id(session_id)
        emit("workbuddy.completed", **client.run(session_id, lock_dir, prompt))
        return 0
    except DispatchError as exc:
        emit("workbuddy.failed", message=str(exc))
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        emit("workbuddy.failed", message=f"invalid WorkBuddy input or response ({type(exc).__name__})")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
