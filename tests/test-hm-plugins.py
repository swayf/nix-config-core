#!/usr/bin/env python3
"""Evaluate the hm wrapper without dependencies; check git-style plugin dispatch."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PluginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        module = ROOT / "modules/home-manager/shell/hm.nix"
        expression = f'''let m = import {module} {{
          config.programs.hm = {{
            flakePath = "/default-checkout"; coreFlakePath = null; profile = "default-user";
            backend = "standalone"; nixosHost = null; bootLabel = false;
          }};
          lib.escapeShellArg = s: "'" + s + "'";
          pkgs = {{ git = null; coreutils = null; writeShellApplication = args: args; }};
        }}; in (builtins.head m.config.home.packages).text'''
        cls.script = subprocess.check_output(
            ["nix", "eval", "--offline", "--impure", "--raw", "--expr", expression],
            text=True, timeout=30,
        )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="hm-plugins-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.flake = self.root / "checkout"
        self.flake.mkdir()
        self.wrapper = self.root / "hm"
        self.wrapper.write_text("set -euo pipefail\n" + self.script)
        plugin = self.root / "hm-demo"
        plugin.write_text("#!/usr/bin/env python3\n"
                          "import json, os, sys\n"
                          "print(json.dumps([sys.argv[1:], os.environ['HM_FLAKE'],"
                          " os.environ['HM_PROFILE']]))\n"
                          "sys.exit(3)\n")
        plugin.chmod(0o755)
        self.env = dict(os.environ, PATH=f"{self.root}:{os.environ['PATH']}",
                        HM_FLAKE=str(self.flake), HM_PROFILE="alice-kde", HM_CORE_FLAKE="")

    def run_hm(self, *args):
        return subprocess.run(["bash", str(self.wrapper), *args], env=self.env,
                              capture_output=True, text=True, timeout=10)

    def test_plugin_gets_arguments_checkout_and_exit_code(self):
        result = self.run_hm("demo", "save", "--profile", "with space")
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(json.loads(result.stdout),
                         [["save", "--profile", "with space"], str(self.flake), "alice-kde"])

    def test_missing_plugin_is_unknown_command(self):
        result = self.run_hm("absent")
        self.assertEqual(result.returncode, 1)
        self.assertIn("неизвестная команда 'absent'", result.stderr)

    def test_unsafe_name_is_not_looked_up(self):
        (self.root / "hm-Demo").symlink_to(self.root / "hm-demo")
        for name in ("Demo", "../hm-demo", "-x"):
            result = self.run_hm(name)
            self.assertEqual(result.returncode, 1, name)
            self.assertIn("неизвестная команда", result.stderr)


if __name__ == "__main__":
    unittest.main()
