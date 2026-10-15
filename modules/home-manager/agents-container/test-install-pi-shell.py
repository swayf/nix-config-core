"""Synthetic CLI, filesystem and launch contracts; never starts Pi or Herdr."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import tempfile
import unittest

HELPER = Path(__file__).with_name("install-pi-shell.py")
SPEC = importlib.util.spec_from_file_location("pi_shell", HELPER)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PiShellTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pi-shell-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / "home with ' spaces"
        self.home.mkdir()
        self.sources = self.base / "seeds with ' spaces"
        self.sources.mkdir()
        for name in ("interactive", "login", "envrc", "prompt"):
            (self.sources / name).write_text("# seed " + name + "\n")
        defaults = self.sources / "profiles"
        for name in ("discussion", "builder", "notes"):
            (defaults / name).mkdir(parents=True)
            (defaults / name / "settings.json").write_text('{"fixture": true}\n')
            (defaults / name / "settings.json").chmod(0o444)
        self.fake = self.base / "fake ' pi"
        self.fake.write_text(
            f"#!{sys.executable}\nimport json, os, sys\n"
            "print(json.dumps({'args': sys.argv[1:], 'profile': os.environ.get('PI_CODING_AGENT_DIR'),"
            " 'display': os.environ.get('DISPLAY')}))\n"
            "sys.exit(int(os.environ.get('FIXTURE_EXIT', '0')))\n")
        self.fake.chmod(0o755)
        self.bridge = self.base / "bridge.sock"
        self.policy = {
            "directories": ["bin", "config", "state"],
            "bashrc": {"destination": "rc", "source": str(self.sources / "interactive"),
                       "marker": "source fixture-interactive", "append": "\nsource fixture-interactive\n"},
            "bash_profile": {"destination": "login", "source": str(self.sources / "login"),
                             "marker": "fixture-rc", "append": "\n# fixture-rc\n"},
            "links": [{"destination": "config/env/rc", "source": str(self.sources / "envrc")},
                      {"destination": "config/prompt", "source": str(self.sources / "prompt")}],
            "pi": {
                "wrapper": "bin/pi", "marker": "# Managed fixture wrapper.",
                "executable": str(self.fake), "interpreter": os.environ.get("TEST_SHELL") or shutil.which("bash"),
                "profiles_directory": "profiles with ' spaces", "legacy_agent_directory": "old agent",
                "default_profile": "discussion", "profiles": ["discussion", "builder", "notes"],
                "settings_source": str(defaults), "clipboard_socket": str(self.bridge),
                "clipboard_display": ":fixture-clipboard",
                "profile_wrappers": {"bin/pi-build": "builder", "bin/pi-notes": "notes"},
                "legacy_wrapper": {"destination": "bin/pi-old", "content": "#!/bin/sh\n# old fixture\nexec pi\n"},
            },
        }
        self.policy_file = self.base / "policy.json"
        self.env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.home)}
        self.install("shell")
        self.install("pi")
        self.install("wrappers")

    def install(self, mode, policy=None, success=True):
        self.policy_file.write_text(json.dumps(self.policy if policy is None else policy))
        result = subprocess.run([sys.executable, str(HELPER), str(self.policy_file), str(self.home), mode],
                                capture_output=True, text=True, env=self.env, cwd=self.base)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def run_pi(self, *args, wrapper="bin/pi", **env):
        result = subprocess.run([str(self.home / wrapper), *args], env={**self.env, **env},
                                capture_output=True, text=True, cwd=self.base)
        return result.returncode, json.loads(result.stdout)

    def session(self, profile):
        directory = self.home / self.policy["pi"]["profiles_directory"] / profile
        directory.mkdir(parents=True, exist_ok=True)
        return str(directory / "sessions/project/session.jsonl")

    def test_default_and_empty_selection(self):
        for env in ({}, {"PI_CODING_AGENT_DIR": ""}):
            _, state = self.run_pi("prompt with spaces", **env)
            self.assertEqual(state["profile"], str(self.home / self.policy["pi"]["profiles_directory"] / "discussion"))
            self.assertEqual(state["args"], ["prompt with spaces"])

    def test_named_and_custom_resume(self):
        for name in ("discussion", "builder", "notes", "custom ' name"):
            session = self.session(name)
            _, state = self.run_pi("--session", session, "prompt")
            self.assertEqual(state["profile"], str(Path(session).parents[2]))
            self.assertEqual(state["args"], ["--session", session, "prompt"])

    def test_explicit_profile_wins(self):
        _, state = self.run_pi("--session", self.session("builder"), PI_CODING_AGENT_DIR="explicit")
        self.assertEqual(state["profile"], "explicit")

    def test_legacy_resume(self):
        directory = self.home / self.policy["pi"]["legacy_agent_directory"]
        _, state = self.run_pi("--session", str(directory / "sessions/a/b"))
        self.assertEqual(state["profile"], str(directory))

    def test_argument_boundaries_and_invalid_profiles(self):
        root = self.home / self.policy["pi"]["profiles_directory"]
        valid = self.session("builder")
        cases = [["--continue"], ["--resume"], ["--session"], ["--session", "id"],
                 ["--", "--session", valid], ["--session=" + valid],
                 ["--session", str(root / "missing/sessions/a")],
                 ["--session", str(root / "builder/nested/sessions/a")],
                 ["--session", str(root / "../sessions/a")], ["prompt --session " + valid]]
        for args in cases:
            _, state = self.run_pi(*args)
            self.assertEqual(state["args"], args)
            self.assertEqual(state["profile"], str(root / "discussion"))

    def test_clipboard_and_status(self):
        with socket.socket(socket.AF_UNIX) as bridge:
            bridge.bind(str(self.bridge))
            status, state = self.run_pi(FIXTURE_EXIT="17")
            self.assertEqual(status, 17)
            self.assertEqual(state["display"], ":fixture-clipboard")
            _, state = self.run_pi(DISPLAY=":0")
            self.assertEqual(state["display"], ":0")
            _, state = self.run_pi(WAYLAND_DISPLAY="wayland-fixture")
            self.assertIsNone(state["display"])
        self.bridge.unlink()
        self.bridge.write_text("not a socket")
        self.assertIsNone(self.run_pi()[1]["display"])

    def test_generated_wrapper_uses_runtime_home(self):
        other = self.base / "other home"
        other.mkdir()
        _, state = self.run_pi(HOME=str(other))
        self.assertEqual(state["profile"], str(other / self.policy["pi"]["profiles_directory"] / "discussion"))

    def test_profile_wrappers_select_and_forward(self):
        fake_pi = self.home / "bin/pi"
        fake_pi.write_text(self.fake.read_text())
        for destination, profile in self.policy["pi"]["profile_wrappers"].items():
            _, state = self.run_pi("one", "two words", wrapper=destination,
                                   PATH=str(self.home / "bin"), PI_CODING_AGENT_DIR="old")
            self.assertEqual(state["profile"], str(self.home / self.policy["pi"]["profiles_directory"] / profile))
            self.assertEqual(state["args"], ["one", "two words"])

    def test_wrapper_upgrade_repeat_and_local_override(self):
        target = self.home / "bin/pi"
        target.write_text("#!/bin/sh\n" + self.policy["pi"]["marker"] + "\n# old\n")
        self.install("pi")
        installed = target.read_bytes()
        self.install("pi")
        self.assertEqual(target.read_bytes(), installed)
        target.write_text("#!/bin/sh\n# local wrapper\n")
        self.install("pi")
        self.assertEqual(target.read_text(), "#!/bin/sh\n# local wrapper\n")

    def test_shell_local_append_is_idempotent(self):
        for name in ("rc", "login"):
            (self.home / name).unlink()
            (self.home / name).write_text("# local\n")
        for _ in range(3):
            self.install("shell")
        self.assertEqual((self.home / "rc").read_text(), "# local\n" + self.policy["bashrc"]["append"])
        self.assertEqual((self.home / "login").read_text(), "# local\n" + self.policy["bash_profile"]["append"])

    def test_shell_exact_vs_substring_markers(self):
        (self.home / "rc").unlink()
        (self.home / "rc").write_text("# " + self.policy["bashrc"]["marker"] + "\n")
        (self.home / "login").unlink()
        (self.home / "login").write_text("# contains fixture-rc already\n")
        self.install("shell")
        self.assertEqual((self.home / "rc").read_text().count("source fixture-interactive"), 2)
        self.assertEqual((self.home / "login").read_text(), "# contains fixture-rc already\n")

    def test_shell_links_and_dangling_rc_preserved(self):
        target = self.home / "rc"
        target.unlink()
        target.symlink_to(self.base / "missing")
        self.install("shell")
        self.assertEqual(target.readlink(), self.base / "missing")
        for link in self.policy["links"]:
            self.assertEqual((self.home / link["destination"]).readlink(), Path(link["source"]))

    def test_legacy_dangling_login_and_seed_link_errors(self):
        for name in ("login", "config/prompt"):
            target = self.home / name
            original = target.readlink()
            target.unlink()
            target.symlink_to(self.base / "missing")
            self.install("shell", success=False)
            self.assertEqual(target.readlink(), self.base / "missing")
            target.unlink()
            target.symlink_to(original)

    def test_settings_migrate_only_known_links(self):
        root = self.home / self.policy["pi"]["profiles_directory"]
        for profile, kind in zip(self.policy["pi"]["profiles"], ("managed", "dangling", "local")):
            target = root / profile / "settings.json"
            target.unlink()
            if kind == "managed":
                target.symlink_to(Path(self.policy["pi"]["settings_source"]) / profile / "settings.json")
            elif kind == "dangling":
                target.symlink_to(self.base / "missing")
            else:
                target.write_text("local settings\n")
        self.install("pi")
        migrated = root / "discussion/settings.json"
        self.assertFalse(migrated.is_symlink())
        self.assertTrue(migrated.stat().st_mode & 0o200)
        self.assertTrue((root / "builder/settings.json").is_symlink())
        self.assertEqual((root / "notes/settings.json").read_text(), "local settings\n")
        migrated.write_text("selected model\n")
        self.install("pi")
        self.assertEqual(migrated.read_text(), "selected model\n")

    def test_all_profile_resource_directories(self):
        for profile in self.policy["pi"]["profiles"]:
            root = self.home / self.policy["pi"]["profiles_directory"] / profile
            for directory in ("extensions", "skills", "prompts", "themes"):
                self.assertTrue((root / directory).is_dir())

    def test_legacy_alias_removal_is_exact_and_preserves_symlinks(self):
        legacy = self.policy["pi"]["legacy_wrapper"]
        target = self.home / legacy["destination"]
        for content in (legacy["content"], "exec pi\n#!/bin/sh\n# old fixture\n"):
            target.write_text(content)
            self.install("wrappers")
            self.assertFalse(target.exists())
        target.write_text(legacy["content"] + "# local\n")
        self.install("wrappers")
        self.assertTrue(target.exists())
        target.unlink()
        external = self.base / "local-alias"
        external.write_text(legacy["content"])
        target.symlink_to(external)
        self.install("wrappers")
        self.assertTrue(target.is_symlink())
        self.assertEqual(external.read_text(), legacy["content"])

    def test_policy_is_data_not_shell_code(self):
        policy = copy.deepcopy(self.policy)
        policy["pi"]["default_profile"] = "$(touch INJECTED)"
        policy["pi"]["profiles"].append(policy["pi"]["default_profile"])
        defaults = Path(policy["pi"]["settings_source"]) / policy["pi"]["default_profile"]
        defaults.mkdir()
        (defaults / "settings.json").write_text("{}")
        self.install("pi", policy)
        _, state = self.run_pi()
        self.assertTrue(state["profile"].endswith("/$(touch INJECTED)"))
        self.assertFalse((self.base / "INJECTED").exists())

    def test_invalid_policy_rejected_before_writes(self):
        variants = []
        for key in self.policy:
            policy = copy.deepcopy(self.policy)
            del policy[key]
            variants.append(policy)
        for key, value in (("wrapper", "../escape"), ("profiles_directory", "/absolute"),
                           ("profiles", ["a/b"]), ("default_profile", "missing"),
                           ("marker", "# fixture\nexec bad"), ("interpreter", "/bin/sh\nexec bad")):
            policy = copy.deepcopy(self.policy)
            policy["pi"][key] = value
            variants.append(policy)
        extra = copy.deepcopy(self.policy)
        extra["unknown"] = "field"
        variants.append(extra)
        wrapper = self.home / "bin/pi"
        before = wrapper.read_bytes()
        for policy in variants:
            self.install("shell", policy, success=False)
            self.assertEqual(wrapper.read_bytes(), before)

    def test_crlf_and_binary_markers_match_legacy_grep(self):
        wrapper = self.home / "bin/pi"
        custom = b"#!/bin/sh\n" + self.policy["pi"]["marker"].encode() + b"\r\n\xff\n"
        wrapper.write_bytes(custom)
        self.install("pi")
        self.assertEqual(wrapper.read_bytes(), custom)
        rc = self.home / "rc"
        rc.unlink()
        content = self.policy["bashrc"]["marker"].encode() + b"\r\n\xff\n"
        rc.write_bytes(content)
        self.install("shell")
        self.assertEqual(rc.read_bytes(), content + self.policy["bashrc"]["append"].encode())

    def test_settings_symlink_text_is_not_normalized(self):
        root = self.home / self.policy["pi"]["profiles_directory"]
        target = root / "discussion/settings.json"
        target.unlink()
        raw = self.policy["pi"]["settings_source"] + "/./discussion/settings.json"
        target.symlink_to(raw)
        self.install("pi")
        self.assertTrue(target.is_symlink())
        self.assertEqual(os.readlink(target), raw)

    def test_settings_copy_permissions_obey_umask(self):
        root = self.home / self.policy["pi"]["profiles_directory"]
        target = root / "discussion/settings.json"
        target.unlink()
        source = Path(self.policy["pi"]["settings_source"]) / "discussion/settings.json"
        source.chmod(0o755)
        result = subprocess.run([sys.executable, str(HELPER), str(self.policy_file), str(self.home), "pi"],
                                env=self.env, capture_output=True, text=True, umask=0o077)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(target.stat().st_mode & 0o777, 0o700)

    def test_missing_settings_source_is_an_error(self):
        target = self.home / self.policy["pi"]["profiles_directory"] / "discussion/settings.json"
        target.unlink()
        source = Path(self.policy["pi"]["settings_source"]) / "discussion/settings.json"
        source.unlink()
        self.install("pi", success=False)
        self.assertFalse(target.exists())

    def test_missing_inputs_and_bad_mode_fail(self):
        for args in ([], [str(self.policy_file)], [str(self.policy_file), str(self.home), "bad"]):
            result = subprocess.run([sys.executable, str(HELPER), *args], capture_output=True)
            self.assertNotEqual(result.returncode, 0)

    def test_state_sentinels_untouched(self):
        sentinels = [self.home / "state/auth.json", self.home / "state/session.jsonl", self.home / "state/history"]
        for target in sentinels:
            target.write_bytes(b"synthetic-state-sentinel\x00\n")
        for _ in range(2):
            for mode in ("shell", "pi", "wrappers"):
                self.install(mode)
        for target in sentinels:
            self.assertEqual(target.read_bytes(), b"synthetic-state-sentinel\x00\n")


if __name__ == "__main__":
    unittest.main()
