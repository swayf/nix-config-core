#!/usr/bin/env python3
"""Explicit wiki initializer: synthetic resources only, no Pi/wiki/QMD runtime."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
BASH = shutil.which("bash")


class WikiInitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="wiki init ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "unrelated-home"
        self.home.mkdir()
        self.profile = self.root / "profiles/fixture"
        self.skills = self.profile / "wiki-skills"
        self.assets = self.root / "assets"
        for name in ("fixture-query", "fixture-ingest", "fixture-read"):
            skill = self.assets / "skills" / name
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(f"name: {name}\n")
        self.qmd = self.root / "qmd-skill"
        self.qmd.mkdir()
        (self.qmd / "SKILL.md").write_text("synthetic skill\n")
        self.instructions = self.root / "instructions.md"
        self.instructions.write_text("SYNTHETIC_RULES\n")
        self.config_dir = self.root / "primary"
        self.legacy_dir = self.root / "legacy"
        self.qmd_dir = self.root / "search"
        self.config = self.config_dir / "config"
        self.index = self.qmd_dir / "index.yml"
        self.config_seed = self.root / "config-seed"
        self.index_seed = self.root / "index-seed"
        # Literal data, not executed/expanded, even if a caller supplies shell text.
        self.config_seed.write_text('FIXTURE="$HOME/literal"\n$(touch SHOULD_NOT_EXIST)\n')
        self.index_seed.write_text("collections: {fixture: {path: /synthetic}}\n")
        self.args = [self.profile, self.assets, self.qmd, self.instructions,
                     self.config_dir, self.legacy_dir, self.qmd_dir,
                     self.config_seed, self.index_seed]
        self.env = {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / "xdg"),
                    "PATH": os.environ.get("PATH", "")}
        self.sentinels = {}
        for name in ("auth.json", "sessions/fixture.jsonl", "settings.json", "SYSTEM.md",
                     "preferences.json", "other-profile/AGENTS.md"):
            path = self.root / "unrelated-state" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic untouched\n")
            self.sentinels[path] = path.read_bytes()
        self.addCleanup(self.check_sentinels)

    def check_sentinels(self):
        for path, content in self.sentinels.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertFalse((self.root / "SHOULD_NOT_EXIST").exists())

    def run_init(self, args=None, success=True):
        result = subprocess.run([BASH, str(HERE / "wiki-init.sh"),
                                 *map(str, self.args if args is None else args)],
                                cwd=self.root, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def test_seed_repeat_and_literal_bytes(self):
        self.run_init()
        for name in ("fixture-query", "fixture-ingest", "fixture-read", "qmd-cli"):
            self.assertTrue((self.skills / name / "SKILL.md").is_file())
        self.assertFalse((self.profile / "skills").exists())
        self.assertFalse((self.profile / "AGENTS.md").exists())
        self.assertEqual((self.profile / "wiki-instructions.md").readlink(), self.instructions)
        self.assertEqual(self.config.read_bytes(), self.config_seed.read_bytes())
        self.assertEqual(self.index.read_bytes(), self.index_seed.read_bytes())
        self.config.write_text("user config\n")
        self.index.write_text("user index\n")
        self.run_init()
        self.assertEqual(self.config.read_text(), "user config\n")
        self.assertEqual(self.index.read_text(), "user index\n")

    def test_preserve_managed_destination_overrides(self):
        self.skills.mkdir(parents=True)
        (self.skills / "fixture-query").mkdir()
        (self.skills / "fixture-query/SKILL.md").write_text("USER")
        (self.skills / "fixture-ingest").symlink_to(self.root / "missing-user")
        (self.skills / "qmd-cli").write_text("USER")
        (self.profile / "wiki-instructions.md").symlink_to("missing-relative")
        (self.profile / "AGENTS.md").write_text("USER_RULES")
        self.run_init()
        self.assertEqual((self.skills / "fixture-query/SKILL.md").read_text(), "USER")
        self.assertEqual((self.skills / "fixture-ingest").readlink(), self.root / "missing-user")
        self.assertEqual((self.skills / "qmd-cli").read_text(), "USER")
        self.assertEqual(str((self.profile / "wiki-instructions.md").readlink()), "missing-relative")
        self.assertEqual((self.profile / "AGENTS.md").read_text(), "USER_RULES")

    def test_migrate_only_managed_links(self):
        automatic = self.profile / "skills"
        automatic.mkdir(parents=True)
        (automatic / "fixture-query").symlink_to(self.assets / "skills/fixture-query")
        (automatic / "qmd-cli").symlink_to(self.qmd)
        (automatic / "fixture-ingest").mkdir()
        (automatic / "fixture-ingest/SKILL.md").write_text("USER_OVERRIDE")
        (automatic / "fixture-read").symlink_to("user-dangling")
        (self.profile / "AGENTS.md").symlink_to(self.instructions)
        self.run_init()
        self.run_init()
        self.assertFalse((automatic / "fixture-query").is_symlink())
        self.assertFalse((automatic / "qmd-cli").is_symlink())
        self.assertFalse((self.skills / "fixture-ingest").exists())
        self.assertFalse((self.skills / "fixture-read").exists())
        self.assertEqual(str((automatic / "fixture-read").readlink()), "user-dangling")
        self.assertEqual((automatic / "fixture-ingest/SKILL.md").read_text(), "USER_OVERRIDE")
        self.assertFalse((self.profile / "AGENTS.md").exists())

    def test_migration_conflicts_and_identical_links(self):
        automatic = self.profile / "skills"
        automatic.mkdir(parents=True)
        self.skills.mkdir()
        for name in ("fixture-query", "fixture-ingest"):
            (automatic / name).symlink_to(self.assets / "skills" / name)
        (self.skills / "fixture-query").write_text("USER")
        (self.skills / "fixture-ingest").symlink_to(self.assets / "skills/fixture-ingest")
        self.run_init()
        self.assertTrue((automatic / "fixture-query").is_symlink())
        self.assertEqual((self.skills / "fixture-query").read_text(), "USER")
        self.assertFalse((automatic / "fixture-ingest").is_symlink())
        self.assertTrue((self.skills / "fixture-ingest").is_symlink())

    def test_retired_current_root_only(self):
        self.run_init()
        (self.skills / "retired").symlink_to(self.assets / "skills/retired")
        (self.skills / "unmanaged").symlink_to(self.root / "missing")
        (self.skills / "old-root").symlink_to(self.root / "old-assets/skills/old-root")
        shutil.rmtree(self.assets / "skills/fixture-query")
        self.run_init()
        self.assertFalse((self.skills / "retired").is_symlink())
        self.assertFalse((self.skills / "fixture-query").is_symlink())
        self.assertTrue((self.skills / "unmanaged").is_symlink())
        self.assertTrue((self.skills / "old-root").is_symlink())

    def test_legacy_existing_config_suppresses_index(self):
        self.legacy_dir.mkdir()
        (self.legacy_dir / "config").write_text("legacy user config")
        self.run_init()
        self.assertFalse(self.config_dir.exists())
        self.assertEqual((self.legacy_dir / "config").read_text(), "legacy user config")
        self.assertFalse(self.index.exists())

    def test_empty_legacy_directory_gets_seed(self):
        self.legacy_dir.mkdir()
        self.run_init()
        self.assertFalse(self.config_dir.exists())
        self.assertEqual((self.legacy_dir / "config").read_bytes(), self.config_seed.read_bytes())
        self.assertTrue(self.index.is_file())

    def test_existing_primary_directory_beats_legacy(self):
        self.config_dir.mkdir()
        self.legacy_dir.mkdir()
        self.run_init()
        self.assertTrue(self.config.is_file())
        self.assertFalse((self.legacy_dir / "config").exists())

    def test_dangling_primary_directory_is_not_claimed(self):
        self.config_dir.symlink_to(self.root / "missing-primary")
        self.legacy_dir.mkdir()
        self.run_init(success=False)
        self.assertEqual(self.config_dir.readlink(), self.root / "missing-primary")
        self.assertFalse((self.legacy_dir / "config").exists())
        self.assertFalse(self.index.exists())

    def test_dangling_config_is_preserved_and_suppresses_index(self):
        self.config_dir.mkdir()
        self.config.symlink_to(self.root / "missing-config")
        self.run_init()
        self.assertEqual(self.config.readlink(), self.root / "missing-config")
        self.assertFalse(self.index.exists())

    def test_existing_index_file_directory_and_dangling_link(self):
        self.qmd_dir.mkdir()
        for kind in ("file", "directory", "link"):
            with self.subTest(kind=kind):
                if kind == "file":
                    self.index.write_text("USER_INDEX")
                elif kind == "directory":
                    self.index.mkdir()
                else:
                    self.index.symlink_to("missing-index")
                self.run_init()
                if kind == "file":
                    self.assertEqual(self.index.read_text(), "USER_INDEX")
                    self.index.unlink()
                elif kind == "directory":
                    self.assertTrue(self.index.is_dir())
                    self.index.rmdir()
                else:
                    self.assertEqual(str(self.index.readlink()), "missing-index")
                self.config.unlink()

    def test_existing_config_directory_is_preserved(self):
        self.config.mkdir(parents=True)
        self.run_init()
        self.assertTrue(self.config.is_dir())
        self.assertFalse(self.index.exists())

    def test_missing_sources_keep_link_contract(self):
        shutil.rmtree(self.assets)
        shutil.rmtree(self.qmd)
        self.instructions.unlink()
        self.run_init()
        self.assertEqual((self.skills / "qmd-cli").readlink(), self.qmd)
        self.assertFalse((self.profile / "wiki-instructions.md").exists())
        self.assertEqual(list(self.skills.iterdir()), [self.skills / "qmd-cli"])

    def test_atomic_source_edits_stay_visible(self):
        self.run_init()
        replacement = self.root / "replacement"
        replacement.write_text("NEW_RULES")
        replacement.replace(self.instructions)
        self.assertEqual((self.profile / "wiki-instructions.md").read_text(), "NEW_RULES")
        self.instructions.unlink()
        self.run_init()
        self.assertTrue((self.profile / "wiki-instructions.md").is_symlink())

    def test_source_alias_is_not_resolved(self):
        alias = self.root / "assets-alias"
        alias.symlink_to(self.assets)
        self.args[1] = alias
        self.run_init()
        self.assertEqual((self.skills / "fixture-query").readlink(), alias / "skills/fixture-query")

    def test_relative_arguments_spaces_and_ignored_environment(self):
        self.run_init([p.relative_to(self.root) for p in self.args])
        self.assertEqual((self.skills / "fixture-query").readlink(), self.assets / "skills/fixture-query")
        self.assertTrue(self.config.is_file())

    def test_missing_extra_and_empty_arguments_have_no_side_effects(self):
        for args in ([], self.args[:-1], [*self.args, self.root], ["", *self.args[1:]]):
            with self.subTest(count=len(args)):
                result = self.run_init(args, success=False)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(self.profile.exists())
                self.assertFalse(self.config_dir.exists())

    def test_missing_seed_does_not_publish_empty_config(self):
        for seed in (self.config_seed, self.index_seed):
            with self.subTest(seed=seed.name):
                content = seed.read_bytes()
                seed.unlink()
                self.run_init(success=False)
                self.assertFalse(self.config.exists())
                self.assertFalse(self.index.exists())
                seed.write_bytes(content)
        self.run_init()
        self.assertTrue(self.config.is_file())

    def test_unused_seed_files_need_not_exist(self):
        self.run_init()
        self.config_seed.unlink()
        self.index_seed.unlink()
        self.run_init()
        self.index.unlink()
        self.run_init()
        self.assertFalse(self.index.exists())  # Existing config never reseeds index.


if __name__ == "__main__":
    unittest.main()
