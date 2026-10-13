#!/usr/bin/env python3
"""Portable generator tests; actual .lnk creation requires Windows + WSL."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("shortcuts", Path(__file__).with_name("windows-shortcuts.py"))
shortcuts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shortcuts)


class WindowsShortcutsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        icon = self.root / "icon.ico"
        icon.write_bytes(b"icon fixture")
        launcher = self.root / "profile/bin/gcnt-windows-launch"
        launcher.parent.mkdir(parents=True)
        launcher.touch()
        self.manifest = dict(username="user", launcher=str(launcher), applications=[
            dict(id="gui-container-firefox", name="GCNT · Firefox", icon=str(icon)),
            dict(id="gui-container-profile-work", name="Work <&> 'quoted' / профили", icon=str(icon)),
        ])

    def test_payload_and_stable_launcher(self):
        result = shortcuts.request(self.manifest, "Ubuntu's WSL")
        self.assertEqual(len(result["applications"]), 2)
        for app in result["applications"]:
            self.assertEqual(base64.b64decode(app["icon"]), b"icon fixture")
            encoded = app["arguments"].split()[-1]
            runtime = base64.b64decode(encoded).decode("utf-16-le")
            self.assertIn("'Ubuntu''s WSL'", runtime)
            self.assertIn(shortcuts.ps_string(self.manifest["launcher"]), runtime)
            self.assertIn("'" + app["id"] + "'", runtime)
            self.assertNotIn("/nix/store/", runtime)
            self.assertNotIn("bash", runtime)
            self.assertIn("MessageBox", runtime)
            self.assertIn("WindowStyle Hidden", app["arguments"])
            self.assertNotRegex(app["name"], r'[<>:"/\\|?*]')
        self.assertIn("[work]", result["applications"][1]["name"])
        self.assertNotEqual(result["scope"], shortcuts.request(self.manifest, "Debian")["scope"])

    def test_rejects_application_id_injection(self):
        self.manifest["applications"][0]["id"] = "firefox; touch /tmp/oops"
        with self.assertRaises(ValueError):
            shortcuts.request(self.manifest, "Ubuntu")

    def test_missing_wsl_is_refused(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ValueError, "inside"):
            shortcuts.main(["unused.json"])

    def test_generation_uses_file_not_long_command_line(self):
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps(self.manifest))
        captures = []

        def run(command, check):
            self.assertTrue(check)
            self.assertIn("-File", command)
            self.assertNotIn("-EncodedCommand", command)
            script = Path(command[-1])
            captures.append(script.read_text())
            # Simulates Windows reading the script during the interop call.
            self.assertIn("GetFolderPath('Desktop')", captures[-1])
            self.assertIn("Refusing to overwrite an unmanaged shortcut", captures[-1])
            self.assertIn("WScript.Shell", captures[-1])
            self.assertIn("WriteAllBytes", captures[-1])
            return subprocess.CompletedProcess(command, 0)

        with patch.dict(os.environ, {"WSL_DISTRO_NAME": "Ubuntu"}), \
                patch.object(shortcuts.shutil, "which", side_effect=lambda command: "/bin/" + command), \
                patch.object(shortcuts.subprocess, "check_output", side_effect=lambda command, text: command[-1] + "\n"), \
                patch.object(shortcuts.subprocess, "run", side_effect=run):
            shortcuts.main([str(manifest)])
        self.assertEqual(len(captures), 1)
        self.assertNotIn("__PAYLOAD__", captures[0])

    def test_profile_must_be_activated(self):
        manifest = self.root / "manifest.json"
        self.manifest["launcher"] = str(self.root / "not-installed")
        manifest.write_text(json.dumps(self.manifest))
        with patch.dict(os.environ, {"WSL_DISTRO_NAME": "Ubuntu"}), \
                patch.object(shortcuts.shutil, "which", return_value="tool"), \
                self.assertRaisesRegex(ValueError, "activate"):
            shortcuts.main([str(manifest)])


if __name__ == "__main__":
    unittest.main()
