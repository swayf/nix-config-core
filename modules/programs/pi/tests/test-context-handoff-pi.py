"""Installed Pi 1.1 boundary integration against a local fake API; no paid model calls."""
from pathlib import Path
import http.server
import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import unittest

PI = os.environ.get("PI_BIN") or shutil.which("pi")
EXTENSION = Path(os.environ.get("CONTEXT_HANDOFF_EXTENSION") or
                 Path(__file__).resolve().parents[1] / "extensions/context-handoff")
PACKET = {
    "task": "TASK1", "goal": "Finish authorised runtime ownership work",
    "completed": "Checkpoint one documented; the plan remains open", "next": "Read active.md and reproduce CASE1",
    "constraints": ["Do not commit or restart the shared container"],
    "checks": ["Fixture checks passed; CASE1 is still unverified"],
    "readFirst": ["0_aidocs/roadmap/active.md", "0_aidocs/plans/task.md"],
    "stateFiles": ["0_aidocs/roadmap/active.md", "0_aidocs/plans/task.md"],
    "statePersisted": True, "nextAuthorized": True,
}


class Rpc:
    def __init__(self, test, cwd, env, session, approval="--approve"):
        self.test, self.records, self.events = test, [], queue.Queue()
        self.stderr = tempfile.TemporaryFile()
        self.process = subprocess.Popen(
            [PI, "--offline", "--mode", "rpc", "--no-extensions", "-e", str(EXTENSION),
             approval, "--no-prompt-templates", "--session", str(session)],
            cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr,
        )
        self.counter = 0
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        for line in self.process.stdout:
            value = json.loads(line)
            self.records.append(value)
            self.events.put(value)
        self.events.put({"type": "EOF"})

    def wait(self, predicate):
        end = time.monotonic() + 30
        while True:
            record = self.events.get(timeout=max(0.01, end - time.monotonic()))
            self.test.assertNotEqual(record.get("type"), "extension_error", record)
            self.test.assertNotEqual(record.get("type"), "EOF", self.records[-5:])
            if predicate(record):
                return record
            if time.monotonic() >= end:
                raise AssertionError("RPC deadline expired")

    def command(self, type_, **fields):
        self.counter += 1
        id_ = str(self.counter)
        self.process.stdin.write((json.dumps({"id": id_, "type": type_, **fields}) + "\n").encode())
        self.process.stdin.flush()
        result = self.wait(lambda record: record.get("type") == "response" and record.get("id") == id_)
        self.test.assertTrue(result["success"], result)
        return result.get("data", {})

    def prompt(self, text):
        data = self.command("prompt", message=text)
        if data["disposition"] != "handled":
            result = self.wait(lambda record: record.get("type") == "agent_settled")
            self.test.assertFalse(result["aborted"])

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.reader.join(timeout=2)
        self.process.stdout.close()
        self.stderr.seek(0)
        errors = self.stderr.read().decode()
        self.stderr.close()
        self.test.assertEqual(self.process.returncode, 0, errors)
        self.test.assertNotIn("Extension error", errors)


