#!/usr/bin/env python3
"""Test an evaluated editor script with mock SOPS; never decrypt credentials."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(sys.argv.pop(1)).read_text()
# Remove only writeShellApplication's runtime PATH so SOPS resolves to our mock.
SCRIPT = "\n".join(line for line in SCRIPT.splitlines() if not line.startswith("export PATH="))


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="core-secret-editor-")
        self.root = Path(self.tmp.name)
        (self.root / "fixture").mkdir()
        for group in ("primary", "secondary"):
            (self.root / "fixture" / f"{group}.yaml").write_text("synthetic ciphertext\n")
        self.script = self.root / "editor.sh"
        self.script.write_text(SCRIPT)
        self.log = self.root / "trace"
        mock = self.root / "sops"
        mock.write_text(f"#!{sys.executable}\n" + '''import json, os, sys
with open(os.environ["MOCK_TRACE"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
args = sys.argv[1:]
if args[0] == "decrypt":
    if os.environ.get("MOCK_DENY") == "1": sys.exit(1)
    if "--extract" in args:
        sys.exit(0 if args[2] in ['["node"]', '["api"]', '["other"]'] else 1)
if args[0] == "edit": sys.exit(int(os.environ.get("MOCK_EDIT_STATUS", "200")))
''')
        mock.chmod(0o755)
        self.env = {
            "PATH": str(self.root) + ":" + os.environ["PATH"],
            "HOME": str(self.root), "MOCK_TRACE": str(self.log),
        }

    def tearDown(self):
        self.tmp.cleanup()

    def run_editor(self, *args, success=True):
        result = subprocess.run(["bash", str(self.script), *args], cwd=self.root,
                                env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, success, result.stderr)
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_parser(self):
        subprocess.run(["bash", "-n", str(self.script)], check=True)

    def test_default_groups_seed_only_missing_and_continue_after_200(self):
        trace = self.run_editor()
        seeded = [row for row in trace if row[0] == "set"]
        self.assertEqual({row[2] for row in seeded}, {'["missing"]', '["nested"]["token"]'})
        self.assertTrue(all(row[3] == '"CHANGEME"' for row in seeded))
        self.assertEqual([row[1] for row in trace if row[0] == "edit"],
                         ["fixture/primary.yaml", "fixture/secondary.yaml"])

    def test_group_alias_uses_mutable_checkout_path(self):
        trace = self.run_editor("secondary")
        self.assertEqual(trace[-1], ["edit", "fixture/secondary.yaml"])
        self.assertFalse(any(row[0] == "set" for row in trace))

    def test_explicit_path(self):
        trace = self.run_editor("fixture/secondary.yaml")
        self.assertEqual(trace[-1], ["edit", "fixture/secondary.yaml"])

    def test_decrypt_failure_never_writes(self):
        self.env["MOCK_DENY"] = "1"
        trace = self.run_editor("primary", success=False)
        self.assertEqual(trace, [["decrypt", "fixture/primary.yaml"]])

    def test_missing_file_never_calls_sops(self):
        (self.root / "fixture/primary.yaml").unlink()
        self.assertEqual(self.run_editor("primary", success=False), [])

    def test_unknown_file_never_calls_sops(self):
        self.assertEqual(self.run_editor("unknown", success=False), [])

    def test_editor_failure_is_propagated(self):
        self.env["MOCK_EDIT_STATUS"] = "7"
        trace = self.run_editor("secondary", success=False)
        self.assertEqual(trace[-1], ["edit", "fixture/secondary.yaml"])


if __name__ == "__main__":
    unittest.main()
