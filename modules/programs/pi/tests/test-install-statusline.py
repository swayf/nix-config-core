"""python3 modules/programs/pi/tests/test-install-statusline.py"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "install-statusline.sh"


class InstallStatuslineTest(unittest.TestCase):
    def install(self, home, extension):
        subprocess.run(["bash", str(SCRIPT), str(extension)],
                       env={**os.environ, "HOME": str(home)}, check=True, capture_output=True)

    def test_all_profiles_idempotence_upgrade_and_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home with spaces"
            custom = home / ".pi/profiles/custom profile"
            custom.mkdir(parents=True)
            settings = custom / "settings.json"
            original = '{"defaultModel":"my-model","extensions":["-extensions/bell.ts"]}\n'
            settings.write_text(original)
            (home / ".pi/profiles/not-a-directory").write_text("ignored")
            profiles = [home / ".pi/agent"] + [home / ".pi/profiles" / name
                        for name in ("general", "code", "wiki", "custom profile")]
            for version in (1, 1, 2):
                extension = root / f"statusline-v{version}.ts"
                extension.write_text("export default function () {}")
                self.install(home, extension)
                for profile in profiles:
                    self.assertEqual((profile / "extensions/statusline.ts").resolve(), extension)
                    self.assertFalse((profile / "extensions/bell.ts").exists())
                self.assertEqual(settings.read_text(), original)
            new = home / ".pi/profiles/new profile"
            new.mkdir()
            self.install(home, extension)
            self.assertEqual((new / "extensions/statusline.ts").resolve(), extension)

    def test_preserves_local_files_directories_and_repairs_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            extension = root / "statusline.ts"
            extension.write_text("export default function () {}")
            targets = []
            for profile in ("code", "wiki", "custom", "directory-link"):
                target = home / ".pi/profiles" / profile / "extensions/statusline.ts"
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
            self.assertEqual((targets[1] / "index.ts").read_text(), "local directory")
            self.assertFalse(targets[1].is_symlink())
            for target in targets[2:]:
                self.assertEqual(target.resolve(), extension)
            self.assertEqual(list(directory.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
