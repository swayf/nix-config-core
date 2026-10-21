"""Установка wiki-ask во все профили кроме wiki; без модельных запросов."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile
import unittest

PI_DIR = Path(__file__).resolve().parents[1]
SCRIPT = PI_DIR / "install-wiki-ask.sh"
PACKAGE = PI_DIR / "extensions/wiki-ask"
PI = os.environ.get("PI_BIN") or shutil.which("pi")


class InstallWikiAskTest(unittest.TestCase):
    def install(self, home, package=PACKAGE, check=True, **env):
        return subprocess.run(["bash", str(SCRIPT), str(package)],
                              env={**os.environ, "HOME": str(home), **env}, check=check,
                              capture_output=True, text=True)

    def target(self, profile):
        return profile / "extensions/wiki-ask"

    def profiles(self, home):
        return [home / ".pi/agent"] + [home / ".pi/profiles" / name
                for name in ("general", "code", "wiki", "custom profile")]

    def test_default_named_custom_new_profiles_settings_and_upgrades(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home with spaces"
            originals = {}
            for profile in self.profiles(home):
                profile.mkdir(parents=True)
                for name in ("settings.json", "models.json", "auth.json"):
                    file = profile / name
                    file.write_text('{"keep":"local"}\n')
                    originals[file] = file.read_bytes()
                other = profile / "extensions/other.ts"
                other.parent.mkdir()
                other.write_text("keep extension")
                originals[other] = other.read_bytes()
            (home / ".pi/profiles/not-a-directory").write_text("keep")
            for version in (1, 1, 2):
                package = root / f"package v{version}"
                shutil.copytree(PACKAGE, package, dirs_exist_ok=True)
                self.install(home, package)
                for profile in self.profiles(home):
                    target = self.target(profile)
                    if profile.name == "wiki":
                        self.assertFalse(target.exists())
                        self.assertFalse(target.is_symlink())
                    else:
                        self.assertTrue(target.is_symlink())
                        self.assertEqual(target.resolve(), package)
                for file, original in originals.items():
                    self.assertEqual(file.read_bytes(), original)
            new = home / ".pi/profiles/new"
            new.mkdir()
            self.install(home, package)
            self.assertEqual(self.target(new).resolve(), package)

    def test_identical_local_installation_is_backed_up_once(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home"
            profile = home / ".pi/profiles/code"
            target = self.target(profile)
            shutil.copytree(PACKAGE, target)
            self.install(home)
            backup = profile / "wiki-ask.before-home-manager"
            self.assertTrue(target.is_symlink())
            self.assertEqual(target.resolve(), PACKAGE)
            for file in PACKAGE.iterdir():
                self.assertEqual((backup / file.name).read_bytes(), file.read_bytes())
            self.install(home)
            self.assertTrue(backup.is_dir())

    def test_preserves_local_overrides_extra_files_and_backups(self):
        for case in ("file", "modified", "extra", "hidden", "backup", "broken-backup", "file-link"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                home = root / "home"
                profile = home / ".pi/profiles/code"
                target = self.target(profile)
                target.parent.mkdir(parents=True)
                backup = profile / "wiki-ask.before-home-manager"
                if case == "file":
                    target.write_text("keep")
                else:
                    shutil.copytree(PACKAGE, target)
                    if case == "modified":
                        (target / "core.mjs").write_text("local implementation")
                    elif case in ("extra", "hidden"):
                        (target / ("extra" if case == "extra" else ".hidden")).write_text("keep")
                    elif case == "backup":
                        backup.mkdir()
                        (backup / "keep").write_text("keep")
                    elif case == "broken-backup":
                        backup.symlink_to(root / "missing")
                    elif case == "file-link":
                        (target / "index.ts").unlink()
                        (target / "index.ts").symlink_to(PACKAGE / "index.ts")
                result = self.install(home)
                self.assertIn("preserving local wiki-ask", result.stderr)
                self.assertFalse(target.is_symlink())
                if case == "file":
                    self.assertEqual(target.read_text(), "keep")
                elif case == "modified":
                    self.assertEqual((target / "core.mjs").read_text(), "local implementation")
                elif case == "file-link":
                    self.assertTrue((target / "index.ts").is_symlink())
                elif case in ("extra", "hidden"):
                    self.assertEqual((target / ("extra" if case == "extra" else ".hidden")).read_text(), "keep")
                elif case == "backup":
                    self.assertEqual((backup / "keep").read_text(), "keep")
                elif case == "broken-backup":
                    self.assertTrue(backup.is_symlink())

    def test_repairs_old_and_broken_links_without_changing_old_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            old = root / "old"
            old.mkdir()
            (old / "keep").write_text("keep")
            for name, source in (("code", old), ("custom", root / "missing")):
                target = self.target(home / ".pi/profiles" / name)
                target.parent.mkdir(parents=True)
                target.symlink_to(source)
            self.install(home)
            for name in ("code", "custom"):
                self.assertEqual(self.target(home / ".pi/profiles" / name).resolve(), PACKAGE)
            self.assertEqual((old / "keep").read_text(), "keep")

    def test_wiki_and_alias_are_untouched(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home"
            wiki = home / ".pi/profiles/wiki"
            target = self.target(wiki)
            target.parent.mkdir(parents=True)
            target.write_text("user-owned wiki extension")
            (wiki.parent / "wiki-alias").symlink_to(wiki)
            self.install(home)
            self.assertEqual(target.read_text(), "user-owned wiki extension")
            self.assertFalse(target.is_symlink())

    def test_failed_hash_check_preserves_local_installation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            profile = home / ".pi/profiles/code"
            target = self.target(profile)
            shutil.copytree(PACKAGE, target)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            command = bin_dir / "sha256sum"
            command.write_text("#!/bin/sh\nexit 1\n")
            command.chmod(0o755)
            result = self.install(home, PATH=f"{bin_dir}:{os.environ['PATH']}")
            self.assertIn("preserving local wiki-ask", result.stderr)
            self.assertFalse(target.is_symlink())
            self.assertFalse((profile / "wiki-ask.before-home-manager").exists())

    def test_missing_package_does_not_modify_home(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            result = self.install(home, root / "missing", check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing wiki-ask package", result.stderr)
            self.assertFalse(home.exists())

    @unittest.skipUnless(PI, "нужен установленный pi")
    def test_pi_discovers_command_everywhere_except_wiki_without_api(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            for profile in self.profiles(home):
                profile.mkdir(parents=True)
            self.install(home)
            for profile in self.profiles(home):
                with self.subTest(profile=profile.name):
                    env = {key: value for key, value in os.environ.items() if not key.startswith("PI_")}
                    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
                               PI_CODING_AGENT_DIR=str(profile), PI_SKIP_VERSION_CHECK="1", PI_TELEMETRY="0")
                    result = subprocess.run([PI, "--mode", "rpc", "--no-session", "--offline", "--no-approve",
                                             "--no-context-files", "--no-skills", "--no-prompt-templates"],
                                            input=b'{"id":"commands","type":"get_commands"}\n',
                                            capture_output=True, cwd=root, env=env, timeout=20, check=True)
                    records = [json.loads(line) for line in result.stdout.split(b"\n") if line]
                    response = next(record for record in records if record.get("id") == "commands")
                    self.assertTrue(response["success"], response)
                    commands = [command for command in response["data"]["commands"] if command["name"] == "wiki-ask"]
                    self.assertEqual(len(commands), int(profile.name != "wiki"), response)
                    self.assertNotIn("Failed to load extension", result.stderr.decode())
                    self.assertFalse((profile / "settings.json").exists())


if __name__ == "__main__":
    unittest.main()
