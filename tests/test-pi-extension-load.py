#!/usr/bin/env python3
"""Caller-selected Pi: registration/session start only, no model requests."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
NAMES = ["bell.ts", "clear.ts", "codex-limits.ts", "compact-prompts.ts",
         "compact-tools.ts", "model-presets.ts", "statusline.ts",
         "context-handoff", "lazy-capabilities", "wiki-ask"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pi", required=True, type=Path)
    parser.add_argument("--extensions", type=Path,
                        default=ROOT / "modules/programs/pi/extensions")
    args = parser.parse_args()
    pi, extensions = args.pi.resolve(strict=True), args.extensions.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="pi-extension-native-") as temp:
        root = Path(temp)
        home = root / "home"
        home.mkdir()
        env = {"PATH": os.environ["PATH"], "HOME": str(home),
               "XDG_CONFIG_HOME": str(home / ".config"), "XDG_CACHE_HOME": str(home / ".cache"),
               "PI_CODING_AGENT_DIR": str(home / ".pi/agent"), "PI_OFFLINE": "1",
               "PI_SKIP_VERSION_CHECK": "1", "PI_TELEMETRY": "0", "LANG": "C.UTF-8"}
        probe = root / "probe.ts"
        probe.write_text('''import assert from "node:assert/strict";
export default function(pi) {
  pi.registerCommand("fixture-load", {description: "Local fixture", handler: async (_args, ctx) => {
    const commands = pi.getCommands().map(c => c.name);
    const tools = pi.getAllTools().map(t => t.name);
    for (const name of ["sound", "bell", "clear", "codex-limits", "model-presets", "wiki-ask", "handoff-context"])
      assert(commands.includes(name), name);
    for (const name of ["codemode_enable", "wiki_enable", "browser_enable", "wiki_ask", "handoff_context"])
      assert(tools.includes(name), name);
    assert(!pi.getActiveTools().includes("handoff_context"));
    assert(!pi.getActiveTools().includes("wiki_ask"));
    process.stderr.write("FIXTURE_NATIVE_LOAD=" + JSON.stringify({mode:ctx.mode, commands, tools}) + "\\n");
  }});
}
''')
        command = [str(pi), "--offline", "--mode", "json", "--print", "--no-session",
                   "--no-extensions", "--no-skills", "--no-context-files",
                   "--no-prompt-templates", "--no-approve"]
        for name in NAMES:
            command += ["-e", str(extensions / name)]
        command += ["-e", str(probe), "--", "/fixture-load"]
        result = subprocess.run(command, cwd=root, env=env, input="", text=True,
                                capture_output=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Extension error" not in result.stdout + result.stderr
        record = next(line.removeprefix("FIXTURE_NATIVE_LOAD=")
                      for line in result.stderr.splitlines() if line.startswith("FIXTURE_NATIVE_LOAD="))
        print(json.dumps({"pi": str(pi), "groups": NAMES, "registered": json.loads(record)}))


if __name__ == "__main__":
    main()
