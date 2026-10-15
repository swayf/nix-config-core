"""Synthetic explicit-source prompt tests; no Pi, private payloads or real HOME."""

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).with_name("install-pi-prompts.py")
spec = importlib.util.spec_from_file_location("pi_prompts", SCRIPT)
prompts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prompts)


class PiPromptsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / "isolated home"
        self.profiles = self.home / "chosen profiles"
        self.shared = self.base / "shared appends"
        self.defaults = self.base / "fallback appends"
        for profile in ("alpha", "beta", "custom"):
            (self.profiles / profile).mkdir(parents=True)
            self.put(self.shared, profile, f"Shared fixture {profile}\n")
            self.put(self.defaults, profile, f"Fallback fixture {profile}\n")
        self.sentinels = {}
        for name in (".pi/agent/auth.json", ".pi/agent/sessions/fixture.jsonl",
                     ".pi/preferences.json", "chosen profiles/alpha/settings.json",
                     "chosen profiles/alpha/SYSTEM.md"):
            path = self.home / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic unrelated state\n")
            self.sentinels[path] = path.read_bytes()
        self.addCleanup(self.assert_sentinels)

    def assert_sentinels(self):
        for path, content in self.sentinels.items():
            self.assertFalse(path.is_symlink())
            self.assertEqual(path.read_bytes(), content)

    def put(self, root, profile, content):
        path = root / profile / "APPEND_SYSTEM.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def target(self, profile="alpha"):
        return self.profiles / profile / "APPEND_SYSTEM.md"

    def install(self):
        prompts.install(self.profiles, self.shared, self.defaults)

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT.resolve()), *map(str, args)], cwd=self.base,
            env={"HOME": str(self.home), "PATH": "", "PYTHONDONTWRITEBYTECODE": "1",
                 "AGENTS_PI_PROMPTS_DIR": str(self.base / "ignored environment source")},
            capture_output=True, text=True, timeout=10,
        )

    def test_fresh_and_repeat_keep_managed_links(self):
        self.install()
        before = {p: self.target(p).lstat().st_ino for p in ("alpha", "beta", "custom")}
        for _ in range(3):
            self.install()
            for profile, inode in before.items():
                self.assertEqual(self.target(profile).readlink(),
                                 self.shared / profile / "APPEND_SYSTEM.md")
                self.assertEqual(self.target(profile).lstat().st_ino, inode)

    def test_atomic_edit_stays_visible_without_reinitialization(self):
        self.install()
        replacement = self.shared / "alpha/replacement.md"
        replacement.write_text("Edited fixture\n")
        replacement.replace(self.shared / "alpha/APPEND_SYSTEM.md")
        self.assertEqual(self.target().read_text(), "Edited fixture\n")

    def test_fallback_upgrade_disappearance_and_restore(self):
        shared = self.shared / "alpha/APPEND_SYSTEM.md"
        shared.unlink()
        self.install()
        self.assertEqual(self.target().readlink(), self.defaults / "alpha/APPEND_SYSTEM.md")
        self.put(self.shared, "alpha", "Restored fixture\n")
        self.install()
        self.assertEqual(self.target().readlink(), shared)
        shared.unlink()
        self.install()
        self.assertEqual(self.target().readlink(), self.defaults / "alpha/APPEND_SYSTEM.md")

    def test_preserve_local_files_directories_and_user_links(self):
        self.target().write_text("Local fixture\n")
        self.target("beta").mkdir()
        custom = self.put(self.base / "user source", "custom", "User fixture\n")
        self.target("custom").symlink_to(custom)
        self.install()
        self.assertEqual(self.target().read_text(), "Local fixture\n")
        self.assertFalse(self.target().is_symlink())
        self.assertTrue(self.target("beta").is_dir())
        self.assertEqual(self.target("custom").readlink(), custom)

    def test_preserve_dangling_user_links(self):
        missing = self.base / "missing.md"
        self.target().symlink_to(missing)
        self.install()
        self.assertEqual(self.target().readlink(), missing)
        self.assertFalse(missing.exists())

    def test_preserve_relative_user_link_even_to_same_source(self):
        # Ownership is the exact link spelling, not a resolved target comparison.
        relative = Path("../../../shared appends/alpha/APPEND_SYSTEM.md")
        self.target().symlink_to(relative)
        self.install()
        self.assertEqual(self.target().readlink(), relative)

    def test_repair_dangling_managed_link_when_fallback_exists(self):
        (self.shared / "alpha/APPEND_SYSTEM.md").unlink()
        self.target().symlink_to(self.shared / "alpha/APPEND_SYSTEM.md")
        self.install()
        self.assertEqual(self.target().readlink(), self.defaults / "alpha/APPEND_SYSTEM.md")

    def test_absent_sources_leave_targets_unchanged(self):
        for root in (self.shared, self.defaults):
            for profile in ("alpha", "beta", "custom"):
                (root / profile / "APPEND_SYSTEM.md").unlink()
        self.target().symlink_to(self.shared / "alpha/APPEND_SYSTEM.md")
        self.target("beta").write_text("Local fixture\n")
        self.install()
        self.assertEqual(self.target().readlink(), self.shared / "alpha/APPEND_SYSTEM.md")
        self.assertEqual(self.target("beta").read_text(), "Local fixture\n")
        self.assertFalse(self.target("custom").is_symlink())
        self.assertFalse(self.target("custom").exists())

    def test_sources_that_are_not_files_are_ignored(self):
        (self.shared / "alpha/APPEND_SYSTEM.md").unlink()
        (self.shared / "alpha/APPEND_SYSTEM.md").mkdir()
        (self.shared / "beta/APPEND_SYSTEM.md").unlink()
        (self.shared / "beta/APPEND_SYSTEM.md").symlink_to(self.base / "absent source")
        self.install()
        for profile in ("alpha", "beta"):
            self.assertEqual(self.target(profile).readlink(),
                             self.defaults / profile / "APPEND_SYSTEM.md")

    def test_missing_profiles_is_noop_and_no_other_home_lookup(self):
        missing = self.base / "absent profiles"
        prompts.install(missing, self.shared, self.defaults)
        self.assertFalse(missing.exists())
        self.assertFalse((self.home / ".pi/profiles").exists())
        self.assertFalse(self.target().exists())

    def test_no_profile_creation_and_ignore_non_directories(self):
        self.put(self.shared, "source-only", "Not installed fixture\n")
        (self.profiles / "not-a-profile").write_text("Unrelated fixture\n")
        (self.profiles / "dangling-profile").symlink_to(self.base / "missing profile")
        self.install()
        self.assertFalse((self.profiles / "source-only").exists())
        self.assertEqual((self.profiles / "not-a-profile").read_text(), "Unrelated fixture\n")
        self.assertTrue((self.profiles / "dangling-profile").is_symlink())

    def test_source_symlink_path_is_not_resolved(self):
        alias = self.base / "shared alias"
        alias.symlink_to(self.shared, target_is_directory=True)
        prompts.install(self.profiles, alias, self.defaults)
        self.assertEqual(self.target().readlink(), alias / "alpha/APPEND_SYSTEM.md")
        self.assertEqual(self.target().read_text(), "Shared fixture alpha\n")

    def test_changed_roots_do_not_claim_old_links(self):
        self.install()
        other = self.base / "other source"
        self.put(other, "alpha", "Other fixture\n")
        prompts.install(self.profiles, other, self.base / "other fallback")
        self.assertEqual(self.target().readlink(), self.shared / "alpha/APPEND_SYSTEM.md")

    def test_link_errors_are_not_hidden(self):
        with mock.patch.object(Path, "symlink_to", side_effect=OSError("fixture denied")):
            with self.assertRaisesRegex(OSError, "fixture denied"):
                self.install()
        self.assertFalse(self.target().exists())

    def test_cli_requires_all_three_explicit_paths_before_writing(self):
        for args in ((), (self.profiles,), (self.profiles, self.shared),
                     (self.profiles, self.shared, self.defaults, "extra")):
            with self.subTest(args=args):
                result = self.cli(*args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse(self.target().is_symlink())
                self.assertFalse(self.target().exists())

    def test_cli_relative_paths_spaces_repeat_and_environment_ignored(self):
        args = [p.relative_to(self.base) for p in (self.profiles, self.shared, self.defaults)]
        for _ in range(2):
            result = self.cli(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.target().readlink(), self.shared / "alpha/APPEND_SYSTEM.md")
            self.assertEqual(self.target().read_text(), "Shared fixture alpha\n")
        self.assertFalse((self.home / ".pi/profiles").exists())


if __name__ == "__main__":
    unittest.main()
