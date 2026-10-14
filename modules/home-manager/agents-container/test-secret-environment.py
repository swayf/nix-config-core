"""Exercise staging and the actual exec wrapper, without real keys or Podman."""
from pathlib import Path
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

HELPER = Path(__file__).with_name("secret-environment.py").resolve()
spec = importlib.util.spec_from_file_location("secret_environment", HELPER)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class SecretEnvironmentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.directory = self.base / "private"
        self.file = self.directory / "environment.json"
        self.secret = self.base / "sops-secret"
        self.secret.write_text("test-key\n")

    def stage(self, sources):
        helper.stage(json.dumps(sources), self.directory)

    def execute(self, env=None):
        return subprocess.run([
            sys.executable, str(HELPER), "exec", str(self.file), sys.executable, "-c",
            "import json, os; print(json.dumps({k: os.environ.get(k) for k in ['TEST_KEY', 'MISSING_KEY', 'PRESERVED']}))",
        ], env=dict(os.environ, **(env or {})), capture_output=True, text=True)

    def test_literal_values_no_shell_evaluation(self):
        sentinel = self.base / "must-not-exist"
        value = f"$HOME $(touch {sentinel}) `touch {sentinel}` quotes='\"'\\\nsecond line"
        self.secret.write_text(value + "\n")
        self.stage({"TEST_KEY": str(self.secret), "MISSING_KEY": None})
        result = self.execute({"MISSING_KEY": "old-value", "PRESERVED": "preserved"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "TEST_KEY": value, "MISSING_KEY": None, "PRESERVED": "preserved",
        })
        self.assertFalse(sentinel.exists())

    def test_permissions_rotation_and_no_temporary_files(self):
        sources = {"TEST_KEY": str(self.secret)}
        self.stage(sources)
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o600)
        self.secret.write_text("rotated\n")
        self.stage(sources)
        self.assertEqual(json.loads(self.execute().stdout)["TEST_KEY"], "rotated")
        self.assertEqual(list(self.directory.iterdir()), [self.file])

    def test_missing_secret_and_placeholders_clear_inherited_value(self):
        for value in (None, "", "CHANGEME"):
            with self.subTest(value=value):
                if value is not None:
                    self.secret.write_text(value + "\n")
                else:
                    self.secret.unlink(missing_ok=True)
                self.stage({"TEST_KEY": str(self.secret)})
                self.assertIsNone(json.loads(self.execute({"TEST_KEY": "stale"}).stdout)["TEST_KEY"])

    def test_invalid_sources_remove_stale_copy(self):
        for sources in ('{invalid', '[]', '{"INVALID-NAME": null}', '{"TEST_KEY": 1}'):
            with self.subTest(sources=sources):
                self.stage({"TEST_KEY": str(self.secret)})
                with self.assertRaises(helper.SecretEnvironmentError):
                    helper.stage(sources, self.directory)
                self.assertFalse(self.file.exists())

    def test_unreadable_or_nul_secret_removes_stale_copy_without_printing_value(self):
        self.stage({"TEST_KEY": str(self.secret)})
        self.secret.write_bytes(b"secret-should-not-be-logged\0")
        with self.assertRaises(helper.SecretEnvironmentError) as error:
            self.stage({"TEST_KEY": str(self.secret)})
        self.assertNotIn("secret-should-not-be-logged", str(error.exception))
        self.assertFalse(self.file.exists())
        with self.assertRaises(helper.SecretEnvironmentError):
            self.stage({"TEST_KEY": str(self.base)})
        self.assertFalse(self.file.exists())

    def test_corrupt_runtime_file_fails_closed_without_logging_contents(self):
        self.directory.mkdir()
        self.file.write_text('{"TEST_KEY": "do-not-log-this"')
        result = self.execute()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("do-not-log-this", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_missing_runtime_file_fails_closed(self):
        result = self.execute({"TEST_KEY": "stale"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("run agents up on the host", result.stderr)
        self.assertNotIn("stale", result.stderr)

    def test_command_exit_status_is_preserved(self):
        self.stage({})
        result = subprocess.run([
            sys.executable, str(HELPER), "exec", str(self.file),
            sys.executable, "-c", "import sys; sys.exit(17)",
        ], capture_output=True)
        self.assertEqual(result.returncode, 17)


if __name__ == "__main__":
    unittest.main()
