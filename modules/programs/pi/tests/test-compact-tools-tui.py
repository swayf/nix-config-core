"""Real Pi fullscreen mouse/Ctrl+O smoke test; synthetic history, no API calls."""
from pathlib import Path
import fcntl
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
import time
import unittest

PI = os.environ.get("PI_BIN") or shutil.which("pi")
EXTENSION = Path(os.environ.get("COMPACT_TOOLS_EXTENSION") or
                 Path(__file__).resolve().parents[1] / "extensions/compact-tools.ts")


@unittest.skipUnless(PI, "requires Pi 1.0.1+ on PATH or PI_BIN")
class CompactToolsTuiTest(unittest.TestCase):
    def test_local_mouse_toggle_and_global_ctrl_o(self):
        with tempfile.TemporaryDirectory(prefix="pi-compact-tui.") as temp:
            root = Path(temp)
            agent = root / "agent"
            agent.mkdir()
            (agent / "settings.json").write_text(json.dumps({
                "quietStartup": True, "defaultProjectTrust": "never", "theme": "dark",
            }))
            usage = dict.fromkeys(("input", "output", "cacheRead", "cacheWrite", "totalTokens"), 0)
            usage["cost"] = dict.fromkeys(("input", "output", "cacheRead", "cacheWrite", "total"), 0)
            assistant = dict(api="openai-responses", provider="openai", model="fixture-model", usage=usage)
            messages = [
                dict(role="user", content="Rendering fixture; no API requests."),
                dict(role="assistant", **assistant, stopReason="toolUse", content=[
                    dict(type="toolCall", id="a", name="ls", arguments={"path": "."}),
                    dict(type="toolCall", id="b", name="write", arguments={
                        "path": "demo.txt", "content": "WRITE_DETAIL_ONE\nWRITE_DETAIL_TWO",
                    }),
                ]),
                dict(role="toolResult", toolCallId="a", toolName="ls", isError=False,
                     content=[dict(type="text", text="LS_DETAIL_ONE\nLS_DETAIL_TWO")]),
                dict(role="toolResult", toolCallId="b", toolName="write", isError=False,
                     content=[dict(type="text", text="Successfully wrote to demo.txt")]),
                dict(role="assistant", **assistant, stopReason="stop",
                     content=[dict(type="text", text="Fixture complete.")]),
            ]
            timestamp = "2026-10-04T00:00:00.000Z"
            entries = [dict(type="session", version=3, id="fixture", timestamp=timestamp, cwd=temp)]
            parent = None
            for index, message in enumerate(messages):
                entry_id = f"{index:08x}"
                entries.append(dict(type="message", id=entry_id, parentId=parent, timestamp=timestamp,
                                    message={**message, "timestamp": index + 1}))
                parent = entry_id
            session = root / "fixture.jsonl"
            session.write_text("".join(json.dumps(entry) + "\n" for entry in entries))
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 100, 0, 0))
            process = subprocess.Popen([
                PI, "--session", str(session), "--no-extensions", "-e", str(EXTENSION),
                "--tui-mode", "fullscreen", "--tools", "read,bash,edit,write,ls,find,grep",
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

            try:
                initial = drain(2)
                self.assertEqual(initial.count("▸"), 2, initial)
                self.assertNotIn("LS_DETAIL_ONE", initial)
                self.assertNotIn("WRITE_DETAIL_ONE", initial)
                # Find the first compact header's absolute terminal row.
                row = re.search(r"\x1b\[(\d+);1H(?:(?!\x1b\[\d+;1H).)*?▸", initial, re.S)
                self.assertIsNotNone(row, initial)
                row = row[1]
                header = next(match[0] for match in re.finditer(
                    rf"\x1b\[{row};1H(?:(?!\x1b\[\d+;1H).)*", initial, re.S,
                ) if "▸" in match[0])
                self.assertRegex(header, r"\x1b\[(?:48;[25];[0-9;]+|4[0-7])m")
                click = f"\x1b[<0;1;{row}M\x1b[<0;1;{row}m".encode()
                os.write(master, click)
                expanded = drain(.4)
                self.assertIn("▾", expanded)
                self.assertIn("LS_DETAIL_ONE", expanded)
                self.assertNotIn("WRITE_DETAIL_ONE", expanded)
                os.write(master, click)
                collapsed = drain(.4)
                self.assertIn("▸", collapsed)
                self.assertNotIn("LS_DETAIL_ONE", collapsed)
                self.assertNotIn("WRITE_DETAIL_ONE", collapsed)
                os.write(master, b"\x0f")
                expanded = drain(.4)
                self.assertIn("LS_DETAIL_ONE", expanded)
                self.assertIn("WRITE_DETAIL_ONE", expanded)
                os.write(master, b"\x0f")
                collapsed = drain(.4)
                self.assertEqual(collapsed.count("▸"), 2)
                self.assertNotIn("LS_DETAIL_ONE", collapsed)
                self.assertNotIn("WRITE_DETAIL_ONE", collapsed)
                self.assertFalse((root / "demo.txt").exists())  # Rendering never executes tools.
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                os.close(master)


if __name__ == "__main__":
    unittest.main()
