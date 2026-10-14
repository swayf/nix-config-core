"""python3 modules/programs/pi/tests/test-shared-models.py"""

from pathlib import Path
import importlib.util
import json
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "shared_models", Path(__file__).parents[1] / "shared-models.py"
)
shared_models = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared_models)


def put(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


class SharedModelsTest(unittest.TestCase):
    def test_shared_files_and_independent_defaults(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            common = home / ".pi/agent"
            profiles = [home / ".pi/profiles" / name for name in ("general", "code", "wiki", "custom")]
            for profile in profiles:
                put(profile / "settings.json", {"defaultModel": profile.name, "defaultProvider": "example"})
            for _ in range(3):
                shared_models.share(home)
                for profile in profiles:
                    self.assertEqual(json.loads((profile / "settings.json").read_text())["defaultModel"], profile.name)
                    for name in ("models.json", "auth.json"):
                        self.assertEqual((profile / name).resolve(), common / name)
                        self.assertEqual((common / name).stat().st_mode & 0o777, 0o600)
            # /login and custom model edits through any profile affect all others.
            (profiles[1] / "auth.json").write_text('{"example":{"type":"api_key","key":"fake"}}')
            (profiles[2] / "models.json").write_text('{"providers":{"example":{"models":[{"id":"test"}]}}}')
            shared_models.share(home)
            for profile in profiles:
                self.assertEqual(json.loads((profile / "auth.json").read_text())["example"]["key"], "fake")
                self.assertIn("example", json.loads((profile / "models.json").read_text())["providers"])
                self.assertEqual(list(profile.glob("*.before-shared*")), [])

    def test_migration_and_backups(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            common = home / ".pi/agent"
            profile = home / ".pi/profiles/code"
            common_auth = {"a": {"type": "api_key", "key": "common"}}
            local_auth = {"a": {"type": "api_key", "key": "local"}, "b": {"type": "api_key", "key": "new"}}
            put(common / "auth.json", common_auth)
            put(profile / "auth.json", local_auth)
            put(common / "models.json", {"providers": {"a": {"baseUrl": "common"}}})
            put(profile / "models.json", {"providers": {"a": {"baseUrl": "local"}, "b": {"models": [{"id": "test"}]}}})
            put(profile / "settings.json", {"defaultModel": "test"})
            shared_models.share(home)
            for _ in range(2):
                shared_models.share(home)
            auth = json.loads((common / "auth.json").read_text())
            self.assertEqual(auth, {**local_auth, **common_auth})
            models = json.loads((common / "models.json").read_text())["providers"]
            self.assertEqual(models["a"]["baseUrl"], "common")
            self.assertEqual(models["b"]["models"], [{"id": "test"}])
            self.assertEqual(json.loads((profile / "auth.json.before-shared").read_text()), local_auth)
            self.assertEqual((profile / "auth.json.before-shared").stat().st_mode & 0o777, 0o600)
            self.assertEqual(len(list(profile.glob("*.before-shared*"))), 2)

    def test_user_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            common = home / ".pi/agent"
            profile = home / ".pi/profiles/wiki"
            external = home / "external.json"
            put(external, {"providers": {"custom": {"models": [{"id": "external"}]}}})
            common.mkdir(parents=True)
            profile.mkdir(parents=True)
            (common / "models.json").symlink_to(external)
            (profile / "models.json").symlink_to(external)
            shared_models.share(home)
            self.assertTrue((common / "models.json").is_symlink())
            self.assertEqual((profile / "models.json").resolve(), external)
            self.assertIn("custom", json.loads(external.read_text())["providers"])

    def test_invalid_profile_file_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            path = home / ".pi/profiles/code/models.json"
            path.parent.mkdir(parents=True)
            path.write_text("not JSON")
            with self.assertRaises(ValueError):
                shared_models.share(home)
            self.assertEqual(path.read_text(), "not JSON")
            self.assertFalse((home / ".pi/agent/models.json").exists())


if __name__ == "__main__":
    unittest.main()
