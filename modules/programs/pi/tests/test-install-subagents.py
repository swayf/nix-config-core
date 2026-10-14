"""Synthetic package installation; no upstream package, Pi or credentials."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

PI_DIR = Path(__file__).resolve().parents[1]
SCRIPT = PI_DIR / "install-subagents.py"
spec = importlib.util.spec_from_file_location("install_subagents", SCRIPT)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallSubagentsTest(unittest.TestCase):
    def package(self, root, name="package"):
        package = root / name
        package.mkdir()
        (package / "package.json").write_text('{"name":"pi-subagents"}')
        (package / "index.js").write_text("export default function () {}")
        return str(package)

    def profiles(self, home):
        return [home / ".pi/agent"] + [home / ".pi/profiles" / name
                for name in ("general", "code", "wiki", "custom profile")]

    def test_profiles_upgrade_idempotence_and_unrelated_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home with spaces"
            originals = {}
            for profile in self.profiles(home):
                profile.mkdir(parents=True)
                settings = {"defaultModel": "local", "defaultThinkingLevel": "high",
                            "subagents": {"defaultModel": "keep"}, "packages": ["npm:unrelated"]}
                (profile / "settings.json").write_text(json.dumps(settings))
                for name in ("auth.json", "models.json", "agents/reviewer.md"):
                    path = profile / name
                    path.parent.mkdir(exist_ok=True)
                    path.write_text("keep exact bytes\n")
                    originals[path] = path.read_bytes()
            for version in (1, 1, 2):
                package = self.package(root, f"package {version}") if version != 1 or not (root / "package 1").exists() else str(root / "package 1")
                # Simulate the package identity used by Nix generations.
                for profile in self.profiles(home):
                    path = profile / "settings.json"
                    current = json.loads(path.read_text())
                    current["packages"] = ["npm:unrelated", "npm:pi-subagents@0.1.0"]
                    path.write_text(json.dumps(current))
                installer.install(home, package)
                before = {p: (p / "settings.json").read_bytes() for p in self.profiles(home)}
                installer.install(home, package)
                for profile in self.profiles(home):
                    settings = json.loads((profile / "settings.json").read_text())
                    self.assertEqual(settings["packages"], ["npm:unrelated", package])
                    self.assertEqual(settings["defaultModel"], "local")
                    self.assertEqual(settings["subagents"], {"defaultModel": "keep"})
                    self.assertEqual((profile / "settings.json").read_bytes(), before[profile])
                    self.assertEqual((profile / "settings.json").stat().st_mode & 0o777, 0o600)
            for path, content in originals.items():
                self.assertEqual(path.read_bytes(), content)
            new = home / ".pi/profiles/new"
            new.mkdir()
            installer.install(home, package)
            self.assertEqual(json.loads((new / "settings.json").read_text())["packages"], [package])

    def test_store_updates_and_package_filters(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            profile = home / ".pi/profiles/code"
            profile.mkdir(parents=True)
            settings = {"packages": ["npm:unrelated", {"source": "/nix/store/old-pi-subagents-0.1.0", "skills": [], "prompts": []}, "npm:pi-subagents"]}
            (profile / "settings.json").write_text(json.dumps(settings))
            package = self.package(root)
            installer.install(home, package)
            self.assertEqual(json.loads((profile / "settings.json").read_text())["packages"],
                             ["npm:unrelated", {"source": package, "skills": [], "prompts": []}])
            backup = profile / "settings.json.before-pi-subagents"
            self.assertEqual(json.loads(backup.read_text()), settings)
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
            self.assertTrue(installer.is_subagents("/nix/store/new-pi-subagents-0.77.0"))
            self.assertFalse(installer.is_subagents("npm:pi-subagents-extra"))

    def test_conflicting_extensions_are_preserved_outside_discovery(self):
        for kind in ("directory", "file", "link", "dangling"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                home = root / "home"
                profile = home / ".pi/profiles/code"
                target = profile / "extensions/subagent"
                target.parent.mkdir(parents=True)
                if kind == "directory":
                    target.mkdir()
                    (target / "index.ts").write_text("legacy")
                elif kind == "file":
                    target.write_text("legacy")
                else:
                    target.symlink_to(root / "old-source")
                    if kind == "link":
                        (root / "old-source").mkdir()
                        (root / "old-source/index.ts").write_text("legacy")
                package = self.package(root)
                installer.install(home, package)
                installer.install(home, package)
                self.assertFalse(os.path.lexists(target))
                backup = profile / "subagent.before-pi-subagents"
                if kind in ("link", "dangling"):
                    self.assertEqual(backup.readlink(), root / "old-source")
                elif kind == "directory":
                    self.assertEqual((backup / "index.ts").read_text(), "legacy")
                else:
                    self.assertEqual(backup.read_text(), "legacy")

    def test_preserves_linked_profiles_settings_and_extensions(self):
        for kind in ("profile", "settings", "extensions"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                home = root / "home"
                profile = home / ".pi/profiles/code"
                profile.mkdir(parents=True)
                outside = root / "outside"
                outside.mkdir()
                if kind == "profile":
                    profile.rmdir()
                    profile.symlink_to(outside)
                elif kind == "settings":
                    (outside / "settings.json").write_text('{"keep":true}')
                    (profile / "settings.json").symlink_to(outside / "settings.json")
                else:
                    (profile / "extensions").symlink_to(outside)
                installer.install(home, self.package(root))
                self.assertEqual(sorted(p.name for p in outside.iterdir()), ["settings.json"] if kind == "settings" else [])
                self.assertFalse((profile / "settings.json.before-pi-subagents").exists())

    def test_backup_conflict_and_invalid_settings_do_not_remove_extension(self):
        for kind in ("backup", "invalid", "packages"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                home = root / "home"
                profile = home / ".pi/profiles/code"
                target = profile / "extensions/subagent"
                target.parent.mkdir(parents=True)
                target.write_text("legacy")
                if kind == "backup":
                    (profile / "subagent.before-pi-subagents").write_text("old backup")
                else:
                    (profile / "settings.json").write_text("[]" if kind == "invalid" else '{"packages":{}}')
                with self.assertRaises(ValueError):
                    installer.install(home, self.package(root))
                self.assertEqual(target.read_text(), "legacy")


if __name__ == "__main__":
    unittest.main()