@unittest.skipUnless(PI, "requires installed Pi on PATH or PI_BIN")
class ContextHandoffPiTest(unittest.TestCase):
    def test_reset_continue_resume_controls_schema_and_worker_exclusion(self):
        requests = []
        scenario = {"name": "success", "stage": 0}

        class FakeApi(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(request)
                name, stage = scenario["name"], scenario["stage"]
                scenario["stage"] += 1
                calls = []
                if name in ("success", "invalid", "parallel"):
                    if stage == 0:
                        calls = [("read", {"path": "0_aidocs/old-exploration.md"})]
                    elif stage == 1:
                        packet = dict(scenario.get("packet", PACKET))
                        if name == "invalid":
                            packet["nextAuthorized"] = False
                        calls = [("handoff_context", packet)]
                        if name == "parallel":
                            calls.append(("read", {"path": "0_aidocs/roadmap/active.md"}))
                    elif stage == 2 and name == "success":
                        calls = [("read", {"path": scenario.get("packet", PACKET)["stateFiles"][0]})]
                elif name == "limit":
                    if stage == 0:
                        calls = [("read", {"path": "0_aidocs/old-exploration.md"})]
                    elif 1 <= stage <= 13:
                        calls = [("handoff_context", {**PACKET, "next": f"Continue step {stage}"})]
                elif name == "renew" and stage == 0:
                    calls = [("handoff_context", {**PACKET, "next": "Continue step 13"})]
                delta = {"role": "assistant"}
                if calls:
                    delta["tool_calls"] = [
                        {"index": i, "id": f"call-{len(requests)}-{i}", "type": "function",
                         "function": {"name": tool, "arguments": json.dumps(args)}}
                        for i, (tool, args) in enumerate(calls)
                    ]
                else:
                    delta["content"] = "FIXTURE_DONE"
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for value, reason in [(delta, None), ({}, "tool_calls" if calls else "stop")]:
                    chunk = {"id": "fixture", "object": "chat.completion.chunk", "created": 1,
                             "model": "fixture", "choices": [{"index": 0, "delta": value, "finish_reason": reason}]}
                    self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                self.wfile.write(b"data: [DONE]\n\n")

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="pi-handoff-real.") as temp:
                root = Path(temp)
                project = root / "arbitrary-project"
                (project / "0_aidocs/roadmap").mkdir(parents=True)
                (project / "0_aidocs/plans").mkdir()
                (project / ".git").mkdir()
                (project / "AGENTS.md").write_text("# Arbitrary project instructions\n")
                (project / ".pi").mkdir()
                config_path = project / ".pi/context-handoff.json"
                policy = {"version": 1, "enabled": True,
                          "readFirst": ["AGENTS.md", "0_aidocs/roadmap/index.md"], "stateRoots": ["0_aidocs/"]}
                config_path.write_text(json.dumps(policy))
                for name in ("0_aidocs/roadmap/index.md", "0_aidocs/roadmap/active.md", "0_aidocs/plans/task.md"):
                    (project / name).write_text("FIXTURE_DURABLE_STATE: open, next CASE1\n")
                (project / "0_aidocs/old-exploration.md").write_text("OLD_HISTORY_MARKER\n" + "old exploration\n" * 500)
                home = root / "home"
                agent = home / ".pi/agent"
                agent.mkdir(parents=True)
                (agent / "settings.json").write_text(json.dumps({
                    "defaultProvider": "fixture", "defaultModel": "fixture", "cacheWarming": "off",
                    "defaultProjectTrust": "never", "compaction": {"enabled": False}, "retry": {"enabled": False},
                }))
                (agent / "models.json").write_text(json.dumps({"providers": {"fixture": {
                    "baseUrl": f"http://127.0.0.1:{server.server_port}/v1", "api": "openai-completions",
                    "apiKey": "dummy", "models": [{"id": "fixture", "contextWindow": 128000, "maxTokens": 1024}],
                }}}))
                env = {key: value for key, value in os.environ.items() if not key.startswith("PI_")}
                env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"), PI_CODING_AGENT_DIR=str(agent),
                           PI_SKIP_VERSION_CHECK="1", PI_TELEMETRY="0")
                session = root / "session.jsonl"

                def names(request):
                    return {tool["function"]["name"] for tool in request.get("tools", [])}

                rpc = Rpc(self, project, env, session)
                try:
                    rpc.prompt("Continue TASK1. USER_CONSTRAINT_MARKER: no commit, no restart.")
                    self.assertEqual(len(requests), 4)
                    self.assertIn("handoff_context", names(requests[0]))
                    self.assertIn("OLD_HISTORY_MARKER", json.dumps(requests[1]))
                    third = json.dumps(requests[2])
                    self.assertNotIn("OLD_HISTORY_MARKER", third)
                    self.assertNotIn("Handoff prepared.", third)
                    for marker in ("USER_CONSTRAINT_MARKER", "Read active.md and reproduce CASE1", "SHA-256", "plan remains open"):
                        self.assertIn(marker, third)
                    self.assertIn("FIXTURE_DURABLE_STATE", json.dumps(requests[3]))
                    self.assertLess(len(third), len(json.dumps(requests[1])))
                    entries = rpc.command("get_entries")["entries"]
                    resets = [entry for entry in entries if entry["type"] == "compaction"]
                    self.assertEqual(len(resets), 1)
                    self.assertEqual(resets[0]["firstKeptEntryId"], resets[0]["id"])
                    self.assertTrue(resets[0]["fromHook"])
                    self.assertIn("OLD_HISTORY_MARKER", json.dumps(entries))  # Raw history preserved.
                    visible = rpc.command("get_messages")["messages"]
                    self.assertNotIn("OLD_HISTORY_MARKER", json.dumps(visible))
                    rpc.prompt("/handoff-context off")
                finally:
                    rpc.close()

                # Real process restart reconstructs the compacted context and branch-local off setting.
                scenario.update(name="inspect", stage=0)
                rpc = Rpc(self, project, env, session)
                try:
                    rpc.prompt("Resume fixture")
                    self.assertNotIn("handoff_context", names(requests[-1]))
                    self.assertIn("USER_CONSTRAINT_MARKER", json.dumps(requests[-1]))
                    self.assertNotIn("OLD_HISTORY_MARKER", json.dumps(requests[-1]))
                    rpc.prompt("/handoff-context on")
                    rpc.prompt("Inspect enabled fixture")
                    self.assertIn("handoff_context", names(requests[-1]))
                    rpc.command("new_session")
                    rpc.prompt("Fresh fixture")
                    self.assertIn("handoff_context", names(requests[-1]))
                    self.assertNotIn("USER_CONSTRAINT_MARKER", json.dumps(requests[-1]))

                    for name in ("invalid", "parallel"):
                        with self.subTest(scenario=name):
                            rpc.command("new_session")
                            scenario.update(name=name, stage=0)
                            rpc.prompt(f"Run {name} fixture")
                            self.assertIn("OLD_HISTORY_MARKER", json.dumps(requests[-1]))
                            entries = rpc.command("get_entries")["entries"]
                            self.assertFalse(any(entry["type"] == "compaction" for entry in entries))
                            if name == "parallel":
                                self.assertIn("handoff must be the only tool", json.dumps(entries))
                            else:
                                errors = [entry for entry in entries if entry.get("message", {}).get("role") == "toolResult"
                                          and entry["message"]["toolName"] == "handoff_context"]
                                self.assertTrue(errors[0]["message"]["isError"])
                finally:
                    rpc.close()

                # One actual user prompt permits twelve successful resets,
                # then an error on the thirteenth, not an unbounded loop.
                scenario.update(name="limit", stage=0)
                before = len(requests)
                limit_session = root / "limit.jsonl"
                rpc = Rpc(self, project, env, limit_session)
                try:
                    rpc.prompt("Run twelve checkpoints. LIMIT_CONSTRAINT: no commit, no restart.")
                    self.assertEqual(len(requests) - before, 15)
                    declaration = next(tool["function"] for tool in requests[before]["tools"]
                                       if tool["function"]["name"] == "handoff_context")
                    self.assertIn("At most 12 resets per user request", declaration["description"])
                    entries = rpc.command("get_entries")["entries"]
                    resets = [entry for entry in entries if entry["type"] == "compaction"]
                    self.assertEqual(len(resets), 12)
                    self.assertEqual(resets[-1]["details"]["next"], "Continue step 12")
                    self.assertTrue(all(entry["fromHook"] for entry in resets))
                    errors = [entry["message"] for entry in entries
                              if entry.get("message", {}).get("role") == "toolResult"
                              and entry["message"]["toolName"] == "handoff_context"
                              and entry["message"]["isError"]]
                    self.assertEqual(len(errors), 1)
                    self.assertIn("Automatic handoff limit reached", json.dumps(errors[0]))
                    visible = json.dumps(rpc.command("get_messages")["messages"])
                    for marker in ("LIMIT_CONSTRAINT", "Continue step 12", "Automatic handoff limit reached"):
                        self.assertIn(marker, visible)
                    self.assertNotIn("OLD_HISTORY_MARKER", visible)
                finally:
                    rpc.close()

                # A new real process retains all twelve recorded boundaries.
                # A new user prompt, not reload, renews the allowance.
                scenario.update(name="renew", stage=0)
                rpc = Rpc(self, project, env, limit_session)
                try:
                    entries = rpc.command("get_entries")["entries"]
                    self.assertEqual(sum(entry["type"] == "compaction" for entry in entries), 12)
                    rpc.prompt("Continue step 13; still no commit or restart.")
                    entries = rpc.command("get_entries")["entries"]
                    resets = [entry for entry in entries if entry["type"] == "compaction"]
                    self.assertEqual(len(resets), 13)
                    self.assertEqual(resets[-1]["details"]["next"], "Continue step 13")
                    self.assertIn("LIMIT_CONSTRAINT", resets[-1]["summary"])
                    self.assertEqual(len(requests) - before, 17)
                finally:
                    rpc.close()

                # Real Pi trust and explicit opt-in both gate tool declarations.
                scenario.update(name="inspect", stage=0)
                for variant in ("untrusted", "missing", "disabled", "malformed"):
                    with self.subTest(policy=variant):
                        config_path.write_text(json.dumps(policy))
                        if variant == "missing":
                            config_path.unlink()
                        elif variant == "disabled":
                            config_path.write_text(json.dumps({**policy, "enabled": False}))
                        elif variant == "malformed":
                            config_path.write_text("{")
                        rpc = Rpc(self, project, env, root / f"{variant}.jsonl",
                                  approval="--no-approve" if variant == "untrusted" else "--approve")
                        try:
                            rpc.prompt("Inspect policy gate")
                            self.assertNotIn("handoff_context", names(requests[-1]))
                            self.assertFalse(any(entry["type"] == "compaction"
                                                 for entry in rpc.command("get_entries")["entries"]))
                        finally:
                            rpc.close()
                config_path.write_text(json.dumps(policy))

                # A different instruction filename/state tree needs no extension-code changes.
                (project / "docs/state").mkdir(parents=True)
                (project / "RULES.md").write_text("# Project rules: use the task constraints\n")
                for name in ("docs/state/active.md", "docs/state/plan.md"):
                    (project / name).write_text("GENERIC_DURABLE_STATE\n")
                config_path.write_text(json.dumps({**policy, "readFirst": ["RULES.md"], "stateRoots": ["docs/"]}))
                generic_packet = {**PACKET, "readFirst": ["docs/state/active.md", "docs/state/plan.md"],
                                  "stateFiles": ["docs/state/active.md", "docs/state/plan.md"]}
                scenario.update(name="success", stage=0, packet=generic_packet)
                before = len(requests)
                rpc = Rpc(self, project, env, root / "generic.jsonl")
                try:
                    rpc.prompt("Continue the same generic task. No commit.")
                    self.assertEqual(len(requests) - before, 4)
                    compacted = json.dumps(requests[before + 2])
                    self.assertIn("RULES.md", compacted)
                    self.assertIn("docs/state/active.md", compacted)
                    self.assertNotIn("foreign-project", compacted)
                    self.assertNotIn("Report in Russian", compacted)
                    self.assertNotIn("FOREIGN_RULESET", compacted)
                    self.assertNotIn("OLD_HISTORY_MARKER", compacted)
                    self.assertIn("GENERIC_DURABLE_STATE", json.dumps(requests[before + 3]))
                finally:
                    rpc.close()
                scenario.pop("packet")
                config_path.write_text(json.dumps(policy))

                # Actual print/JSON workflow workers never advertise the reset tool even with trust/opt-in.
                scenario.update(name="inspect", stage=0)
                for mode in ("text", "json"):
                    command = [PI, "--offline", "--print", "--mode", mode, "--no-session", "--no-extensions",
                               "-e", str(EXTENSION), "--approve", "--", "Worker fixture"]
                    result = subprocess.run(command, cwd=project, env=env, capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                    self.assertIn("FIXTURE_DONE", result.stdout)
                    self.assertNotIn("handoff_context", names(requests[-1]))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
