"""Synthetic explicit-source seeding contracts; no private templates or real Herdr."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("install-herdr-projects.py")
SPEC = importlib.util.spec_from_file_location("projects", SCRIPT)
projects = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(projects)


class InstallerTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="template-seeder-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / "home with spaces"
        self.home.mkdir()
        self.target = self.home / "plus config"
        self.source = self.root / "explicit source"
        self.content = {}
        for directory in ("projects", "worktrees"):
            for name in ("alpha", "beta"):
                path = self.source / directory / f"{name}.toml"
                path.parent.mkdir(parents=True, exist_ok=True)
                # Commands are opaque data, not installation actions.
                text = f'name = "{name}"\ncommand = "never-run-fixture-command"\n'
                path.write_text(text)
                self.content[path.relative_to(self.source)] = text
        self.env = {"HOME": str(self.home), "PATH": "", "PYTHONDONTWRITEBYTECODE": "1"}
        environment = patch.dict(os.environ, self.env, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        cli = patch.object(projects.subprocess, "check_output",
                           side_effect=AssertionError("No implicit Herdr calls"))
        cli.start()
        self.addCleanup(cli.stop)

    def test_fresh_edit_delete_repeat(self):
        added = projects.install(self.source, self.target)
        self.assertEqual(len(added), 4)
        for relative, text in self.content.items():
            path = self.target / relative
            self.assertFalse(path.is_symlink())
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            self.assertEqual(path.read_text(), text)
            tomllib.loads(path.read_text())
        edited = self.target / "projects/alpha.toml"
        removed = self.target / "worktrees/beta.toml"
        edited.write_text("# local override\n")
        removed.unlink()
        (self.source / "projects/new.toml").write_text('name = "later"\n')
        self.assertEqual(projects.install(self.source, self.target), [])
        self.assertEqual(edited.read_text(), "# local override\n")
        self.assertFalse(removed.exists())
        self.assertFalse((self.target / "projects/new.toml").exists())

    def test_existing_file_and_dangling_symlink_preserved(self):
        directory = self.target / "projects"
        directory.mkdir(parents=True)
        existing = directory / "alpha.toml"
        existing.write_text("# user file\n")
        dangling = directory / "beta.toml"
        dangling.symlink_to(self.root / "missing")
        self.assertEqual(len(projects.install(self.source, self.target)), 2)
        self.assertEqual(existing.read_text(), "# user file\n")
        self.assertTrue(dangling.is_symlink())
        self.assertFalse(dangling.exists())

    def test_existing_managed_link_is_not_replaced_or_followed(self):
        directory = self.target / "projects"
        directory.mkdir(parents=True)
        managed = directory / "alpha.toml"
        upstream = self.root / "managed-template.toml"
        upstream.write_text("# existing managed resource\n")
        managed.symlink_to(upstream)
        projects.install(self.source, self.target)
        self.assertEqual(managed.readlink(), upstream)
        self.assertEqual(upstream.read_text(), "# existing managed resource\n")

    def test_marker_file_or_dangling_link_prevents_seeding(self):
        for kind in ("file", "dangling"):
            with self.subTest(kind=kind):
                target = self.home / kind
                target.mkdir()
                marker = target / projects.MARKER
                if kind == "file":
                    marker.write_text("existing marker\n")
                else:
                    marker.symlink_to(self.root / "absent")
                # The marker is checked before even reading the source.
                self.assertEqual(projects.install(self.root / "missing-source", target), [])
                self.assertEqual([p.name for p in target.iterdir()], [projects.MARKER])

    def test_malformed_input_leaves_target_untouched(self):
        (self.source / "worktrees/beta.toml").write_text("[broken\n")
        with self.assertRaises(tomllib.TOMLDecodeError):
            projects.install(self.source, self.target)
        self.assertFalse(self.target.exists())

    def test_missing_source_has_no_home_fallback(self):
        decoy = self.home / "projects/decoy.toml"
        decoy.parent.mkdir()
        decoy.write_text('name = "do not discover"\n')
        with self.assertRaises(ValueError):
            projects.install(self.root / "absent", self.target)
        self.assertFalse(self.target.exists())

    def test_missing_template_group_leaves_target_untouched(self):
        for path in (self.source / "worktrees").iterdir():
            path.unlink()
        with self.assertRaises(ValueError):
            projects.install(self.source, self.target)
        self.assertFalse(self.target.exists())

    def test_copy_new_exclusive_and_no_temporary_files_left(self):
        path = self.target / "fixture.toml"
        self.assertTrue(projects.copy_new(path, b"first\n"))
        self.assertFalse(projects.copy_new(path, b"second\n"))
        self.assertEqual(path.read_bytes(), b"first\n")
        self.assertEqual(list(self.target.iterdir()), [path])

    def test_unrelated_synthetic_auth_session_and_preferences_unchanged(self):
        state = {}
        for name in (".pi/agent/auth.json", ".pi/agent/sessions/fixture.jsonl",
                     "plus config/settings.toml"):
            path = self.home / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic state; not a credential\n")
            state[path] = path.read_bytes()
        for _ in range(2):
            projects.install(self.source, self.target)
        self.assertEqual({p: p.read_bytes() for p in state}, state)

    def run_cli(self, *args, env=None):
        result = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                                env=env or self.env, cwd=self.root,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_cli_explicit_config_without_herdr(self):
        self.assertEqual(self.run_cli(self.source, "--config-dir", self.target).count("Installed"), 4)
        self.assertEqual(self.run_cli(self.source, "--config-dir", self.target), "")

    def test_cli_reported_config_with_stubbed_herdr(self):
        bins = self.root / "bin"
        bins.mkdir()
        stub = bins / "herdr"
        stub.write_text(f'#!{sys.executable}\n'
                        'import os, sys\n'
                        'assert sys.argv[1:] == ["plugin", "config-dir", "cloudmanic.herdr-plus"]\n'
                        'print(os.environ["FIXTURE_CONFIG"])\n')
        stub.chmod(0o755)
        env = {**self.env, "PATH": str(bins), "FIXTURE_CONFIG": str(self.target)}
        self.assertEqual(self.run_cli(self.source, env=env).count("Installed"), 4)
        self.assertTrue((self.target / projects.MARKER).is_file())

    def test_cli_requires_source(self):
        result = subprocess.run([sys.executable, str(SCRIPT)], env=self.env, cwd=self.root,
                                capture_output=True, text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source", result.stderr)
        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
