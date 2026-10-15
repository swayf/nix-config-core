"""Synthetic policy, fake exec, hook and filesystem contracts; no live application."""

from pathlib import Path
from unittest.mock import patch
import copy
import importlib.util
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("claude-profile.py")
SPEC = importlib.util.spec_from_file_location("profiles", SCRIPT)
profiles = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profiles)


def fixture(root):
    return {
        "state_dir": str(root / "state records"),
        "settings_path": str(root / "config/settings.json"),
        "wrapper_path": str(root / "bin/client"),
        "executable": "/synthetic/client", "argv0": "fixture-client", "python": sys.executable,
        "profile_env": "FIXTURE_PRESET", "default_profile": "plain",
        "presets": {"plain": [], "fast": ["--allow-fixture"],
                    "note": ["--allow-fixture", "--note-file", "fixture note"]},
        "selection_rules": [
            {"profile": "note", "flags": ["--allow-fixture"], "options": {"--note-file": "fixture note"}},
            {"profile": "fast", "flags": ["--allow-fixture"], "options": {}},
        ],
        "explicit_flags": ["--allow-fixture"],
        "explicit_options": ["--note-file", "--note", "--mode"],
        "resume_options": ["--resume", "-r"],
        "legacy_scripts": ["/fixture/old-profile.py"],
        "legacy_store_suffixes": ["-fixture-etc/etc/profile.py"],
    }


class ClaudeProfileTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "alice with ' quotes"
        self.root.mkdir()
        self.home = self.root / "unrelated home"
        self.home.mkdir()
        self.sentinels = {}
        for name in ("auth.json", "sessions/id.json", "settings.json", "SYSTEM.md", "preferences.json"):
            path = self.home / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("unrelated fixture: " + name)
            self.sentinels[path] = path.read_bytes()
        self.addCleanup(self.check_sentinels)
        env = {"HOME": str(self.home), "XDG_STATE_HOME": str(self.home / "wrong-state"),
               "CLAUDE_CONFIG_DIR": str(self.home / "wrong-config"), "PATH": "",
               "UNRELATED_VARIABLE": "preserved", "PYTHONDONTWRITEBYTECODE": "1"}
        self.environment = patch.dict(os.environ, env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.policy = fixture(self.root)
        self.policy_path = self.root / "policy ' file.json"
        self.save_policy()

    def check_sentinels(self):
        for path, content in self.sentinels.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertFalse((self.home / "wrong-state").exists())
        self.assertFalse((self.home / "wrong-config").exists())

    def save_policy(self):
        self.policy_path.write_text(json.dumps(self.policy))

    def cli(self, *args, script=SCRIPT, **kwargs):
        return subprocess.run([sys.executable, str(script), *map(str, args)],
                              capture_output=True, text=True, timeout=10, **kwargs)

    def launch(self, *args):
        with patch.object(profiles.os, "execve") as execute:
            profiles.launch(self.policy, list(args))
        executable, argv, env = execute.call_args.args
        self.assertEqual(executable, self.policy["executable"])
        self.assertEqual(argv[0], self.policy["argv0"])
        self.assertEqual(env["UNRELATED_VARIABLE"], "preserved")
        return argv[1:], env[self.policy["profile_env"]]

    def record(self, session_id, profile):
        with patch.dict(os.environ, {self.policy["profile_env"]: profile}), patch.object(
            profiles.sys, "stdin", io.StringIO(json.dumps({"session_id": session_id}))
        ):
            profiles.record(self.policy)

    def install(self):
        profiles.install(self.policy, SCRIPT.absolute(), self.policy_path)

    def test_round_trip_independent_sessions_and_repeated_restore(self):
        for name, flags in self.policy["presets"].items():
            self.assertEqual(self.launch(*flags, "prompt with spaces"), ([*flags, "prompt with spaces"], name))
            self.record(name, name)
        for _ in range(3):
            for name, flags in self.policy["presets"].items():
                self.assertEqual(self.launch("--resume", name), ([*flags, "--resume", name], name))
                self.record(name, name)

    def test_explicit_options_win_including_equals_forms(self):
        self.record("id", "note")
        for flag in ("--note-file", "--note", "--mode"):
            for args in ([flag, "custom", "--resume", "id"], [flag + "=custom", "--resume=id"]):
                self.assertEqual(self.launch(*args), (args, "plain"))
        args = ["--allow-fixture", "--resume", "id"]
        self.assertEqual(self.launch(*args), (args, "fast"))

    def test_resume_forms_and_separator(self):
        self.record("id", "note")
        for args in (["--resume=id"], ["-r", "id"], ["-r=id"]):
            self.assertEqual(self.launch(*args), ([*self.policy["presets"]["note"], *args], "note"))
        for args in ([], ["--continue"], ["--resume"], ["--resume", "--mode"],
                     ["--resume", "unknown"], ["--", "--resume", "id"], ["prompt --resume id"]):
            self.assertEqual(self.launch(*args), (args, "plain"))
        args = ["--allow-fixture", "--note-file=fixture note"]
        self.assertEqual(self.launch(*args), (args, "note"))

    def test_flag_matching_is_exact_and_rules_use_first_match(self):
        self.record("id", "note")
        args = ["--allow-fixture=true", "--resume", "id"]
        self.assertEqual(self.launch(*args), ([*self.policy["presets"]["note"], *args], "note"))
        self.policy["selection_rules"].reverse()
        flags = self.policy["presets"]["note"]
        self.assertEqual(self.launch(*flags), (flags, "fast"))

    def test_duplicate_current_hooks_are_collapsed(self):
        self.install()
        settings = Path(self.policy["settings_path"])
        saved = json.loads(settings.read_text())
        saved["hooks"]["SessionStart"] *= 3
        settings.write_text(json.dumps(saved))
        self.install()
        self.assertEqual(len(json.loads(settings.read_text())["hooks"]["SessionStart"]), 1)

    def test_malformed_settings_are_not_replaced(self):
        settings = Path(self.policy["settings_path"])
        settings.parent.mkdir()
        settings.write_text("not json")
        result = self.cli(self.policy_path, "install")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(settings.read_text(), "not json")
        self.assertFalse(Path(self.policy["wrapper_path"]).exists())

    def test_bad_state_never_adds_flags(self):
        path = profiles.state_path(self.policy, "id")
        path.parent.mkdir()
        for text in ("invalid", "[]", "null", '{"profile": "unknown"}', '{"profile": []}'):
            path.write_text(text)
            self.assertEqual(self.launch("--resume", "id"), (["--resume", "id"], "plain"))

    def test_record_invalid_events_are_silent(self):
        for text in ("invalid", "[]", "null", "{}", '{"session_id": []}', '{"session_id": ""}'):
            with patch.object(profiles.sys, "stdin", io.StringIO(text)):
                profiles.record(self.policy)
        self.record("id", "unknown")
        self.assertFalse(Path(self.policy["state_dir"]).exists())

    def test_record_is_private_atomic_and_session_cannot_escape(self):
        self.record("../../outside", "fast")
        path = profiles.state_path(self.policy, "../../outside")
        self.assertEqual(path.parent, Path(self.policy["state_dir"]))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(path.read_text()), {"profile": "fast"})
        self.record("../../outside", "note")
        self.assertEqual(json.loads(path.read_text()), {"profile": "note"})
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_record_write_failure_is_silent(self):
        with patch.object(profiles, "atomic_write", side_effect=OSError("fixture failure")):
            self.record("id", "note")

    def test_atomic_write_failure_cleans_temporary(self):
        path = self.root / "atomic/file"
        with patch.object(profiles.os, "replace", side_effect=OSError("fixture failure")):
            with self.assertRaises(OSError):
                profiles.atomic_write(path, "text")
        self.assertEqual(list(path.parent.iterdir()), [])

    def test_install_preserves_settings_and_migrates_only_selected_hooks(self):
        settings = Path(self.policy["settings_path"])
        settings.parent.mkdir()
        legacy = [
            {"type": "command", "command": shlex.join([sys.executable, path, "record"])}
            for path in ("/fixture/old-profile.py", "/nix/store/old-fixture-etc/etc/profile.py")
        ]
        unrelated = [
            {"type": "command", "command": shlex.join([sys.executable, "/custom/profile.py", "record"])},
            {"type": "command", "command": shlex.join([sys.executable, str(SCRIPT.absolute()), "another-policy", "record"])},
            {"type": "command", "command": "echo 'unterminated"},
            {"type": "prompt", "command": legacy[0]["command"]},
        ]
        mixed = {"matcher": "startup", "custom": True, "hooks": [legacy[1], *unrelated]}
        settings.write_text(json.dumps({"model": "fixture", "hooks": {
            "SessionStart": [{"hooks": [legacy[0]]}, mixed], "Stop": [{"hooks": legacy}],
        }}))
        self.install()
        saved = json.loads(settings.read_text())
        self.assertEqual(saved["model"], "fixture")
        self.assertEqual(saved["hooks"]["Stop"], [{"hooks": legacy}])
        self.assertEqual(saved["hooks"]["SessionStart"][0], dict(mixed, hooks=unrelated))
        hook = saved["hooks"]["SessionStart"][1]["hooks"][0]
        self.assertEqual(shlex.split(hook["command"]), [sys.executable, str(SCRIPT.absolute()), str(self.policy_path), "record"])
        before = settings.read_bytes()
        for _ in range(3):
            self.install()
        self.assertEqual(settings.read_bytes(), before)

    def test_actual_wrapper_hook_arguments_environment_and_exit_status(self):
        fake = self.root / "fake client"
        fake.write_text(f"#!{sys.executable}\nimport json, os, sys\n"
                        "print(json.dumps({'args': sys.argv[1:], 'profile': os.environ.get('FIXTURE_PRESET'), "
                        "'unrelated': os.environ.get('UNRELATED_VARIABLE')}))\nsys.exit(17)\n")
        fake.chmod(0o755)
        self.policy["executable"] = str(fake)
        self.save_policy()
        result = self.cli(self.policy_path, "install")
        self.assertEqual(result.returncode, 0, result.stderr)
        settings = json.loads(Path(self.policy["settings_path"]).read_text())
        hook = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        result = subprocess.run(shlex.split(hook), input='{"session_id":"id"}', text=True,
                                capture_output=True, env=dict(os.environ, FIXTURE_PRESET="note"), timeout=10)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        args = ["--resume", "id", "prompt with ' quotes", "--", "literal"]
        result = subprocess.run([self.policy["wrapper_path"], *args], text=True,
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "args": [*self.policy["presets"]["note"], *args], "profile": "note", "unrelated": "preserved",
        })

    def test_install_preserves_user_wrapper_file_directory_and_symlinks(self):
        wrapper = Path(self.policy["wrapper_path"])
        wrapper.parent.mkdir()
        wrapper.write_text("#!/bin/sh\n# custom\n")
        self.install()
        self.assertEqual(wrapper.read_text(), "#!/bin/sh\n# custom\n")
        wrapper.unlink()
        wrapper.mkdir()
        self.install()
        self.assertTrue(wrapper.is_dir())
        wrapper.rmdir()
        target = self.root / "user wrapper"
        target.write_text("custom")
        for dest in (target, self.root / "absent"):
            wrapper.symlink_to(dest)
            self.install()
            self.assertTrue(wrapper.is_symlink())
            self.assertEqual(wrapper.readlink(), dest)
            wrapper.unlink()
        self.assertEqual(target.read_text(), "custom")

    def test_managed_wrapper_replaced_and_executable(self):
        self.install()
        wrapper = Path(self.policy["wrapper_path"])
        installed = wrapper.read_text()
        wrapper.write_text("#!/bin/sh\n" + profiles.MARKER + "\n# stale\n")
        self.install()
        self.assertEqual(wrapper.read_text(), installed)
        self.assertEqual(wrapper.stat().st_mode & 0o777, 0o755)

    def test_logical_script_and_policy_symlinks_survive_image_replacement(self):
        script = self.root / "etc/profile.py"
        policy_path = self.root / "etc/policy.json"
        script.parent.mkdir()
        snapshots = []
        for version in ("old", "new"):
            image = self.root / version
            image.mkdir()
            shutil.copyfile(SCRIPT, image / "profile.py")
            (image / "policy.json").write_text(json.dumps(self.policy))
            for link, target in ((script, image / "profile.py"), (policy_path, image / "policy.json")):
                if link.is_symlink():
                    link.unlink()
                link.symlink_to(target)
            result = self.cli(policy_path, "install", script=script)
            self.assertEqual(result.returncode, 0, result.stderr)
            snapshots.append((Path(self.policy["settings_path"]).read_bytes(), Path(self.policy["wrapper_path"]).read_bytes()))
        self.assertEqual(*snapshots)
        shutil.rmtree(self.root / "old")
        hook = json.loads(snapshots[1][0])["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        result = subprocess.run(shlex.split(hook), input='{"session_id":"image"}', text=True,
                                capture_output=True, env=dict(os.environ, FIXTURE_PRESET="note"), timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.launch("--resume", "image")[1], "note")

    def test_all_policy_fields_required_and_unknown_fields_rejected(self):
        for key in self.policy:
            policy = dict(self.policy)
            del policy[key]
            self.policy_path.write_text(json.dumps(policy))
            with self.subTest(missing=key), self.assertRaises(ValueError):
                profiles.load_policy(self.policy_path)
        self.policy_path.write_text(json.dumps(dict(self.policy, unknown=True)))
        with self.assertRaises(ValueError):
            profiles.load_policy(self.policy_path)

    def test_policy_types_and_references_rejected(self):
        changes = [(key, value) for key in ("state_dir", "python", "executable") for value in ("", None, [], "a\0b")]
        changes += [("presets", {}), ("presets", {"x": "flag"}), ("default_profile", "unknown"),
                    ("default_profile", []), ("profile_env", "NOT-ENV"), ("python", "relative/python"),
                    ("executable", "relative"), ("resume_options", ["--"]), ("explicit_options", ["--x=y"]),
                    ("legacy_scripts", "not-list"), ("selection_rules", {}),
                    ("selection_rules", [{"profile": "unknown", "flags": [], "options": {}}]),
                    ("selection_rules", [{"profile": "plain", "flags": [], "options": {"--x": []}}]),
                    ("selection_rules", [{"profile": "plain", "flags": []}])]
        for key, value in changes:
            policy = copy.deepcopy(self.policy)
            policy[key] = value
            self.policy_path.write_text(json.dumps(policy))
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                profiles.load_policy(self.policy_path)

    def test_cli_missing_invalid_inputs_do_not_write(self):
        for args in ([], [self.policy_path], ["", "install"], [self.policy_path, "unknown"],
                     [self.policy_path, "install", "extra"], [self.root / "absent", "install"]):
            self.assertNotEqual(self.cli(*args).returncode, 0)
        self.policy_path.write_text("{}")
        self.assertNotEqual(self.cli(self.policy_path, "install").returncode, 0)
        self.assertNotEqual(self.cli(self.policy_path, "run").returncode, 0)
        self.assertFalse(Path(self.policy["settings_path"]).exists())
        self.assertFalse(Path(self.policy["wrapper_path"]).exists())

    def test_record_missing_or_invalid_policy_is_silent_success(self):
        for text in ("invalid", "{}", "[]"):
            self.policy_path.write_text(text)
            result = self.cli(self.policy_path, "record", input="private fixture event")
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        result = self.cli(self.root / "absent", "record", input="private fixture event")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_relative_paths_are_policy_based_without_home_expansion(self):
        for key in ("state_dir", "settings_path", "wrapper_path"):
            self.policy[key] = "literal~/" + key
        self.save_policy()
        policy = profiles.load_policy(self.policy_path)
        for key in ("state_dir", "settings_path", "wrapper_path"):
            self.assertEqual(policy[key], str(self.policy_path.parent / "literal~" / key))

    def test_relative_policy_destinations_survive_wrapper_cwd_change(self):
        fake = self.root / "fake client"
        fake.write_text(f"#!{sys.executable}\nimport os\nprint(os.environ['FIXTURE_PRESET'])\n")
        fake.chmod(0o755)
        self.policy.update(executable=str(fake), state_dir="relative-state", settings_path="relative-config/settings.json",
                           wrapper_path="relative-bin/client")
        self.save_policy()
        result = self.cli(self.policy_path, "install", cwd=self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        policy = profiles.load_policy(self.policy_path)
        with patch.dict(os.environ, FIXTURE_PRESET="note"), patch.object(
            profiles.sys, "stdin", io.StringIO('{"session_id":"id"}')
        ):
            profiles.record(policy)
        result = subprocess.run([str(self.root / "relative-bin/client"), "--resume", "id"],
                                cwd=self.home, capture_output=True, text=True, timeout=10)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "note\n", ""))
        self.assertFalse((self.home / "relative-state").exists())

    def test_empty_policy_choices_are_not_filled_in(self):
        self.policy.update(selection_rules=[], explicit_flags=[], explicit_options=[], resume_options=[],
                           legacy_scripts=[], legacy_store_suffixes=[])
        self.save_policy()
        policy = profiles.load_policy(self.policy_path)
        self.record("id", "note")
        with patch.object(profiles.os, "execve") as execute:
            profiles.launch(policy, ["--resume", "id", "--allow-fixture"])
        self.assertEqual(execute.call_args.args[1], ["fixture-client", "--resume", "id", "--allow-fixture"])
        self.assertEqual(execute.call_args.args[2]["FIXTURE_PRESET"], "plain")


if __name__ == "__main__":
    unittest.main()
