#!/usr/bin/env python3
"""Evaluate the hm wrapper without dependencies; exercise news with a mock CLI."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NewsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        module = ROOT / "modules/home-manager/shell/hm.nix"
        expression = f'''let m = import {module} {{
          config.programs.hm = {{
            flakePath = "/default-checkout"; coreFlakePath = null; profile = "default-user";
          }};
          lib.escapeShellArg = s: "'" + s + "'";
          pkgs = {{ git = null; coreutils = null; writeShellApplication = args: args; }};
        }}; in (builtins.head m.config.home.packages).text'''
        cls.script = subprocess.check_output(
            ["nix", "eval", "--offline", "--impure", "--raw", "--expr", expression],
            text=True, timeout=30,
        )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="hm-news-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.flake = self.root / "checkout with spaces"
        self.flake.mkdir()
        self.wrapper = self.root / "hm"
        self.wrapper.write_text("set -euo pipefail\n" + self.script)
        mock = self.root / "home-manager"
        mock.write_text("#!/usr/bin/env python3\n" +
                        "import json, os, sys\n" +
                        "from pathlib import Path\n" +
                        "args = sys.argv[1:]\n" +
                        "flake = Path(args[args.index('--flake') + 1].split('#')[0]) / 'flake.nix'\n" +
                        "if flake.exists(): Path(os.environ['FLAKE_TRACE']).write_text(flake.read_text())\n" +
                        "print(json.dumps(args))\n" +
                        "sys.exit(int(os.environ.get('MOCK_EXIT', '0')))\n")
        mock.chmod(0o755)
        self.env = dict(os.environ, PATH=f"{self.root}:{os.environ['PATH']}",
                        HM_FLAKE=str(self.flake), HM_PROFILE="alice-kde", HM_CORE_FLAKE="",
                        FLAKE_TRACE=str(self.root / "flake-trace"), TMPDIR=str(self.root))

    def run_hm(self, *args):
        return subprocess.run(["bash", str(self.wrapper), *args], env=self.env,
                              capture_output=True, text=True, timeout=10)

    def test_shell_syntax(self):
        subprocess.run(["bash", "-n", str(self.wrapper)], check=True)

    def test_news_forwards_configuration_and_arguments(self):
        result = self.run_hm("news", "--all", "--option", "test-option", "value with spaces")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [
            "news", "--flake", f"{self.flake}#alice-kde", "--all",
            "--option", "test-option", "value with spaces",
        ])

    def test_news_uses_default_profile(self):
        self.env.pop("HM_PROFILE")
        result = self.run_hm("news")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [
            "news", "--flake", f"{self.flake}#default-user",
        ])

    def test_news_forwards_local_core_without_lock_update(self):
        core = self.root / "core checkout"
        core.mkdir()
        (core / "flake.nix").write_text("{}")
        subprocess.run(["git", "init", "--quiet", str(core)], check=True)
        self.env["HM_CORE_FLAKE"] = str(core)
        result = self.run_hm("news", "--unread")
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads(result.stdout)
        wrapper = args[2].removesuffix("#alice-kde")
        self.assertEqual(args, [
            "news", "--flake", f"{wrapper}#alice-kde", "--override-input", "home/core",
            "git+file://" + str(core).replace(" ", "%20"), "--no-write-lock-file", "--unread",
        ])
        self.assertIn('inputs.home.url = "git+file://' + str(self.flake).replace(" ", "%20") + '"',
                      (self.root / "flake-trace").read_text())
        self.assertFalse(Path(wrapper).exists())

    def test_news_preserves_cli_failure(self):
        self.env["MOCK_EXIT"] = "7"
        self.assertEqual(self.run_hm("news").returncode, 7)

    def test_help_lists_news(self):
        result = self.run_hm("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("news [args]", result.stdout)


if __name__ == "__main__":
    unittest.main()
