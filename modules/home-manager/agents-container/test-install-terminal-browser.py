"""Offline linking contracts with mocks and a synthetic CLI/state fixture."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("install-terminal-browser.py")
spec = importlib.util.spec_from_file_location("installer", SCRIPT)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
SOURCE = "/nix/store/fixture-terminal-browser/share/herdr-plugins/terminal-browser"


class InstallTest(unittest.TestCase):
    def run_install(self, current=None):
        plugins = [] if current is None else [current]
        with patch.object(installer, "herdr", return_value=json.dumps({
            "result": {"plugins": plugins}
        })) as cli:
            installer.install(SOURCE)
            return [call.args for call in cli.call_args_list]

    def current(self, root=SOURCE, enabled=True):
        return {"plugin_id": installer.PLUGIN, "plugin_root": root, "enabled": enabled}

    def test_first_install(self):
        self.assertEqual(self.run_install(), [
            ("plugin", "list", "--json"), ("plugin", "link", SOURCE)
        ])

    def test_current_is_untouched(self):
        for enabled in (True, False):
            self.assertEqual(self.run_install(self.current(enabled=enabled)), [
                ("plugin", "list", "--json")
            ])

    def test_upgrade(self):
        self.assertEqual(self.run_install(self.current("/old"))[-1],
                         ("plugin", "link", SOURCE))

    def test_disabled_upgrade_stays_disabled(self):
        self.assertEqual(self.run_install(self.current("/old", False))[-1],
                         ("plugin", "link", SOURCE, "--disabled"))

    def test_unrelated_plugin_does_not_prevent_install(self):
        current = self.current()
        current["plugin_id"] = "unrelated.plugin"
        self.assertEqual(self.run_install(current)[-1], ("plugin", "link", SOURCE))

    def test_invalid_listing_does_not_link(self):
        for response in ("not-json", "{}", '{"result": {}}'):
            with self.subTest(response=response):
                with patch.object(installer, "herdr", return_value=response) as cli:
                    with self.assertRaises((ValueError, KeyError)):
                        installer.install(SOURCE)
                    self.assertEqual([c.args for c in cli.call_args_list], [
                        ("plugin", "list", "--json")])

    def test_list_or_link_failure_is_not_hidden_or_retried(self):
        error = subprocess.CalledProcessError(1, ["herdr"])
        for responses, expected in (([error], 1),
                                    ([json.dumps({"result": {"plugins": []}}), error], 2)):
            with self.subTest(expected=expected):
                with patch.object(installer, "herdr", side_effect=responses) as cli:
                    with self.assertRaises(subprocess.CalledProcessError):
                        installer.install(SOURCE)
                    self.assertEqual(cli.call_count, expected)

    def test_herdr_wrapper_uses_only_explicit_argv(self):
        with patch.object(installer.subprocess, "check_output", return_value="fixture") as cli:
            self.assertEqual(installer.herdr("plugin", "link", "source with spaces"), "fixture")
            cli.assert_called_once_with(["herdr", "plugin", "link", "source with spaces"], text=True)

    def test_cli_fresh_repeat_upgrade_disabled_and_synthetic_state(self):
        with tempfile.TemporaryDirectory(prefix="browser-linker-") as temp:
            root = Path(temp)
            home = root / "home with spaces"
            home.mkdir()
            bins = root / "bin"
            bins.mkdir()
            registry = home / "fixture-plugins.json"
            registry.write_text("[]")
            calls = root / "calls.jsonl"
            stub = bins / "herdr"
            # A deliberately narrow fake: unexpected server/network/actions fail.
            stub.write_text(f'#!{sys.executable}\n' + '''import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ["FIXTURE_CALLS"], "a") as log:
    log.write(json.dumps(args) + "\\n")
path = Path(os.environ["FIXTURE_REGISTRY"])
plugins = json.loads(path.read_text())
if args == ["plugin", "list", "--json"]:
    print(json.dumps({"result": {"plugins": plugins}}))
elif args[:2] == ["plugin", "link"] and len(args) in (3, 4):
    assert len(args) == 3 or args[3] == "--disabled"
    plugins = [p for p in plugins if p["plugin_id"] != "zenbu-labs.terminal-browser"]
    plugins.append({"plugin_id": "zenbu-labs.terminal-browser",
                    "plugin_root": args[2], "enabled": len(args) == 3})
    path.write_text(json.dumps(plugins))
else:
    raise AssertionError(args)
''')
            stub.chmod(0o755)
            env = {"HOME": str(home), "PATH": str(bins), "FIXTURE_CALLS": str(calls),
                   "FIXTURE_REGISTRY": str(registry), "PYTHONDONTWRITEBYTECODE": "1"}
            state = {}
            for name in (".pi/agent/auth.json", ".pi/agent/sessions/fixture.jsonl", "preferences.toml"):
                path = home / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("synthetic state; not a credential\n")
                state[path] = path.read_bytes()

            def run(source):
                result = subprocess.run([sys.executable, str(SCRIPT), source], env=env,
                                        cwd=root, capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)

            source = str(root / "explicit packaged plugin with spaces")
            run(source)
            run(source)
            upgraded = source + "-next"
            run(upgraded)
            self.assertEqual(json.loads(registry.read_text()), [self.current(upgraded)])
            disabled = self.current(upgraded, False)
            unrelated = {"plugin_id": "fixture.other", "plugin_root": "/fixture", "enabled": False}
            registry.write_text(json.dumps([disabled, unrelated]))
            run(upgraded)  # Same managed root must preserve disabled status.
            latest = source + "-latest"
            run(latest)  # A changed managed root also preserves explicit disable.
            self.assertEqual(json.loads(registry.read_text()), [unrelated, self.current(latest, False)])
            self.assertEqual([json.loads(line) for line in calls.read_text().splitlines()], [
                ["plugin", "list", "--json"], ["plugin", "link", source],
                ["plugin", "list", "--json"],
                ["plugin", "list", "--json"], ["plugin", "link", upgraded],
                ["plugin", "list", "--json"],
                ["plugin", "list", "--json"], ["plugin", "link", latest, "--disabled"],
            ])
            self.assertEqual({p: p.read_bytes() for p in state}, state)


if __name__ == "__main__":
    unittest.main()
