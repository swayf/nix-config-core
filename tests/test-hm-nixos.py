#!/usr/bin/env python3
"""hm with backend nixos: real Git fixtures, mocked nixos-rebuild/nix/sudo."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def script(boot_label):
    module = ROOT / "modules/home-manager/shell/hm.nix"
    expression = f'''let m = import {module} {{
      config.programs.hm = {{
        flakePath = "/unused"; coreFlakePath = null; profile = "unused";
        backend = "nixos"; nixosHost = "fixture"; bootLabel = {"true" if boot_label else "false"};
      }};
      lib.escapeShellArg = s: "'" + s + "'";
      pkgs = {{ git = null; coreutils = null; jq = null; util-linux = null;
                writeShellApplication = args: args; }};
    }}; in (builtins.head m.config.home.packages).text'''
    return subprocess.check_output(
        ["nix", "eval", "--offline", "--impure", "--raw", "--expr", expression],
        text=True, timeout=30,
    )


MOCK = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
kind, args = Path(sys.argv[0]).name, sys.argv[1:]
with open(os.environ["TRACE"], "a") as f:
    f.write(json.dumps({"kind": kind, "args": args,
                        "label": os.environ.get("HM_BOOT_LABEL")}) + "\\n")
if kind == "nixos-rebuild" and args[:2] == ["list-generations", "--json"]:
    print(json.dumps([{"generation": 2, "date": "2026-11-10 10:00:00",
        "nixosVersion": "26.05", "kernelVersion": "6.12", "configurationRevision": "",
        "specialisations": [], "current": True}]))
if kind == "nix" and args[:1] == ["build"]:
    print("/fake/toplevel")
'''


class NixosBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scripts = {True: script(True), False: script(False)}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="hm-nixos-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home checkout"
        self.core = self.root / "core"
        self.trace = self.root / "trace"
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        for name in ("nixos-rebuild", "nix", "sudo", "nix-env"):
            path = bin_dir / name
            path.write_text(MOCK)
            path.chmod(0o755)
        self.env = os.environ | {
            "PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(self.root),
            "HM_FLAKE": str(self.home), "HM_CORE_FLAKE": "", "TRACE": str(self.trace),
            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        }
        self.env.pop("HM_BOOT_LABEL", None)
        for repo in (self.home, self.core):
            repo.mkdir()
            self.git(repo, "init", "--quiet")
            (repo / "flake.nix").write_text("{ outputs = _: { }; }\n")
            self.git(repo, "add", ".")
            self.git(repo, "commit", "--quiet", "-m", "Initial fixture")
        self.use_script(True)

    def use_script(self, boot_label):
        self.wrapper = self.root / "hm"
        self.wrapper.write_text("#!/usr/bin/env bash\nset -euo pipefail\n"
                                + self.scripts[boot_label])
        self.wrapper.chmod(0o755)

    def git(self, repo, *args):
        return subprocess.check_output(["git", "-C", str(repo), *args], env=self.env,
                                       text=True, stderr=subprocess.PIPE).strip()

    def run_hm(self, *args):
        return subprocess.run([str(self.wrapper), *args], env=self.env,
                              text=True, capture_output=True, timeout=30)

    def events(self):
        if not self.trace.exists():
            return []
        return [json.loads(line) for line in self.trace.read_text().splitlines()]

    def test_help_lists_only_nixos_commands(self):
        out = self.run_hm("--help").stdout
        self.assertIn("NixOS fixture", out)
        self.assertIn("rollback [ID] ", out)
        for word in ("news", "mark", "unmark", "HM_PROFILE)", "HM_MARKS"):
            self.assertNotIn("  " + word + " ", out, word)

    def test_standalone_only_commands_refuse_without_side_effects(self):
        for cmd in ("news", "mark", "unmark"):
            with self.subTest(cmd=cmd):
                result = self.run_hm(cmd, "x")
                self.assertEqual(result.returncode, 1)
                self.assertIn("только для standalone", result.stderr)
        self.assertEqual(self.events(), [])

    def test_switch_on_tagged_clean_commit_passes_boot_label(self):
        self.git(self.home, "tag", "-a", "hm/fixture-change", "-m", "x")
        result = self.run_hm("switch", "--show-trace")
        self.assertEqual(result.returncode, 0, result.stderr)
        [event] = self.events()
        self.assertEqual(event["args"][:4], ["switch", "--sudo", "--flake", f"{self.home}#fixture"])
        self.assertEqual(event["args"][4:], ["--impure", "--show-trace"])
        self.assertRegex(event["label"], r"^fixture-change-\d\d:\d\d$")

    def test_switch_on_dirty_tree_has_no_label(self):
        (self.home / "flake.nix").write_text("{ outputs = _: { dirty = 1; }; }\n")
        self.assertEqual(self.run_hm("switch").returncode, 0)
        [event] = self.events()
        self.assertNotIn("--impure", event["args"])
        self.assertIsNone(event["label"])

    def test_switch_without_boot_label_option_stays_pure(self):
        self.use_script(False)
        self.assertEqual(self.run_hm("switch").returncode, 0)
        [event] = self.events()
        self.assertNotIn("--impure", event["args"])
        self.assertIsNone(event["label"])

    def test_local_core_overrides_root_input(self):
        self.env["HM_CORE_FLAKE"] = str(self.core)
        (self.core / "new.nix").write_text("{ }\n")
        result = self.run_hm("build")
        self.assertEqual(result.returncode, 0, result.stderr)
        [event] = self.events()
        args = event["args"]
        self.assertEqual(args[:5], ["build", "--flake", f"{self.home}#fixture",
                                    "--override-input", "core"])
        self.assertTrue(args[5].startswith("git+file://") and args[5].endswith("/core"))
        self.assertEqual(args[6:], ["--no-write-lock-file"])
        self.assertIn("new.nix", result.stderr)

    def test_mark_is_refused_before_pull_or_build(self):
        for cmd in ("switch", "update", "flake-update"):
            with self.subTest(cmd=cmd):
                result = self.run_hm(cmd, "stable")
                self.assertEqual(result.returncode, 1)
                self.assertIn("'stable' не принят", result.stderr)
        self.assertEqual(self.events(), [])

    def test_rollback(self):
        self.assertEqual(self.run_hm("rollback").returncode, 0)
        self.assertEqual(self.events()[0]["args"], ["switch", "--rollback", "--sudo"])
        for arg in ("999999999", "stable"):
            with self.subTest(arg=arg):
                result = self.run_hm("rollback", arg)
                self.assertEqual(result.returncode, 1)
                self.assertIn("нет системного поколения", result.stderr)
        self.assertEqual(len(self.events()), 1)

    def test_diff_closures_builds_toplevel_against_running_system(self):
        self.assertEqual(self.run_hm("diff-closures").returncode, 0)
        build, diff = self.events()
        self.assertEqual(build["args"], ["build", "--no-link", "--print-out-paths",
            f'{self.home}#nixosConfigurations."fixture".config.system.build.toplevel'])
        self.assertEqual(diff["args"], ["store", "diff-closures", "/run/current-system",
                                        "/fake/toplevel"])

    def test_generations_table_has_label_column(self):
        result = self.run_hm("generations")
        self.assertEqual(result.returncode, 0, result.stderr)
        header, row = result.stdout.splitlines()
        self.assertTrue(header.startswith("Generation") and header.endswith("Метка"))
        self.assertTrue(row.startswith("2 ") and row.endswith("-"))
        self.run_hm("generations", "--json")
        self.assertEqual(self.events()[-1]["args"], ["list-generations", "--json"])


if __name__ == "__main__":
    unittest.main()
