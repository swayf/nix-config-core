"""Synthetic explicit-policy contracts. Herdr is mocked; no runtime/downloads."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "navigation", Path(__file__).with_name("install-herdr-navigation.py"))
navigation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(navigation)


class NavigationTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "config with spaces"
        self.root.mkdir()
        self.config = self.root / "user.toml"
        self.original = '# user content\n[theme]\nname = "local"\n'
        self.config.write_text(self.original)
        self.config.chmod(0o640)
        self.policy = {
            "plugins": ["fixture.alpha", "fixture.beta"],
            "directories": ["plugin-data/alpha/items"],
            "seeds": [{"path": "plugin-data/beta/config.toml", "text": '[picker]\nauto = false\n'}],
            "bindings": [{"key": "prefix+x", "action": "fixture.alpha.open", "description": "A chooser"},
                         {"key": "prefix+y", "action": "fixture.beta.search", "description": "B chooser"}],
            "backup_suffix": ".before-fixture",
            "binding_comment": "# Fixture bindings.",
        }
        self.sources = ["/fixture/alpha with spaces", "/fixture/beta"]
        self.calls, self.plugins = [], []
        self.running = True
        self.fake = patch.object(navigation, "herdr", self.herdr)
        self.fake.start()
        self.addCleanup(self.fake.stop)

    def herdr(self, *args):
        self.calls.append(args)
        if args == ("plugin", "list", "--json"):
            return json.dumps({"result": {"plugins": self.plugins}})
        if args == ("status", "--json"):
            return json.dumps({"server": {"running": self.running}})
        if args[:2] == ("plugin", "link"):
            identifier = self.policy["plugins"][self.sources.index(args[2])]
            self.plugins = [p for p in self.plugins if p["plugin_id"] != identifier]
            self.plugins.append({"plugin_id": identifier, "plugin_root": args[2],
                                 "enabled": "--disabled" not in args})
        return "{}"

    def install(self):
        navigation.install(self.policy, self.root, self.config, self.sources)

    def test_fresh_repeat_backup_and_local_seed(self):
        self.install()
        saved = self.config.read_bytes()
        parsed = tomllib.loads(saved.decode())
        self.assertEqual(parsed["theme"], {"name": "local"})
        self.assertEqual(len(parsed["keys"]["command"]), 2)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o640)
        backup = self.config.with_name(self.config.name + ".before-fixture")
        self.assertEqual(backup.read_text(), self.original)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        seed = self.root / self.policy["seeds"][0]["path"]
        seed.write_text("# user's seed\n")
        self.calls.clear()
        self.install()
        self.assertEqual(self.calls, [("plugin", "list", "--json")])
        self.assertEqual(self.config.read_bytes(), saved)
        self.assertEqual(seed.read_text(), "# user's seed\n")
        self.assertEqual(backup.read_text(), self.original)

    def test_occupied_builtin_nested_and_remapped_actions(self):
        for keys in ('[keys]\nnew_tab = "prefix+x, prefix+y"\n',
                     '[keys.normal]\nfirst = "prefix+x"\nsecond = "prefix+y"\n',
                     '[[keys.command]]\nkey="prefix+x"\ntype="command"\ncommand="mine"\n'
                     '[[keys.command]]\nkey="prefix+z"\ntype="plugin_action"\ncommand="fixture.beta.search"\n'):
            with self.subTest(keys=keys):
                self.config.write_text(self.original + keys)
                self.calls.clear()
                self.install()
                self.assertEqual(self.config.read_text(), self.original + keys)
                self.assertNotIn(("status", "--json"), self.calls)

    def test_disabled_upgrade_and_unchanged_disabled(self):
        self.plugins = [{"plugin_id": "fixture.beta", "plugin_root": "/old", "enabled": False}]
        self.install()
        self.assertIn(("plugin", "link", "/fixture/beta", "--disabled"), self.calls)
        self.assertEqual(len(tomllib.loads(self.config.read_text())["keys"]["command"]), 1)
        self.calls.clear()
        self.install()
        self.assertEqual(self.calls, [("plugin", "list", "--json")])

    def test_dangling_or_missing_config_not_repaired(self):
        self.config.unlink()
        for dangling in (False, True):
            with self.subTest(dangling=dangling):
                if dangling:
                    self.config.symlink_to(self.root / "absent")
                self.calls.clear()
                self.install()
                self.assertFalse(self.config.exists())
                self.assertEqual(self.config.is_symlink(), dangling)
                self.assertNotIn(("status", "--json"), self.calls)

    def test_valid_config_link_target_replaced_and_backed_up(self):
        target = self.root / "target.toml"
        self.config.rename(target)
        self.config.symlink_to(target)
        self.install()
        self.assertEqual(self.config.readlink(), target)
        self.assertTrue(target.with_name("target.toml.before-fixture").exists())
        self.assertIn("keys", tomllib.loads(target.read_text()))

    def test_dangling_seed_preserved(self):
        seed = self.root / self.policy["seeds"][0]["path"]
        seed.parent.mkdir(parents=True)
        seed.symlink_to(self.root / "missing-seed")
        self.install()
        self.assertTrue(seed.is_symlink())
        self.assertFalse(seed.exists())

    def test_malformed_user_toml_no_side_effects(self):
        self.config.write_text("[broken\n")
        with self.assertRaises(tomllib.TOMLDecodeError):
            self.install()
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.root.iterdir()), [self.config])

    def test_stopped_server_never_started(self):
        self.running = False
        self.install()
        self.assertIn(("status", "--json"), self.calls)
        self.assertFalse(any(call[0] == "server" for call in self.calls))

    def test_empty_explicit_policy_has_no_implicit_seeds_or_bindings(self):
        self.policy.update(plugins=[], directories=[], seeds=[], bindings=[])
        self.sources = []
        self.install()
        self.assertEqual(self.calls, [("plugin", "list", "--json")])
        self.assertEqual(list(self.root.iterdir()), [self.config])
        self.assertEqual(self.config.read_text(), self.original)

    def test_invalid_policy_rejected_before_any_call(self):
        bad = []
        for field in self.policy:
            value = copy.deepcopy(self.policy)
            del value[field]
            bad.append(value)
        for field, value in (("unexpected", True), ("plugins", None),
                             ("directories", ["../escape"]), ("directories", ["/absolute"]),
                             ("backup_suffix", "/bad"), ("binding_comment", "not a comment"),
                             ("seeds", [{"path": "seed.toml", "text": "[broken"}]),
                             ("plugins", ["duplicate", "duplicate"]),
                             ("bindings", [{"key": "x", "action": "unknown.open", "description": "x"}])):
            value_policy = copy.deepcopy(self.policy)
            value_policy[field] = value
            bad.append(value_policy)
        for policy in bad:
            with self.subTest(policy=policy), self.assertRaises((ValueError, tomllib.TOMLDecodeError)):
                navigation.install(policy, self.root, self.config, self.sources)
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.root.iterdir()), [self.config])

    def test_explicit_relative_paths_are_relative_to_cwd(self):
        navigation.install(self.policy, os.path.relpath(self.root), os.path.relpath(self.config), self.sources)
        self.assertEqual(len(tomllib.loads(self.config.read_text())["keys"]["command"]), 2)
        self.assertTrue((self.root / "plugin-data/alpha/items").is_dir())

    def test_sources_required_in_policy_order(self):
        for sources in ([], ["/one"], ["/one", "relative"], ["/one", "/two", "/three"]):
            with self.subTest(sources=sources), self.assertRaises(ValueError):
                navigation.install(self.policy, self.root, self.config, sources)
        self.assertEqual(self.calls, [])

    def test_binding_strings_are_toml_escaped(self):
        self.policy["bindings"][0]["description"] = 'A "quoted" \\ choice\nline'
        self.install()
        commands = tomllib.loads(self.config.read_text())["keys"]["command"]
        self.assertEqual(commands[0]["description"], self.policy["bindings"][0]["description"])

    def test_cli_missing_arguments_cannot_discover_home(self):
        result = subprocess.run([sys.executable, navigation.__file__], cwd=self.root,
                                env={"PATH": "", "HOME": str(self.root)}, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("usage:", result.stderr)
        self.assertEqual(list(self.root.iterdir()), [self.config])


if __name__ == "__main__":
    unittest.main()
