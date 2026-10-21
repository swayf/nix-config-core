#!/usr/bin/env python3
"""Settings migration plus real Pi discovery check; no model/network calls."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).parents[1] / "disable-bell.py"
spec = importlib.util.spec_from_file_location("disable_bell", SOURCE)
bell = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bell)


class DisableBellTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)

    def test_every_profile_keeps_other_settings_and_links(self):
        for relative in (".pi/agent", ".pi/profiles/code", ".pi/profiles/general",
                         ".pi/profiles/wiki", ".pi/profiles/custom"):
            profile = self.home / relative
            (profile / "extensions").mkdir(parents=True)
            source = self.home / "bell.ts"
            source.write_text("// bell\n")
            (profile / "extensions/bell.ts").symlink_to(source)
            settings = {"defaultModel": "my-model", "custom": {"keep": True},
                        "extensions": ["+extensions/bell.ts", "extensions/other.ts"]}
            path = profile / "settings.json"
            original = json.dumps(settings)
            path.write_text(original)
            bell.install(self.home)
            saved = json.loads(path.read_text())
            self.assertEqual(saved["defaultModel"], settings["defaultModel"])
            self.assertEqual(saved["custom"], settings["custom"])
            self.assertEqual(saved["extensions"], ["extensions/other.ts", "-extensions/bell.ts"])
            self.assertEqual(path.with_name(path.name + ".before-disable-bell").read_text(), original)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            before = path.stat().st_mtime_ns
            bell.install(self.home)
            self.assertEqual(path.stat().st_mtime_ns, before)
            self.assertTrue((profile / "extensions/bell.ts").is_symlink())

    def test_invalid_json_and_symlinks_are_not_overwritten(self):
        profile = self.home / ".pi/agent"
        profile.mkdir(parents=True)
        path = profile / "settings.json"
        path.write_text("invalid")
        with self.assertRaises(ValueError):
            bell.install(self.home)
        self.assertEqual(path.read_text(), "invalid")
        path.unlink()
        path.symlink_to(self.home / "missing")
        bell.install(self.home)
        self.assertTrue(path.is_symlink())
        self.assertFalse(path.exists())

    @unittest.skipUnless(shutil.which("pi"), "requires Pi on PATH")
    def test_real_pi_excludes_symlinked_bell_but_keeps_state_integration(self):
        profile = self.home / ".pi/agent"
        extensions = profile / "extensions"
        extensions.mkdir(parents=True)
        source = self.home / "bell.ts"
        source.write_text('export default function(pi) { pi.registerFlag("bell-probe", {description: "bell loaded", type: "boolean"}); }\n')
        (extensions / "bell.ts").symlink_to(source)
        (extensions / "herdr-agent-state.ts").write_text('export default function(pi) { pi.registerFlag("state-probe", {description: "state loaded", type: "boolean"}); }\n')
        env = dict(os.environ, HOME=str(self.home), PI_CODING_AGENT_DIR=str(profile))
        def help_text():
            result = subprocess.run([shutil.which("pi"), "--offline", "--no-approve", "--help"],
                                    cwd=self.home, env=env, text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout
        self.assertIn("--bell-probe", help_text())
        bell.install(self.home)
        help_after = help_text()
        self.assertNotIn("--bell-probe", help_after)
        self.assertIn("--state-probe", help_after)


if __name__ == "__main__":
    unittest.main()
