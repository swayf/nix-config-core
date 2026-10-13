#!/usr/bin/env python3
"""Probe lifecycle mocks only: never contact WARP or register an account."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("probe", ROOT / "scripts/xray-warp-probe.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


class ProbeTests(unittest.TestCase):
    def test_invalid_input_never_starts_tools(self):
        renderer = SimpleNamespace(validate_warp=Mock(side_effect=ValueError("invalid")))
        with patch.object(P.subprocess, "run") as run, patch.object(P.subprocess, "Popen") as popen:
            with self.assertRaises(ValueError):
                P.probe({}, renderer)
            run.assert_not_called()
            popen.assert_not_called()

    def test_sanitized_environment_and_cleanup(self):
        process = Mock()
        process.poll.return_value = None
        commands = []
        def run(command, **kwargs):
            commands.append((command, kwargs))
            if command[0] == "xray":
                config = Path(command[-1])
                self.assertEqual(config.stat().st_mode & 0o777, 0o600)
                self.assertEqual(json.loads(config.read_text())["inbounds"][0]["listen"], "127.0.0.1")
                return SimpleNamespace(returncode=0)
            return SimpleNamespace(returncode=0, stdout=b"ip=192.0.2.1\nwarp=on\nloc=TEST\ncolo=TEST\n")
        with patch.dict(os.environ, {"SOPS_AGE_KEY_FILE": "do-not-inherit", "HTTPS_PROXY": "do-not-inherit"}), \
             patch.object(P.subprocess, "run", side_effect=run), \
             patch.object(P.subprocess, "Popen", return_value=process) as popen, \
             patch.object(P.socket, "create_connection", return_value=Mock(__enter__=Mock(), __exit__=Mock())):
            result = P.probe({}, SimpleNamespace(validate_warp=Mock()))
        self.assertEqual(result["warp"], "on")
        for _, kwargs in commands + [popen.call_args]:
            self.assertNotIn("SOPS_AGE_KEY_FILE", kwargs["env"])
            self.assertNotIn("HTTPS_PROXY", kwargs["env"])
        self.assertIn("--disable", commands[1][0])
        self.assertFalse(Path(commands[0][0][-1]).exists())
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=3)

    def test_rejected_xray_does_not_start_child(self):
        with patch.object(P.subprocess, "run", return_value=SimpleNamespace(returncode=1)), \
             patch.object(P.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(ValueError, "Xray rejected"):
                P.probe({}, SimpleNamespace(validate_warp=Mock()))
            popen.assert_not_called()

    def test_failed_curl_still_stops_process(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("xray", 3), None]
        with patch.object(P.subprocess, "run", side_effect=[SimpleNamespace(returncode=0), SimpleNamespace(returncode=1)]), \
             patch.object(P.subprocess, "Popen", return_value=process), \
             patch.object(P.socket, "create_connection", return_value=Mock(__enter__=Mock(), __exit__=Mock())):
            with self.assertRaisesRegex(ValueError, "WARP probe failed"):
                P.probe({}, SimpleNamespace(validate_warp=Mock()))
        process.terminate.assert_called_once()
        process.kill.assert_called_once()

    def test_cli_redacts_invalid_material_and_limits_input(self):
        for data in (b'{"secretKey":"DO_NOT_PRINT"}', b"x" * (1024 * 1024 + 1)):
            result = subprocess.run([sys.executable, str(ROOT / "scripts/xray-warp-probe.py"),
                                     "--renderer", str(ROOT / "modules/nixos/xray-render.py")],
                                    input=data, capture_output=True, env={"PATH": ""}, timeout=10)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, b"")
            self.assertNotIn(b"DO_NOT_PRINT", result.stderr)
            self.assertIn(b"No deploy performed", result.stderr)


if __name__ == "__main__":
    unittest.main()
