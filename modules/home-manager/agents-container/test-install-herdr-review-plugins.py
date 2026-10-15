"""Synthetic contracts only: no private policy, live Herdr, plugins or network."""
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
    "installer", Path(__file__).with_name("install-herdr-review-plugins.py"))
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class ReviewPluginsTest(unittest.TestCase):
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
            "plugins": ["fixture.alpha", "fixture.beta", "fixture.gamma"],
            "defaults": [{"plugin": "fixture.alpha", "text": '[picker]\nauto = false\n'},
                         {"plugin": "fixture.beta", "text": '# beta\nupdates = false\n'}],
            "bindings": [{"key": "prefix+x", "action": "fixture.alpha.open", "description": "A chooser"},
                         {"key": "prefix+y", "action": "fixture.beta.search", "description": "B chooser"},
                         {"key": "prefix+z", "action": "fixture.gamma.open", "description": "C chooser"}],
            "backup_suffix": ".before-fixture",
            "binding_comment": "# Fixture bindings.",
        }
        self.sources = ["/fixture/alpha with spaces", "/fixture/beta", "/fixture/gamma"]
        self.calls, self.plugins = [], []
        self.running = True
        self.check_fails = False
        fake = patch.object(installer, "herdr", self.herdr)
        fake.start()
        self.addCleanup(fake.stop)

    def seed(self, index=0):
        return self.root / "data" / self.policy["defaults"][index]["plugin"] / "config.toml"

    def herdr(self, *args):
        self.calls.append(args)
        if args == ("plugin", "list", "--json"):
            return json.dumps({"result": {"plugins": self.plugins}})
        if args[:2] == ("plugin", "config-dir"):
            return str(self.root / "data" / args[2]) + "\n"
        if args == ("status", "--json"):
            return json.dumps({"server": {"running": self.running}})
        if args[:2] == ("plugin", "link"):
            identifier = self.policy["plugins"][self.sources.index(args[2])]
            self.plugins = [p for p in self.plugins if p["plugin_id"] != identifier]
            self.plugins.append({"plugin_id": identifier, "plugin_root": args[2],
                                 "enabled": "--disabled" not in args})
        elif args == ("config", "check"):
            if self.check_fails:
                raise subprocess.CalledProcessError(1, ["herdr", *args])
        elif args != ("server", "reload-config"):
            raise AssertionError(args)
        return "{}"

    def install(self):
        installer.install(self.policy, self.config, self.sources)

    def seed_calls(self):
        return [("plugin", "config-dir", seed["plugin"]) for seed in self.policy["defaults"]]

    def test_fresh_repeat_backup_modes_and_call_order(self):
        self.install()
        saved = self.config.read_bytes()
        parsed = tomllib.loads(saved.decode())
        self.assertEqual(parsed["theme"], {"name": "local"})
        self.assertEqual(len(parsed["keys"]["command"]), 3)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o640)
        backup = self.config.with_name(self.config.name + ".before-fixture")
        self.assertEqual(backup.read_text(), self.original)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.calls, [("plugin", "list", "--json")]
                         + [("plugin", "link", s) for s in self.sources] + self.seed_calls()
                         + [("config", "check"), ("status", "--json"), ("server", "reload-config")])
        for index, seed in enumerate(self.policy["defaults"]):
            self.assertEqual(self.seed(index).read_text(), seed["text"])
        self.seed().write_text("# user's seed\n")
        self.calls.clear()
        self.install()
        self.assertEqual(self.calls, [("plugin", "list", "--json")] + self.seed_calls())
        self.assertEqual(self.config.read_bytes(), saved)
        self.assertEqual(self.seed().read_text(), "# user's seed\n")
        self.assertEqual(backup.read_text(), self.original)

    def test_occupied_builtin_nested_and_remapped_actions(self):
        for keys in ('[keys]\nnew_tab = "prefix+x, prefix+y prefix+z"\n',
                     '[keys.normal]\nfirst = "prefix+x"\nsecond = "prefix+y"\nthird = "prefix+z"\n',
                     '[[keys.command]]\nkey="prefix+x"\ntype="command"\ncommand="mine"\n'
                     '[[keys.command]]\nkey="prefix+q"\ntype="plugin_action"\ncommand="fixture.beta.search"\n'
                     '[[keys.command]]\nkey="prefix+w"\ntype="plugin_action"\ncommand="fixture.gamma.open"\n'):
            with self.subTest(keys=keys):
                self.config.write_text(self.original + keys)
                self.calls.clear()
                self.install()
                self.assertEqual(self.config.read_text(), self.original + keys)
                self.assertNotIn(("config", "check"), self.calls)
                self.assertNotIn(("status", "--json"), self.calls)

    def test_disabled_upgrade_and_unchanged_disabled(self):
        self.plugins = [{"plugin_id": "fixture.beta", "plugin_root": "/old", "enabled": False}]
        self.install()
        self.assertIn(("plugin", "link", "/fixture/beta", "--disabled"), self.calls)
        self.assertEqual(len(tomllib.loads(self.config.read_text())["keys"]["command"]), 2)
        self.assertTrue(self.seed(1).is_file())  # Disabled plugin still gets initial defaults.
        self.calls.clear()
        self.install()
        self.assertEqual(self.calls, [("plugin", "list", "--json")] + self.seed_calls())

    def test_missing_and_dangling_main_config_not_repaired(self):
        self.config.unlink()
        for dangling in (False, True):
            with self.subTest(dangling=dangling):
                if dangling:
                    self.config.symlink_to(self.root / "absent")
                self.calls.clear()
                self.install()
                self.assertFalse(self.config.exists())
                self.assertEqual(self.config.is_symlink(), dangling)
                self.assertNotIn(("config", "check"), self.calls)
                self.assertNotIn(("status", "--json"), self.calls)

    def test_valid_config_link_target_replaced_and_backed_up(self):
        target = self.root / "target.toml"
        self.config.rename(target)
        self.config.symlink_to(target)
        self.install()
        self.assertEqual(self.config.readlink(), target)
        self.assertTrue(target.with_name("target.toml.before-fixture").exists())
        self.assertEqual(target.stat().st_mode & 0o777, 0o640)
        self.assertIn("keys", tomllib.loads(target.read_text()))

    def test_seed_symlinks_preserved(self):
        for index in (0, 1):
            seed = self.seed(index)
            seed.parent.mkdir(parents=True)
            target = self.root / ("existing" if index == 0 else "missing")
            if index == 0:
                target.write_text("# local\n")
            seed.symlink_to(target)
        self.install()
        self.assertTrue(self.seed().is_symlink())
        self.assertEqual(self.seed().read_text(), "# local\n")
        self.assertTrue(self.seed(1).is_symlink())
        self.assertFalse(self.seed(1).exists())

    def test_existing_backup_is_not_overwritten(self):
        backup = self.config.with_name(self.config.name + self.policy["backup_suffix"])
        backup.write_text("# old backup\n")
        self.install()
        self.assertEqual(backup.read_text(), "# old backup\n")

    def test_malformed_user_toml_no_side_effects(self):
        self.config.write_text("[broken\n")
        with self.assertRaises(tomllib.TOMLDecodeError):
            self.install()
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.root.iterdir()), [self.config])

    def test_stopped_server_never_started(self):
        self.running = False
        self.install()
        self.assertEqual(self.calls[-2:], [("config", "check"), ("status", "--json")])
        self.assertFalse(any(call[0] == "server" for call in self.calls))

    def test_config_check_failure_prevents_status_reload_but_is_not_transactional(self):
        self.check_fails = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.install()
        self.assertNotIn(("status", "--json"), self.calls)
        self.assertNotIn(("server", "reload-config"), self.calls)
        self.assertIn("keys", tomllib.loads(self.config.read_text()))
        self.assertTrue(self.seed().exists())

    def test_empty_explicit_policy_has_no_implicit_defaults_or_bindings(self):
        self.policy.update(plugins=[], defaults=[], bindings=[])
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
        for field, value in (("unexpected", True), ("plugins", None), ("defaults", {}),
                             ("backup_suffix", "/bad"), ("binding_comment", "not a comment"),
                             ("defaults", [{"plugin": "fixture.alpha", "text": "[broken"}]),
                             ("defaults", [{"plugin": "unknown", "text": ""}]),
                             ("defaults", self.policy["defaults"] * 2),
                             ("plugins", ["duplicate", "duplicate"]),
                             ("bindings", [{"key": "x", "action": "unknown.open", "description": "x"}])):
            value_policy = copy.deepcopy(self.policy)
            value_policy[field] = value
            bad.append(value_policy)
        for policy in bad:
            with self.subTest(policy=policy), self.assertRaises((ValueError, tomllib.TOMLDecodeError)):
                installer.install(policy, self.config, self.sources)
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.root.iterdir()), [self.config])

    def test_relative_config_is_relative_to_cwd(self):
        installer.install(self.policy, os.path.relpath(self.config), self.sources)
        self.assertEqual(len(tomllib.loads(self.config.read_text())["keys"]["command"]), 3)

    def test_sources_required_in_policy_order(self):
        for sources in ([], ["/one"], ["/one", "relative", "/three"], ["/a"] * 4):
            with self.subTest(sources=sources), self.assertRaises(ValueError):
                installer.install(self.policy, self.config, sources)
        self.assertEqual(self.calls, [])

    def test_empty_config_path_rejected(self):
        with self.assertRaises(ValueError):
            installer.install(self.policy, "", self.sources)
        self.assertEqual(self.calls, [])

    def test_binding_strings_are_toml_escaped(self):
        self.policy["bindings"][0]["description"] = 'A "quoted" \\ choice\nline'
        self.install()
        commands = tomllib.loads(self.config.read_text())["keys"]["command"]
        self.assertEqual(commands[0]["description"], self.policy["bindings"][0]["description"])

    def test_cli_missing_arguments_cannot_discover_home(self):
        for args in ([], ["missing-policy.json"]):
            result = subprocess.run([sys.executable, installer.__file__, *args], cwd=self.root,
                                    env={"PATH": "", "HOME": str(self.root)}, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("usage:", result.stderr)
        self.assertEqual(list(self.root.iterdir()), [self.config])


if __name__ == "__main__":
    unittest.main()
