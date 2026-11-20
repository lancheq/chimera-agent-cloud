"""Run the dispatch CLI against an isolated WorkBuddy HTTP/ACP test server."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from queue import Queue
import re
import subprocess
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic, sleep
import unittest

ROOT = Path(__file__).resolve().parents[1]
DISPATCH = Path(os.environ.get("CHIMERA_TEST_DISPATCH_BIN", ROOT / "discussion/codex-dispatch.sh"))
PREFIX = "CODEX_DISPATCH_WORKBUDDY_"
SESSION = "smoke-session"
MODEL = "deepseek-v4.1-flash"
ACP = "/v1/agents/acp"
BASE = "/v2/enterprises/demo/managedagent"
AGENT = BASE + "/agents/demo"
CARD = "请返回合成冒烟编号。\n\n编号 618\n"


class WorkBuddyServer(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, status, body=None):
        wire = json.dumps(body).encode() if body is not None else b""
        self.send_response(status)
        self.send_header("Content-Length", str(len(wire)))
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(wire)

    def control(self, status, body):
        if self.headers.get("X-Api-Key") != "ck_mock_fixture":
            self.respond(401)
            return
        self.respond(status, {"code": 0, "data": body})

    def events(self, messages, connection=None):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        if connection:
            self.send_header("Acp-Connection-Id", connection)
        self.end_headers()
        try:
            for message in messages:
                if message is None:
                    break
                self.wfile.write(("data: " + json.dumps(message) + "\n\n").encode())
                self.wfile.flush()
        except (ConnectionError, BrokenPipeError):
            pass

    def session(self):
        return {
            "sessionId": SESSION, "versionId": "pin", "agentId": "demo", "status": "IDLE",
            "endpointSet": [{"endpointType": "PUBLIC", "url": self.server.origin}],
        }

    def do_GET(self):
        if self.path == AGENT + "/versions/pin":
            self.control(200, {**self.session(), "status": "ENABLED", "model": self.server.version_model})
        elif self.path == AGENT + f"/sessions/{SESSION}":
            self.control(200, self.session())
        elif self.path == ACP and self.headers.get("Authorization") == "Bearer smoke-acp":
            self.events(iter(self.server.messages.get, None), "smoke-connection")
        else:
            self.respond(404)

    def do_POST(self):
        size = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(size)) if size else None
        if self.path == BASE + "/user-access-token":
            self.control(201, {"accessToken": "smoke-acp"})
        elif self.path == AGENT + "/sessions":
            self.server.creates.append(body)
            self.control(201, self.session())
        elif self.path == ACP and self.headers.get("Acp-Connection-Id") == "smoke-connection":
            self.acp_post(body)
        else:
            self.respond(404)

    def acp_post(self, body):
        self.server.acp_requests.append(body)
        method = body.get("method")
        response = {"jsonrpc": "2.0", "id": body.get("id"), "result": {}}
        messages = [response]
        if method == "initialize":
            response["result"] = {"protocolVersion": 1}
        elif method == "session/load":
            messages.insert(0, self.update("agent_message_chunk", content={"type": "text", "text": "history"}))
        elif method == "session/prompt":
            if self.server.timeout_reply:
                self.respond(202)
                return
            response["result"] = {"stopReason": self.server.stop_reason}
            messages[:0] = [
                self.update("agent_message_chunk", content={"type": "text", "text": "SMOKE-"}),
                self.update("agent_message_chunk", content={"type": "text", "text": "618"}),
                self.update("usage_update", model=self.server.actual_model, inputTokens=15, outputTokens=3),
            ]
            if self.server.prompt_in_post:
                self.events(messages)
                return
        elif method == "session/cancel":
            self.respond(202)
            return
        else:
            self.respond(400)
            return
        self.respond(202)
        for message in messages:
            self.server.messages.put(message)

    @staticmethod
    def update(kind, **fields):
        return {"jsonrpc": "2.0", "method": "session/update",
                "params": {"sessionId": SESSION, "update": {"sessionUpdate": kind, **fields}}}

    def do_DELETE(self):
        self.server.messages.put(None)
        self.respond(204)


class WorkBuddyDispatchTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.card = self.directory / "card.md"
        self.card.write_text(CARD, encoding="utf-8")
        server = ThreadingHTTPServer(("127.0.0.1", 0), WorkBuddyServer)
        self.addCleanup(server.server_close)
        server.origin = f"http://127.0.0.1:{server.server_port}"
        server.messages = Queue()
        server.creates, server.acp_requests = [], []
        server.version_model = server.actual_model = MODEL
        server.stop_reason = "end_turn"
        server.prompt_in_post = server.timeout_reply = False
        Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        self.server = server
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("CODEX_DISPATCH_")}
        self.env.update(
            CODEX_DISPATCH_HOME=str(self.directory / "dispatch"),
            NO_PROXY="127.0.0.1",
            **{PREFIX + k: v for k, v in dict(
                API_ORIGIN=server.origin, AGENT_ORIGIN=server.origin, API_KEY="ck_mock_fixture",
                ENTERPRISE_ID="demo", AGENT_ID="demo", VERSION_ID="pin", TIMEOUT="2",
            ).items()},
        )

    def cli(self, *args, check=True):
        return subprocess.run(
            ["bash", str(DISPATCH), *args], env=self.env, check=check,
            capture_output=True, text=True, timeout=10,
        )

    def submit(self, *args, check=True):
        return self.cli(
            *args, "--backend", "workbuddy", "--cd", str(self.directory),
            "--prompt-file", str(self.card), check=check,
        )

    def run_dispatch(self, *args):
        result = self.submit(*args)
        status, log = self.wait_for_dispatch(result)
        events = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertNotIn("ck_mock_fixture", log.read_text())
        self.assertNotIn("smoke-acp", log.read_text())
        return status, events

    def wait_for_dispatch(self, result):
        dispatch_id = re.search(r"dispatch-id: (\S+)", result.stdout)[1]
        log = self.directory / "dispatch" / "logs" / f"{dispatch_id}.log"
        deadline = monotonic() + 8
        while monotonic() < deadline and not log.with_suffix(".exit").exists():
            sleep(0.02)
        return self.cli("status", dispatch_id).stdout, log

    def test_new_and_send_read_back_model_answer_and_usage(self):
        for command, delivery in ((("new", "--name", "smoke"), False), (("send", f"wb:{SESSION}"), True)):
            self.server.prompt_in_post = delivery
            model = MODEL + "-ioa" if delivery else MODEL
            self.server.actual_model = model
            status, events = self.run_dispatch(*command)
            self.assertIn("state:    completed", status)
            self.assertIn("session:  wb:" + SESSION, status)
            self.assertIn("model:    " + model, status)
            self.assertEqual(events[-1], {
                "type": "workbuddy.completed", "model": model,
                "usage": {"inputTokens": 15, "outputTokens": 3},
                "stop_reason": "end_turn", "text": "SMOKE-618",
            })
        self.assertEqual(self.server.creates, [{"AgentId": "demo", "VersionId": "pin"}])
        prompts = [req["params"]["prompt"] for req in self.server.acp_requests if req["method"] == "session/prompt"]
        self.assertEqual(prompts, [[{"type": "text", "text": CARD}]] * 2)

    def test_wrong_or_absent_runtime_model_fails(self):
        for model in ("other-model", None):
            self.server.actual_model = model
            status, events = self.run_dispatch("new", "--name", "wrong-model")
            self.assertIn("state:    failed", status)
            self.assertIn("did not confirm", events[-1]["message"])

    def test_version_mismatch_stops_before_creating_a_session(self):
        self.server.version_model = "other-model"
        status, events = self.run_dispatch("new", "--name", "wrong-version")
        self.assertIn("state:    failed", status)
        self.assertIn("version must be enabled", events[-1]["message"])
        self.assertEqual(self.server.creates, [])

    def test_timeout_cancels_without_resubmitting(self):
        self.server.timeout_reply = True
        status, events = self.run_dispatch("new", "--name", "timeout")
        self.assertIn("state:    failed", status)
        self.assertIn("timed out", events[-1]["message"])
        methods = [req["method"] for req in self.server.acp_requests]
        self.assertEqual(methods.count("session/prompt"), 1)
        self.assertEqual(methods.count("session/cancel"), 1)

    def test_non_successful_stop_is_not_completed(self):
        self.server.stop_reason = "refusal"
        status, events = self.run_dispatch("new", "--name", "refusal")
        self.assertIn("state:    failed", status)
        self.assertIn("did not report a normal end_turn", events[-1]["message"])

    def test_missing_credentials_fail_before_dispatch(self):
        del self.env[PREFIX + "API_KEY"]
        result = self.submit("new", "--name", "missing-credentials", check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn(PREFIX + "API_KEY", result.stderr)
        self.assertEqual(self.server.creates, [])

    def test_codex_exec_keeps_original_completion_receipt(self):
        codex = self.directory / "codex"
        codex.write_text(
            "#!/bin/sh\n"
            "cat >/dev/null\n"
            "echo '{\"type\":\"thread.started\",\"thread_id\":\"stub-codex\"}'\n"
            "echo '{\"type\":\"turn.completed\"}'\n"
        )
        codex.chmod(0o700)
        self.env["CODEX_BIN"] = str(codex)
        result = self.cli(
            "new", "--backend", "exec", "--name", "codex-stub",
            "--cd", str(self.directory), "--prompt-file", str(self.card),
        )
        status, _ = self.wait_for_dispatch(result)
        self.assertIn("state:    completed", status)
        self.assertIn("thread:   stub-codex", status)
        self.assertNotIn("workbuddy", status)


if __name__ == "__main__":
    unittest.main()
