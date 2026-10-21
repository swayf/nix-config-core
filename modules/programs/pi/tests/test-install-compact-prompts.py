"""All-profile installation, updates and local override preservation."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "install-compact-prompts.sh"


class InstallCompactPromptsTest(unittest.TestCase):
    def test_profiles_repeat_updates_and_local_overrides(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home with spaces"
            custom = home / ".pi/profiles/custom profile"
            custom.mkdir(parents=True)
            settings = custom / "settings.json"
            settings.write_text('{"defaultModel":"keep-me"}\n')
            (home / ".pi/profiles/not-directory").write_text("ignored")
            for version in (1, 1, 2):
                extension = root / f"compact-prompts-v{version}.ts"
                extension.write_text("export default function () {}")
                subprocess.run(["bash", str(SCRIPT), str(extension)],
                               env={**os.environ, "HOME": str(home)}, check=True, capture_output=True)
                for profile in [home / ".pi/agent"] + [home / ".pi/profiles" / name
                                for name in ("general", "code", "wiki", "custom profile")]:
                    self.assertEqual((profile / "extensions/compact-prompts.ts").resolve(), extension)
                self.assertEqual(settings.read_text(), '{"defaultModel":"keep-me"}\n')
            targets = [home / ".pi/profiles" / name / "extensions/compact-prompts.ts"
                       for name in ("code", "wiki", "custom profile")]
            for target in targets:
                target.unlink()
            targets[0].write_text("local override")
            targets[1].mkdir()
            (targets[1] / "index.ts").write_text("local directory")
            targets[2].symlink_to(root / "missing")
            new = home / ".pi/profiles/new profile"
            new.mkdir()
            subprocess.run(["bash", str(SCRIPT), str(extension)],
                           env={**os.environ, "HOME": str(home)}, check=True, capture_output=True)
            self.assertEqual(targets[0].read_text(), "local override")
            self.assertEqual((targets[1] / "index.ts").read_text(), "local directory")
            self.assertEqual(targets[2].resolve(), extension)
            self.assertEqual((new / "extensions/compact-prompts.ts").resolve(), extension)


if __name__ == "__main__":
    unittest.main()
