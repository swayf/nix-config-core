"""Synthetic explicit-policy contracts; all Herdr calls mocked, no payloads."""
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

spec = importlib.util.spec_from_file_location("installer", Path(__file__).with_name("install-herdr-radar.py"))
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def values(**kwargs):
    return [{"key": key, "value": value} for key, value in kwargs.items()]


class RadarTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.config = self.home / "main/config.toml"
        self.plugin_config = self.home / "plugin/config.toml"
        self.policy = {
            "plugin": "fixture.panel", "literal_tables": ["layout"],
            "main_values": [{"table": "palette", "values": values(style='"fixture"', automatic="false")}],
            "plugin_seed": '# initial\nvariant = "plain"\n',
            "plugin_values": values(follow="false", label='"fixture"'),
            "backup_suffix": ".before-fixture",
            "actions": {"stop": "halt", "configure": "apply", "start": "begin"},
        }
        self.source = "/fixture/plugin with spaces"
        self.calls, self.plugins = [], []
        self.running, self.fail = False, None
        fake = patch.object(installer, "herdr", self.herdr)
        fake.start()
        self.addCleanup(fake.stop)

    def herdr(self, *args):
        self.calls.append(args)
        if args == ("plugin", "list", "--json"):
            return json.dumps({"result": {"plugins": self.plugins}})
        if args == ("status", "--json"):
            return json.dumps({"server": {"running": self.running}})
        if args[:2] == ("plugin", "link"):
            self.current(args[2], "--disabled" not in args)
            return "{}"
        if args[:3] == ("plugin", "action", "invoke"):
            self.action = args[3]
            return json.dumps({"result": {"log": {"log_id": "selected"}}})
        if args == ("plugin", "log", "list", "--plugin", "fixture.panel", "--limit", "100"):
            return json.dumps({"result": {"logs": [{"log_id": "unrelated", "status": "failed"},
                {"log_id": "selected", "status": "failed" if self.action == self.fail else "succeeded"}]}})
        raise AssertionError(args)

    def current(self, source=None, enabled=True):
        self.plugins = [{"plugin_id": self.policy["plugin"], "plugin_root": source or self.source, "enabled": enabled}]

    def install(self):
        installer.install(self.policy, self.config, self.plugin_config, self.source)

    def actions(self):
        return [c[-1] for c in self.calls if c[:3] == ("plugin", "action", "invoke")]

    def seed(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(0o640)

    def test_offline_fresh_and_repeat(self):
        self.install()
        self.assertEqual(tomllib.loads(self.config.read_text()), {"layout": {}, "palette": {"style": "fixture", "automatic": False}})
        self.assertEqual(tomllib.loads(self.plugin_config.read_text()), {"variant": "plain", "follow": False, "label": "fixture"})
        self.assertEqual(self.actions(), [])
        saved = (self.config.read_bytes(), self.plugin_config.read_bytes())
        self.calls.clear()
        self.install()
        self.assertEqual(saved, (self.config.read_bytes(), self.plugin_config.read_bytes()))
        self.assertEqual(self.calls, [("plugin", "list", "--json"), ("status", "--json")])
        self.assertFalse(self.config.with_name("config.toml.before-fixture").exists())

    def test_owned_keys_only_once_backup_and_modes(self):
        original = '[layout]\nmouse = false\n[palette] # local\nstyle = "local"\nautomatic = true\n[palette.custom]\naccent = "pink"\n'
        plugin = 'variant = "custom"\nfollow = true\n[colors]\nline = "red"\n'
        self.seed(self.config, original)
        self.seed(self.plugin_config, plugin)
        self.running = True
        self.current()
        self.install()
        self.assertEqual(self.actions(), ["fixture.panel.halt", "fixture.panel.apply", "fixture.panel.begin"])
        for path, text in ((self.config, original), (self.plugin_config, plugin)):
            backup = path.with_name(path.name + self.policy["backup_suffix"])
            self.assertEqual(backup.read_text(), text)
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)
        self.assertEqual(tomllib.loads(self.config.read_text())["palette"]["custom"], {"accent": "pink"})
        self.assertEqual(tomllib.loads(self.plugin_config.read_text())["variant"], "custom")
        self.calls.clear()
        self.install()
        self.assertEqual(self.actions(), ["fixture.panel.begin"])

    def test_upgrade_serial_actions(self):
        self.install()
        self.current("/old")
        self.running = True
        self.calls.clear()
        self.install()
        self.assertEqual(self.actions(), ["fixture.panel.halt", "fixture.panel.apply", "fixture.panel.begin"])

    def test_disabled_current_and_upgrade(self):
        for source in (self.source, "/old"):
            with self.subTest(source=source):
                self.current(source, False)
                self.running = True
                self.calls.clear()
                self.install()
                self.assertEqual(self.actions(), [])
                self.assertNotIn(("status", "--json"), self.calls)
                self.assertEqual(("plugin", "link", self.source, "--disabled") in self.calls, source != self.source)

    def test_new_plugin_running_already_prepared_only_starts(self):
        self.install()
        self.plugins = []
        self.running = True
        self.calls.clear()
        self.install()
        self.assertEqual(self.actions(), ["fixture.panel.begin"])

    def test_stop_and_configure_failure_prevent_later_actions(self):
        for action in ("halt", "apply"):
            with self.subTest(action=action):
                self.current("/old")
                self.running = True
                self.fail = "fixture.panel." + action
                self.calls.clear()
                with self.assertRaises(RuntimeError):
                    self.install()
                expected = ["fixture.panel.halt"] + (["fixture.panel.apply"] if action == "apply" else [])
                self.assertEqual(self.actions(), expected)

    def test_poll_waits_for_matching_success(self):
        replies = [json.dumps({"result": {"log": {"log_id": "id"}}}),
                   json.dumps({"result": {"logs": [{"log_id": "other", "status": "succeeded"}]}}),
                   json.dumps({"result": {"logs": [{"log_id": "id", "status": "running"}]}}),
                   json.dumps({"result": {"logs": [{"log_id": "id", "status": "succeeded"}]}})]
        with patch.object(installer, "herdr", side_effect=replies), patch.object(installer.time, "sleep") as sleep:
            installer.invoke("fixture.panel", "halt")
        self.assertEqual(sleep.call_count, 2)
        sleep.assert_called_with(0.2)

    def test_timeout_without_real_wait(self):
        with patch.object(installer.time, "monotonic", side_effect=[0, 61]):
            with self.assertRaises(TimeoutError):
                installer.invoke("fixture.panel", "halt")
        self.assertEqual(self.actions(), ["fixture.panel.halt"])

    def test_symlinks_preserve_targets_modes_and_link_local_backups(self):
        for path in (self.config, self.plugin_config):
            target = self.home / (path.parent.name + "-target")
            self.seed(target, '# local\n')
            path.parent.mkdir()
            path.symlink_to(target)
        self.install()
        for path in (self.config, self.plugin_config):
            self.assertTrue(path.is_symlink())
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)
            self.assertEqual(path.with_name(path.name + ".before-fixture").read_text(), '# local\n')

    def test_both_invalid_or_dangling_files_prevent_all_writes(self):
        for bad in (self.config, self.plugin_config):
            for mode in ("invalid", "dangling"):
                with self.subTest(path=bad, mode=mode):
                    for path in (self.config, self.plugin_config):
                        path.unlink(missing_ok=True)
                        self.seed(path, '# untouched\n')
                    if mode == "invalid":
                        bad.write_text("not TOML!\n")
                    else:
                        bad.unlink()
                        bad.symlink_to(self.home / "missing")
                    before = [(p.is_symlink(), os.readlink(p) if p.is_symlink() else p.read_bytes()) for p in (self.config, self.plugin_config)]
                    with self.assertRaises((ValueError, tomllib.TOMLDecodeError)):
                        self.install()
                    self.assertEqual(before, [(p.is_symlink(), os.readlink(p) if p.is_symlink() else p.read_bytes()) for p in (self.config, self.plugin_config)])
                    self.assertEqual(self.calls, [])
                    self.assertEqual(list(self.home.rglob("*.before-fixture")), [])

    def test_inline_dotted_and_commented_literal_table_conflicts_refused(self):
        for text in ('layout = { mouse = true }\n', 'palette = { style = "local" }\n',
                     '[layout] # local\nmouse = true\n', 'palette.style = "local"\n'):
            with self.subTest(text=text):
                self.seed(self.config, text)
                with self.assertRaises(tomllib.TOMLDecodeError):
                    self.install()
                self.assertEqual(self.config.read_text(), text)
                self.assertEqual(self.calls, [])

    def test_existing_backup_not_overwritten(self):
        self.seed(self.config, '# local\n')
        backup = self.config.with_name(self.config.name + ".before-fixture")
        backup.write_text("previous backup")
        self.install()
        self.assertEqual(backup.read_text(), "previous backup")

    def test_invalid_policy_rejected_before_side_effects(self):
        bad = [None, {}, {**self.policy, "extra": True}]
        for field in self.policy:
            item = copy.deepcopy(self.policy)
            del item[field]
            bad.append(item)
        for field, value in (("plugin", ""), ("literal_tables", ["a]", "b"]),
                             ("main_values", [{"table": "x", "values": values(x="[invalid")}]),
                             ("plugin_seed", "broken!"), ("backup_suffix", "/escape"),
                             ("actions", {"stop": "halt"}), ("plugin_values", values(x="1\ny=2")),
                             ("plugin_values", [{"key": "x.y", "value": "1"}]),
                             ("plugin_values", values(x="1") * 2)):
            bad.append({**self.policy, field: value})
        for policy in bad:
            with self.subTest(policy=policy):
                with self.assertRaises(ValueError):
                    installer.install(policy, self.config, self.plugin_config, self.source)
                self.assertEqual(list(self.home.iterdir()), [])
                self.assertEqual(self.calls, [])

    def test_missing_paths_sources_and_alias_refused(self):
        for config, plugin, source in (("", self.plugin_config, self.source),
                                       (self.config, "", self.source), (self.config, self.plugin_config, "relative"),
                                       (self.config, self.config, self.source)):
            with self.subTest(config=config, plugin=plugin, source=source):
                with self.assertRaises(ValueError):
                    installer.install(self.policy, config, plugin, source)
                self.assertEqual(self.calls, [])
                self.assertEqual(list(self.home.iterdir()), [])

    def test_cli_requires_all_arguments_without_home_discovery(self):
        for args in ([], ["missing"], ["missing", "a", "b"]):
            result = subprocess.run([sys.executable, installer.__file__, *args], cwd=self.home,
                                    env={"HOME": str(self.home), "PATH": "", "PYTHONDONTWRITEBYTECODE": "1"},
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("usage:", result.stderr)
            self.assertEqual(list(self.home.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
