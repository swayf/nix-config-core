"""Code-only delivery, idempotence and preservation of local paths; no Pi/model."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "install-context-handoff.sh"
PACKAGE = SCRIPT.parent / "extensions/context-handoff"


class InstallContextHandoffTest(unittest.TestCase):
    def install(self, home, package=PACKAGE, cwd=None):
        return subprocess.run(["bash", str(SCRIPT), str(package)], env={**os.environ, "HOME": str(home)},
                              cwd=cwd, capture_output=True, text=True)

    def test_relative_package_path_produces_a_working_absolute_link(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            result = self.install(home, Path("context-handoff"), cwd=PACKAGE.parent)
            self.assertEqual(result.returncode, 0, result.stderr)
            target = home / ".pi/profiles/code/extensions/context-handoff"
            self.assertTrue(target.readlink().is_absolute())
            self.assertEqual((target / "index.ts").read_text(), (PACKAGE / "index.ts").read_text())

    def test_code_only_idempotent_and_no_settings_change(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            for profile in ("code", "general", "wiki", "custom"):
                directory = home / ".pi/profiles" / profile
                directory.mkdir(parents=True)
                (directory / "settings.json").write_text('{"defaultModel":"keep"}\n')
            for _ in range(2):
                result = self.install(home)
                self.assertEqual(result.returncode, 0, result.stderr)
                for profile in ("code", "general", "wiki", "custom"):
                    directory = home / ".pi/profiles" / profile
                    self.assertEqual((directory / "settings.json").read_text(), '{"defaultModel":"keep"}\n')
                    target = directory / "extensions/context-handoff"
                    if profile == "code":
                        self.assertEqual(target.readlink(), PACKAGE)
                    else:
                        self.assertFalse(target.exists())
                self.assertFalse((home / ".pi/agent").exists())

    def test_preserves_local_file_directory_and_unknown_symlinks(self):
        for kind in ("file", "directory", "symlink", "dangling"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                home = Path(temp)
                target = home / ".pi/profiles/code/extensions/context-handoff"
                target.parent.mkdir(parents=True)
                outside = home / "outside"
                if kind == "file":
                    target.write_text("local")
                elif kind == "directory":
                    target.mkdir()
                    (target / "index.ts").write_text("local")
                else:
                    if kind == "symlink":
                        outside.mkdir()
                        (outside / "index.ts").write_text("local")
                    target.symlink_to(outside)
                result = self.install(home)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("preserving local", result.stderr)
                if kind == "file":
                    self.assertEqual(target.read_text(), "local")
                elif kind == "directory":
                    self.assertEqual((target / "index.ts").read_text(), "local")
                else:
                    self.assertEqual(target.readlink(), outside)

    def test_managed_image_and_store_links_upgrade_even_when_dangling(self):
        for old in ("/etc/agents/pi-extensions/context-handoff", "/nix/store/old-context-handoff"):
            with self.subTest(old=old), tempfile.TemporaryDirectory() as temp:
                home = Path(temp)
                target = home / ".pi/profiles/code/extensions/context-handoff"
                target.parent.mkdir(parents=True)
                target.symlink_to(old)
                result = self.install(home)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(target.readlink(), PACKAGE)

    def test_source_repair_is_preserved_as_local_when_managed_package_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            self.assertEqual(self.install(home).returncode, 0)
            target = home / ".pi/profiles/code/extensions/context-handoff"
            replacement = root / "managed-package"
            shutil.copytree(PACKAGE, replacement)
            result = self.install(home, replacement)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("preserving local extension", result.stderr)
            self.assertEqual(target.readlink(), PACKAGE)
            self.assertEqual((target / "core.mjs").read_bytes(), (PACKAGE / "core.mjs").read_bytes())

    def test_preserves_aliased_parent_profile_and_extensions(self):
        for relative in (".pi", ".pi/profiles", ".pi/profiles/code", ".pi/profiles/code/extensions"):
            with self.subTest(path=relative), tempfile.TemporaryDirectory() as temp:
                home = Path(temp)
                target = home / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                outside = home / "outside"
                outside.mkdir()
                target.symlink_to(outside, target_is_directory=True)
                result = self.install(home)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("preserving aliased", result.stderr)
                self.assertEqual(list(outside.iterdir()), [])

    def test_incomplete_package_fails_before_creating_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / "incomplete"
            package.mkdir()
            home = root / "home"
            result = self.install(home, package)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(home.exists())


if __name__ == "__main__":
    unittest.main()
