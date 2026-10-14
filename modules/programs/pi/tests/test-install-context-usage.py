"""Synthetic all-profile installer contract; no Pi, model or network needed."""
from pathlib import Path
import json
import os
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "install-context-usage.sh"


class InstallContextUsageTest(unittest.TestCase):
    def install(self, home, package, check=True):
        return subprocess.run(
            ["bash", str(SCRIPT), str(package)],
            env={**os.environ, "HOME": str(home)}, check=check,
            capture_output=True, text=True,
        )

    def package(self, root, name):
        package = root / name
        (package / "src").mkdir(parents=True)
        (package / "package.json").write_text(json.dumps({
            "name": "pi-context-usage", "pi": {"extensions": ["./src/index.ts"]},
        }))
        (package / "src/index.ts").write_text("export default function () {}")
        return package

    def test_all_profiles_settings_idempotence_upgrade_and_new_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home with spaces"
            custom = home / ".pi/profiles/custom profile"
            custom.mkdir(parents=True)
            settings = custom / "settings.json"
            original = '{"defaultModel":"my-model","packages":["./local"]}\n'
            settings.write_text(original)
            other = custom / "extensions/other.ts"
            other.parent.mkdir()
            other.write_text("unrelated extension")
            (home / ".pi/profiles/not-a-directory").write_text("ignored")
            packages = [self.package(root, name) for name in ("package v1", "package v2")]
            for package in (packages[0], packages[0], packages[1]):
                self.install(home, package)
                for profile in [home / ".pi/agent"] + [home / ".pi/profiles" / name
                                for name in ("general", "code", "wiki", "custom profile")]:
                    target = profile / "extensions/pi-context-usage"
                    self.assertTrue(target.is_symlink())
                    self.assertEqual(target.resolve(), package)
                self.assertEqual(settings.read_text(), original)
                self.assertEqual(other.read_text(), "unrelated extension")
            new = home / ".pi/profiles/new profile"
            new.mkdir()
            self.install(home, package)
            self.assertEqual((new / "extensions/pi-context-usage").resolve(), package)

    def test_preserves_local_overrides_and_repairs_links(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            package = self.package(root, "package")
            targets = []
            for profile in ("code", "wiki", "custom", "directory-link"):
                target = home / ".pi/profiles" / profile / "extensions/pi-context-usage"
                target.parent.mkdir(parents=True)
                targets.append(target)
            targets[0].write_text("local file")
            targets[1].mkdir()
            (targets[1] / "index.ts").write_text("local extension")
            targets[2].symlink_to(root / "missing")
            old = root / "old directory"
            old.mkdir()
            targets[3].symlink_to(old)
            self.install(home, package)
            self.assertEqual(targets[0].read_text(), "local file")
            self.assertEqual((targets[1] / "index.ts").read_text(), "local extension")
            for target in targets[2:]:
                self.assertEqual(target.resolve(), package)
            self.assertEqual(list(old.iterdir()), [])

    def test_missing_source_does_not_modify_home(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for package in (root / "missing", root / "incomplete"):
                if package.name == "incomplete":
                    package.mkdir()
                    (package / "package.json").write_text("{}")
                result = self.install(root / "home", package, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("missing pi-context-usage package", result.stderr)
                self.assertFalse((root / "home").exists())


if __name__ == "__main__":
    unittest.main()
