"""Exercise the real shell preflight and image entrypoint without a container."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent


class ProcessPreflightTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.cgroup = self.base / "cgroup"
        self.cgroup.mkdir()
        self.readings("100", "2048")
        self.helper = ROOT / "process-preflight.sh"

    def readings(self, current, maximum):
        for name, value in (("pids.current", current), ("pids.max", maximum)):
            target = self.cgroup / name
            if value is None:
                target.unlink(missing_ok=True)
            else:
                target.write_text(value + "\n")

    def check(self, minimum="256"):
        return subprocess.run(
            ["bash", "-euo", "pipefail", "-c",
             'source "$1"; agents_process_preflight "$2" "$3"',
             "preflight-test", str(self.helper), minimum, str(self.cgroup)],
            capture_output=True, text=True, timeout=5,
        )

    def test_healthy_is_silent(self):
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout + result.stderr, "")

    def test_exact_threshold_is_allowed(self):
        self.readings("1792", "2048")
        self.assertEqual(self.check().returncode, 0)

    def test_low_headroom_refuses_with_values(self):
        self.readings("2032", "2048")
        result = self.check()
        self.assertEqual(result.returncode, 75)
        for text in ("current=2032", "max=2048", "free=16", "minimum=256",
                     "No process was killed", "agents rebuild"):
            self.assertIn(text, result.stderr)

    def test_unlimited_is_allowed(self):
        self.readings("2032", "max")
        self.assertEqual(self.check().returncode, 0)

    def test_configured_minimum(self):
        self.readings("2032", "2048")
        self.assertEqual(self.check("16").returncode, 0)
        self.assertEqual(self.check("17").returncode, 75)

    def test_invalid_values_refuse(self):
        for current, maximum in (("", "2048"), ("-1", "2048"), ("01", "2048"),
                                 ("garbage", "2048"), ("100", ""), ("100", "0"),
                                 ("100", "-1"), ("100", "2048 extra"),
                                 ("100", "9999999999999999999"),
                                 ("9999999999999999999", "max")):
            with self.subTest(current=current, maximum=maximum):
                self.readings(current, maximum)
                result = self.check()
                self.assertEqual(result.returncode, 75, result.stderr)
                self.assertIn("cannot verify process headroom", result.stderr)

    def test_missing_values_refuse(self):
        for current, maximum in ((None, "2048"), ("100", None), (None, None)):
            with self.subTest(current=current, maximum=maximum):
                self.readings(current, maximum)
                result = self.check()
                self.assertEqual(result.returncode, 75)
                self.assertIn("cannot verify process headroom", result.stderr)

    def test_invalid_minimum_refuses(self):
        for minimum in ("0", "-1", "", "garbage", "01", "9999999999999999999"):
            with self.subTest(minimum=minimum):
                result = self.check(minimum)
                self.assertEqual(result.returncode, 75)
                self.assertIn("invalid minimumFreePids", result.stderr)

    def test_current_above_limit_refuses(self):
        self.readings("2049", "2048")
        result = self.check()
        self.assertEqual(result.returncode, 75)
        self.assertIn("free=-1", result.stderr)

    def test_helper_invokes_no_external_command(self):
        env = dict(os.environ, PATH="/nonexistent")
        result = subprocess.run(
            ["/bin/bash", "-euo", "pipefail", "-c",
             'source "$1"; agents_process_preflight 256 "$2"',
             "preflight-test", str(self.helper), str(self.cgroup)],
            env=env, capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def entrypoint(self, current):
        self.readings(current, "2048")
        # Run the actual Nix shell fragment. Redirect only store dependencies
        # and the cgroup filesystem, not its order or admission logic.
        source = (ROOT / "image.nix").read_text()
        fragment = source.split('entrypoint = pkgs.writeShellScript "agents-entrypoint" \'\'\n', 1)[1].split("\n  '';", 1)[0]
        dispatcher = self.base / "secret-dispatcher"
        called = self.base / "called"
        dispatcher.write_text('#!/bin/bash\nprintf "called\\n" > "$TEST_CALLED"\n')
        dispatcher.chmod(0o755)
        fragment = fragment.replace("${pkgs.python3}/bin/python3", str(dispatcher)).replace(
            "${./secret-environment.py}", "secret-helper").replace(
            "${sessionEntrypoint}", "session-helper").replace(
            "${./process-preflight.sh}", str(self.helper)).replace(
            "${toString minimumFreePids}", "256")
        fragment = fragment.replace("agents_process_preflight 256",
                                    f'agents_process_preflight 256 "{self.cgroup}"')
        result = subprocess.run(
            ["bash", "-euo", "pipefail", "-c", fragment, "entrypoint-test", "true"],
            env=dict(os.environ, TEST_CALLED=str(called)), capture_output=True,
            text=True, timeout=5,
        )
        return result, called

    def session_receipt(self, *args):
        source = (ROOT / "image.nix").read_text()
        fragment = source.split('sessionEntrypoint = pkgs.writeShellScript "agents-session" \'\'\n', 1)[1].split("\n  '';", 1)[0]
        receipt = self.base / "workspace-ready"
        fragment = fragment.replace("${software.homeInit}", ":").replace(
            "/tmp/agents-workspace-ready", str(receipt)).replace('exec "$@"', 'exit 0')
        result = subprocess.run(
            ["bash", "-euo", "pipefail", "-c", fragment, "session-test", *args],
            env=dict(os.environ, HOME=str(self.base / "home"),
                     XDG_RUNTIME_DIR=str(self.base / "runtime")),
            capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return receipt

    def test_initial_lifetime_command_publishes_ready_receipt(self):
        self.assertTrue(self.session_receipt("/bin/sleep", "infinity").exists())

    def test_operator_command_does_not_publish_startup_receipt(self):
        self.assertFalse(self.session_receipt("true").exists())

    def test_entrypoint_refuses_before_secret_dispatch(self):
        result, called = self.entrypoint("2032")
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertFalse(called.exists())

    def test_healthy_entrypoint_dispatches(self):
        result, called = self.entrypoint("100")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(called.exists())


if __name__ == "__main__":
    unittest.main()
