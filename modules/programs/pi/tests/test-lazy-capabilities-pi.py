"""Actual Pi tool activation and prompt filtering against a local fake API; no LLM."""
from pathlib import Path
import http.server
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest

PI = os.environ.get("PI_BIN") or shutil.which("pi")
EXTENSION = Path(os.environ.get("LAZY_CAPABILITIES_EXTENSION") or
                 Path(__file__).resolve().parents[1] / "extensions/lazy-capabilities")
INSTALLER = Path(os.environ.get("LAZY_CAPABILITIES_INSTALLER") or
                 Path(__file__).resolve().parents[1] / "install-lazy-capabilities.sh")


@unittest.skipUnless(PI, "requires Pi on PATH or PI_BIN")
class LazyCapabilitiesPiTest(unittest.TestCase):
    def test_next_request_resume_new_session_and_explicit_skill(self):
        requests = []

        class FakeApi(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(request)
                user = next(m for m in reversed(request["messages"]) if m["role"] == "user")
                user_text = json.dumps(user["content"])
                tool_names = {t["function"]["name"] for t in request.get("tools", [])}
                calls = []
                if "ENABLE_FIXTURE" in user_text:
                    if "codemode" not in tool_names:
                        latest_user = max(i for i, m in enumerate(request["messages"]) if m["role"] == "user")
                        attempted = any(m.get("role") == "tool" for m in request["messages"][latest_user + 1:])
                        if not attempted:
                            calls = [("codemode_enable", {}), ("wiki_enable", {}), ("browser_enable", {})]
                    elif not any(m.get("role") == "tool" and "SCRIPT_OK" in str(m.get("content")) for m in request["messages"]):
                        calls = [("codemode", {"code": 'return "SCRIPT_OK";'})]
                delta = {"role": "assistant"}
                if calls:
                    delta["tool_calls"] = [{"index": i, "id": f"call-{len(requests)}-{i}", "type": "function",
                                            "function": {"name": name, "arguments": json.dumps(args)}}
                                           for i, (name, args) in enumerate(calls)]
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
            with tempfile.TemporaryDirectory(prefix="pi-lazy-real.") as temp:
                root = Path(temp)
                home = root / "home"
                agent = home / ".pi/agent"
                (agent / "skills").mkdir(parents=True)
                wiki_root = root / "wiki-skills"
                skill = wiki_root / "wiki-fixture"
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text(
                    "---\nname: wiki-fixture\ndescription: WIKI_CATALOG_MARKER\n---\nFULL_SKILL_BODY_MARKER\n")
                (agent / "skills/wiki-fixture").symlink_to(skill)
                browser = home / ".agents/skills/terminal-browser"
                browser.mkdir(parents=True)
                (browser / "SKILL.md").write_text(
                    "---\nname: terminal-browser\ndescription: BROWSER_CATALOG_MARKER\n---\nBROWSER_FULL_BODY_MARKER\n")
                other = agent / "skills/unrelated"
                other.mkdir()
                (other / "SKILL.md").write_text("---\nname: unrelated\ndescription: UNRELATED_CATALOG_MARKER\n---\nKEEP\n")
                instructions = root / "wiki-agent.md"
                instructions.write_text("WIKI_INSTRUCTIONS_MARKER\n")
                (agent / "AGENTS.md").symlink_to(instructions)
                (agent / "settings.json").write_text(json.dumps({
                    "defaultProvider": "fixture", "defaultModel": "fixture", "cacheWarming": "off",
                    "defaultProjectTrust": "never", "codemode": {"mode": "on"},
                }))
                (agent / "models.json").write_text(json.dumps({"providers": {"fixture": {
                    "baseUrl": f"http://127.0.0.1:{server.server_port}/v1", "api": "openai-completions",
                    "apiKey": "dummy", "models": [{"id": "fixture", "contextWindow": 128000, "maxTokens": 1024}],
                }}}))
                wrapper = root / "fixture.ts"
                wrapper.write_text('''import { Type } from "@earendil-works/pi-ai";
import { buildSessionContext, formatSkillsForPrompt, getAgentDir, loadSkillsFromDir } from "@earendil-works/pi-coding-agent";
import { registerLazyCapabilities } from ''' + json.dumps(str(EXTENSION / "core.mjs")) + ''';
export default function(pi) {
  pi.registerTool({ name: "wiki_ask", label: "Wiki", description: "WIKI_TOOL_DESCRIPTION_MARKER",
    parameters: Type.Object({}), execute: async () => { throw new Error("Must not query wiki"); } });
  registerLazyCapabilities(pi, { parameters: Type.Object({}), buildSessionContext, formatSkillsForPrompt,
    agentDir: getAgentDir(), loadSkillsFromDir,
    wikiRoots: [''' + json.dumps(str(wiki_root)) + '''], wikiInstructionsPath: ''' + json.dumps(str(instructions)) + ''' });
}
''')
                env = {key: value for key, value in os.environ.items() if not key.startswith("PI_")}
                env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"), PI_CODING_AGENT_DIR=str(agent),
                           PI_SKIP_VERSION_CHECK="1", PI_TELEMETRY="0")
                session = root / "session.jsonl"

                def run(prompt, *, resume=False, extra=(), extension=wrapper):
                    args = [PI, "--offline", "--print", "--no-extensions", "-e", "builtin:codemode", "-e", str(extension),
                            "--no-approve", "--no-prompt-templates"]
                    args += ["--session", str(session)] if resume or not session.exists() else ["--no-session"]
                    result = subprocess.run(args + list(extra) + ["--", prompt], cwd=root, env=env,
                                            capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                    self.assertIn("FIXTURE_DONE", result.stdout, result.stderr)
                    self.assertNotIn("Extension error", result.stderr)
                    return requests[-1]

                def names(request):
                    return {tool["function"]["name"] for tool in request.get("tools", [])}

                run("ENABLE_FIXTURE")
                self.assertEqual(len(requests), 3)
                first, second, third = requests
                self.assertTrue({"codemode_enable", "wiki_enable", "browser_enable"} <= names(first))
                self.assertFalse({"codemode", "wiki_ask"} & names(first))
                self.assertIn("UNRELATED_CATALOG_MARKER", json.dumps(first))
                for marker in ("WIKI_CATALOG_MARKER", "WIKI_INSTRUCTIONS_MARKER", "WIKI_TOOL_DESCRIPTION_MARKER",
                               "BROWSER_CATALOG_MARKER", "BROWSER_FULL_BODY_MARKER", str(browser / "SKILL.md")):
                    self.assertNotIn(marker, json.dumps(first))
                self.assertTrue({"codemode", "wiki_ask"} <= names(second))
                self.assertIn("WIKI_CATALOG_MARKER", json.dumps(second))
                self.assertIn("WIKI_INSTRUCTIONS_MARKER", json.dumps(second))
                self.assertNotIn("FULL_SKILL_BODY_MARKER", json.dumps(second))
                self.assertIn("BROWSER_CATALOG_MARKER", json.dumps(second))
                self.assertIn(str(browser / "SKILL.md"), json.dumps(second))
                self.assertNotIn("BROWSER_FULL_BODY_MARKER", json.dumps(second))
                self.assertIn("SCRIPT_OK", json.dumps(third))
                resumed = run("RESUME_FIXTURE", resume=True)
                self.assertTrue({"codemode", "wiki_ask"} <= names(resumed))
                self.assertIn("WIKI_CATALOG_MARKER", json.dumps(resumed))
                self.assertIn("BROWSER_CATALOG_MARKER", json.dumps(resumed))
                fresh = run("FRESH_FIXTURE")
                self.assertFalse({"codemode", "wiki_ask"} & names(fresh))
                self.assertNotIn("WIKI_CATALOG_MARKER", json.dumps(fresh))
                self.assertNotIn("BROWSER_CATALOG_MARKER", json.dumps(fresh))
                self.assertNotIn(str(browser / "SKILL.md"), json.dumps(fresh))
                browser_explicit = run("/skill:terminal-browser EXPLICIT_FIXTURE")
                self.assertIn("BROWSER_FULL_BODY_MARKER", json.dumps(browser_explicit))
                self.assertIn("BROWSER_CATALOG_MARKER", json.dumps(browser_explicit))
                self.assertNotIn("wiki_ask", names(browser_explicit))
                explicit = run("/skill:wiki-fixture EXPLICIT_FIXTURE")
                self.assertIn("wiki_ask", names(explicit))
                self.assertIn("FULL_SKILL_BODY_MARKER", json.dumps(explicit))
                self.assertIn("WIKI_CATALOG_MARKER", json.dumps(explicit))
                blocked = run("ENABLE_FIXTURE", extra=["--exclude-tools", "codemode"])
                self.assertNotIn("codemode", names(blocked))
                self.assertRegex(json.dumps(blocked), r"Codemode is unavailable|Activation blocked")

                # Actual pi-wiki layout: no wiki_ask, no native wiki resources at startup.
                (agent / "wiki-skills").mkdir()
                (agent / "skills/wiki-fixture").rename(agent / "wiki-skills/wiki-fixture")
                (agent / "AGENTS.md").rename(agent / "wiki-instructions.md")
                subprocess.run(["bash", str(INSTALLER),
                                str(EXTENSION)], env=env, check=True)
                session.unlink()
                offset = len(requests)
                run("ENABLE_FIXTURE", extension=EXTENSION / "index.ts")
                lazy_first, lazy_second, _ = requests[offset:]
                self.assertNotIn("WIKI_CATALOG_MARKER", json.dumps(lazy_first))
                self.assertNotIn("WIKI_INSTRUCTIONS_MARKER", json.dumps(lazy_first))
                self.assertNotIn("BROWSER_CATALOG_MARKER", json.dumps(lazy_first))
                self.assertNotIn(str(browser / "SKILL.md"), json.dumps(lazy_first))
                self.assertIn("BROWSER_CATALOG_MARKER", json.dumps(lazy_second))
                self.assertNotIn("BROWSER_FULL_BODY_MARKER", json.dumps(lazy_second))
                self.assertIn("WIKI_CATALOG_MARKER", json.dumps(lazy_second))
                self.assertIn("WIKI_INSTRUCTIONS_MARKER", json.dumps(lazy_second))
                self.assertNotIn("FULL_SKILL_BODY_MARKER", json.dumps(lazy_second))
                self.assertNotIn("wiki_ask", names(lazy_second))
                resumed = run("RESUME_FIXTURE", resume=True, extension=EXTENSION / "index.ts")
                self.assertIn("WIKI_CATALOG_MARKER", json.dumps(resumed))
                self.assertIn("BROWSER_CATALOG_MARKER", json.dumps(resumed))
                fresh = run("FRESH_FIXTURE", extension=EXTENSION / "index.ts")
                self.assertNotIn("WIKI_CATALOG_MARKER", json.dumps(fresh))
                self.assertNotIn("WIKI_INSTRUCTIONS_MARKER", json.dumps(fresh))
                self.assertNotIn("BROWSER_CATALOG_MARKER", json.dumps(fresh))
                shutil.rmtree(browser)
                missing = run("ENABLE_FIXTURE", extension=EXTENSION / "index.ts")
                self.assertRegex(json.dumps(missing), r"terminal-browser skill is not installed")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
