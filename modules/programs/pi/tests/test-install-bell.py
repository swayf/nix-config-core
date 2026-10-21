"""python3 modules/programs/pi/tests/test-install-bell.py"""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "install-bell.sh"


class InstallBellTest(unittest.TestCase):
    def install(self, home, extension):
        subprocess.run(
            ["bash", str(SCRIPT), str(extension)],
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
            original = '{"defaultModel":"my-model","extensions":["./local.ts"]}\n'
            settings.write_text(original)
            (home / ".pi/profiles/not-a-directory").write_text("ignored")
            extensions = [root / "bell-v1.ts", root / "bell-v2.ts"]
            for extension in extensions:
                extension.write_text("export default function () {}")
            profiles = [home / ".pi/agent"] + [
                home / ".pi/profiles" / name
                for name in ("general", "code", "wiki", "custom profile")
            ]
            for extension in (extensions[0], extensions[0], extensions[1]):
                self.install(home, extension)
                for profile in profiles:
                    target = profile / "extensions/bell.ts"
                    self.assertTrue(target.is_symlink())
                    self.assertEqual(target.resolve(), extension)
                self.assertEqual(settings.read_text(), original)
            new_profile = home / ".pi/profiles/new profile"
            new_profile.mkdir()
            self.install(home, extensions[1])
            self.assertEqual((new_profile / "extensions/bell.ts").resolve(), extensions[1])

    def test_preserves_local_replacements_and_repairs_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            extension = root / "bell.ts"
            extension.write_text("export default function () {}")
            targets = []
            for profile in ("code", "wiki", "custom", "directory-link"):
                target = home / ".pi/profiles" / profile / "extensions/bell.ts"
                target.parent.mkdir(parents=True)
                targets.append(target)
            targets[0].write_text("local extension")
            targets[1].mkdir()
            (targets[1] / "index.ts").write_text("local directory")
            targets[2].symlink_to(root / "removed-extension")
            directory = root / "old-directory"
            directory.mkdir()
            targets[3].symlink_to(directory)
            self.install(home, extension)
            self.assertEqual(targets[0].read_text(), "local extension")
            self.assertFalse(targets[1].is_symlink())
            self.assertEqual((targets[1] / "index.ts").read_text(), "local directory")
            for target in targets[2:]:
                self.assertEqual(target.resolve(), extension)
            self.assertEqual(list(directory.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
