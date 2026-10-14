"""Real Pi fullscreen prompts/fetch smoke test; local fake API, no model calls."""
from pathlib import Path
import fcntl
import hashlib
import http.server
import json
import os
import pty
import re
import select
import shutil
import struct
import subprocess
import tempfile
import termios
import threading
import time
import unittest

PI = os.environ.get("PI_BIN") or shutil.which("pi")
EXTENSIONS = Path(os.environ.get("PI_EXTENSIONS_DIR") or
                  Path(__file__).resolve().parents[1] / "extensions")


@unittest.skipUnless(PI, "requires pinned Pi on PATH or PI_BIN")
class CompactPromptsTuiTest(unittest.TestCase):
    def test_resume_click_ctrl_o_and_native_template_input(self):
        requests = []

        class FakeApi(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for delta, reason in [({"role": "assistant", "content": "FIXTURE_ANSWER"}, None), ({}, "stop")]:
                    chunk = {"id": "fixture", "object": "chat.completion.chunk", "created": 1,
                             "model": "fixture", "choices": [{"index": 0, "delta": delta, "finish_reason": reason}]}
                    self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                self.wfile.write(b"data: [DONE]\n\n")

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="pi-prompt-tui.") as temp:
                root = Path(temp)
                agent = root / "agent"
                (agent / "prompts").mkdir(parents=True)
                (agent / "prompts/fixture-news.md").write_text(
                    "---\ndescription: Fixture prompt\n---\nNATIVE_TEMPLATE_DETAIL ${1:-all} / $ARGUMENTS\n")
                (agent / "settings.json").write_text(json.dumps({
                    "quietStartup": True, "defaultProjectTrust": "never", "theme": "dark",
                    "cacheWarming": "off", "defaultProvider": "fixture", "defaultModel": "fixture",
                }))
                (agent / "models.json").write_text(json.dumps({"providers": {"fixture": {
                    "baseUrl": f"http://127.0.0.1:{server.server_port}/v1", "api": "openai-completions",
                    "apiKey": "dummy", "models": [{"id": "fixture", "contextWindow": 128000, "maxTokens": 1024}],
                }}}))
                fetch = root / "fetch.ts"
                fetch.write_text('''import { Type } from "typebox";
export default function(pi) {
  pi.registerTool({ name: "fetch_content", label: "Fetch", description: "Fixture only",
    parameters: Type.Object({ url: Type.String() }),
    execute: async () => { throw new Error("Rendering must not execute tools"); } });
}''')
                usage = dict.fromkeys(("input", "output", "cacheRead", "cacheWrite", "totalTokens"), 0)
                usage["cost"] = dict.fromkeys(("input", "output", "cacheRead", "cacheWrite", "total"), 0)
                assistant = dict(role="assistant", api="openai-completions", provider="fixture", model="fixture", usage=usage)
                timestamp = "2026-10-06T00:00:00.000Z"
                entries = [dict(type="session", version=3, id="fixture", timestamp=timestamp, cwd=temp)]
                parent = None

                def append(kind, **data):
                    nonlocal parent
                    entry_id = f"{len(entries):08x}"
                    entries.append(dict(type=kind, id=entry_id, parentId=parent, timestamp=timestamp, **data))
                    parent = entry_id

                for name in ("one", "two"):
                    text = f"PROMPT_DETAIL_{name.upper()}\nSecond line"
                    append("custom", customType="compact-prompt", data={
                        "hash": hashlib.sha256(text.encode()).hexdigest(), "invocation": f"/fixture-news {name}",
                    })
                    append("message", message=dict(role="user", content=text, timestamp=len(entries)))
                append("message", message=dict(role="user", content="ORDINARY_MESSAGE", timestamp=len(entries)))
                append("message", message=dict(**assistant, stopReason="toolUse", timestamp=len(entries), content=[
                    dict(type="toolCall", id="fetch", name="fetch_content", arguments={"url": "https://example.org/news"}),
                ]))
                append("message", message=dict(role="toolResult", toolCallId="fetch", toolName="fetch_content", isError=False,
                       content=[dict(type="text", text="FETCH_DETAIL_ONE\nFETCH_DETAIL_TWO")], timestamp=len(entries)))
                append("message", message=dict(**assistant, stopReason="stop", timestamp=len(entries),
                       content=[dict(type="text", text="Fixture ready.")]))
                session = root / "fixture.jsonl"
                session.write_text("".join(json.dumps(entry) + "\n" for entry in entries))
                master, slave = pty.openpty()
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 48, 100, 0, 0))
                process = subprocess.Popen([
                    PI, "--session", str(session), "--no-extensions", "-e", str(fetch),
                    "-e", str(EXTENSIONS / "compact-tools.ts"), "-e", str(EXTENSIONS / "compact-prompts.ts"),
                    "--tui-mode", "fullscreen", "--provider", "fixture", "--model", "fixture", "--no-tools",
                ], stdin=slave, stdout=slave, stderr=slave, cwd=root, env={
                    **os.environ, "PI_CODING_AGENT_DIR": str(agent), "TERM": "xterm-256color",
                    "PI_SKIP_VERSION_CHECK": "1", "PI_TELEMETRY": "0",
                })
                os.close(slave)

                def drain(seconds):
                    raw = bytearray()
                    until = time.monotonic() + seconds
                    while time.monotonic() < until:
                        if select.select([master], [], [], .05)[0]:
                            try:
                                data = os.read(master, 65536)
                            except OSError:
                                break
                            raw.extend(data)
                            if b"\x1b[6n" in data:
                                os.write(master, b"\x1b[1;1R")
                    return raw.decode(errors="replace")

                def click_row(output, label):
                    for row, content in re.findall(r"\x1b\[(\d+);1H((?:(?!\x1b\[\d+;1H).)*)", output, re.S):
                        if label in content:
                            return f"\x1b[<0;1;{row}M\x1b[<0;1;{row}m".encode()
                    self.fail(f"Missing header {label}: {output}")

                try:
                    initial = drain(2)
                    self.assertIn("▸", initial)
                    self.assertIn("/fixture-news one", initial)
                    self.assertIn("/fixture-news two", initial)
                    self.assertIn("ORDINARY_MESSAGE", initial)
                    for detail in ("PROMPT_DETAIL_ONE", "PROMPT_DETAIL_TWO", "FETCH_DETAIL_ONE"):
                        self.assertNotIn(detail, initial)
                    click = click_row(initial, "/fixture-news one")
                    os.write(master, click)
                    expanded = drain(.4)
                    self.assertIn("PROMPT_DETAIL_ONE", expanded)
                    self.assertNotIn("PROMPT_DETAIL_TWO", expanded)
                    os.write(master, click)
                    collapsed = drain(.4)
                    self.assertNotIn("PROMPT_DETAIL_ONE", collapsed)
                    click = click_row(collapsed, "fetch_content")
                    os.write(master, click)
                    expanded = drain(.4)
                    self.assertIn("FETCH_DETAIL_ONE", expanded)
                    self.assertNotIn("PROMPT_DETAIL_ONE", expanded)
                    os.write(master, click)
                    drain(.4)
                    os.write(master, b"\x0f")
                    expanded = drain(.4)
                    for detail in ("PROMPT_DETAIL_ONE", "PROMPT_DETAIL_TWO", "FETCH_DETAIL_ONE"):
                        self.assertIn(detail, expanded)
                    os.write(master, b"\x0f")
                    drain(.4)
                    self.assertEqual(requests, [])
                    # Use the real prompt loader/expander and original user-input pipeline.
                    os.write(master, b'/fixture-news "topic news"\r')
                    response = drain(2)
                    self.assertIn("FIXTURE_ANSWER", response)
                    self.assertNotIn("NATIVE_TEMPLATE_DETAIL", response)
                    self.assertEqual(len(requests), 1)
                    user = [message for message in requests[0]["messages"] if message["role"] == "user"][-1]
                    expected = "NATIVE_TEMPLATE_DETAIL topic news / topic news"
                    self.assertEqual(user["content"], [{"type": "text", "text": expected}])
                    persisted = [json.loads(line) for line in session.read_text().splitlines()]
                    users = [entry["message"] for entry in persisted
                             if entry["type"] == "message" and entry["message"]["role"] == "user"]
                    self.assertEqual(users[-1]["content"][0]["text"], expected)
                    self.assertTrue(any(entry["type"] == "custom" and entry.get("customType") == "compact-prompt"
                                        and entry["data"]["invocation"] == '/fixture-news "topic news"' for entry in persisted))
                    os.write(master, b"/reload\r")
                    reloaded = drain(1.5)
                    self.assertNotIn("NATIVE_TEMPLATE_DETAIL", reloaded)
                    self.assertEqual(len(requests), 1)
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    os.close(master)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
