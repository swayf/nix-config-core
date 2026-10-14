"""python3 modules/programs/pi/tests/test-install-ask-user-question.py"""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "install-ask-user-question.sh"


class InstallAskUserQuestionTest(unittest.TestCase):
    def install(self, home, package):
        subprocess.run(
            ["bash", str(SCRIPT), str(package)],
            env={**os.environ, "HOME": str(home)},
            check=True,
            capture_output=True,
        )

    def test_all_profiles_idempotence_and_upgrade(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home with spaces"
            custom = home / ".pi/profiles/custom profile"
            custom.mkdir(parents=True)
            settings = custom / "settings.json"
            original = '{"defaultModel":"my-model","packages":["./local"]}\n'
            settings.write_text(original)
            (home / ".pi/profiles/not-a-directory").write_text("ignored")
            packages = [root / "package-v1", root / "package-v2"]
            for package in packages:
                package.mkdir()
                (package / "index.ts").write_text("export default function () {}")
            profiles = [home / ".pi/agent"] + [
                home / ".pi/profiles" / name
                for name in ("general", "code", "wiki", "custom profile")
            ]
            for package in (packages[0], packages[0], packages[1]):
                self.install(home, package)
                for profile in profiles:
                    target = profile / "extensions/rpiv-ask-user-question"
                    self.assertTrue(target.is_symlink())
                    self.assertEqual(target.resolve(), package)
                self.assertEqual(settings.read_text(), original)

    def test_preserves_local_replacements_and_repairs_dangling_link(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            package = root / "package"
            package.mkdir()
            targets = []
            for profile in ("code", "wiki", "custom"):
                target = home / ".pi/profiles" / profile / "extensions/rpiv-ask-user-question"
                target.parent.mkdir(parents=True)
                targets.append(target)
            targets[0].write_text("local extension")
            targets[1].mkdir()
            (targets[1] / "index.ts").write_text("local directory")
            targets[2].symlink_to(root / "removed-package")
            self.install(home, package)
            self.assertEqual(targets[0].read_text(), "local extension")
            self.assertFalse(targets[1].is_symlink())
            self.assertEqual((targets[1] / "index.ts").read_text(), "local directory")
            self.assertEqual(targets[2].resolve(), package)


if __name__ == "__main__":
    unittest.main()
