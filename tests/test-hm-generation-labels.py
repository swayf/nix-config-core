#!/usr/bin/env python3
"""Real Git/Nix fixtures for local-core labels; never build or activate HOME."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class GenerationLabelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        module = ROOT / "modules/home-manager/shell/hm.nix"
        expression = f'''let m = import {module} {{
          config.programs.hm = {{
            flakePath = "/unused"; coreFlakePath = null; profile = "fixture";
          }};
          lib.escapeShellArg = s: "'" + s + "'";
          pkgs = {{ git = null; coreutils = null; writeShellApplication = args: args; }};
        }}; in (builtins.head m.config.home.packages).text'''
        cls.script = subprocess.check_output(
            ["nix", "eval", "--offline", "--impure", "--raw", "--expr", expression],
            text=True, timeout=30,
        )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="hm-generation-labels-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home checkout"
        self.core = self.root / "core #?%тест"
        original = self.original = self.root / "original"
        self.locks = self.root / "locks"
        self.locks.mkdir()
        self.trace = self.root / "trace"
        self.env = os.environ | {
            "HOME": str(self.root), "TMPDIR": str(self.locks),
            "HM_FLAKE": str(self.home), "HM_CORE_FLAKE": str(self.core),
            "HM_PROFILE": "fixture", "XDG_STATE_HOME": str(self.root / "state"),
            "HM_MARKS": str(self.root / "marks"),
            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            "TRACE": str(self.trace), "REAL_NIX": shutil.which("nix"),
        }
        self.env.pop("MOCK_EVAL_FAILURE", None)
        for repo, marker in ((original, "original"), (self.core, "local")):
            self.init(repo, '{ outputs = _: { marker = "' + marker + '"; }; }')
        self.init(self.home, '''{
          inputs.core.url = "git+file://''' + str(original) + '''";
          outputs = { self, core }: { probe = {
            label = self.shortRev or self.dirtyShortRev or "unknown";
            marker = core.marker;
          }; };
        }''', commit=False)
        self.nix("flake", "lock", str(self.home))
        self.git(self.home, "add", "flake.lock")
        self.git(self.home, "commit", "--quiet", "-m", "Initial fixture")
        self.wrapper = self.root / "hm"
        self.wrapper.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + self.script)
        self.wrapper.chmod(0o755)
        subprocess.run(["bash", "-n", str(self.wrapper)], check=True)
        # Lock resolution and probe evaluation use real Nix. Only the expensive
        # build/activation entry points are replaced, recording their arguments.
        mock = '''#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
kind, args = Path(sys.argv[0]).name, sys.argv[1:]
event = {"kind": kind, "args": args}
with open(os.environ["TRACE"], "a") as f:
    f.write(json.dumps(event) + "\\n")
if os.environ.get("MOCK_EVAL_FAILURE"):
    sys.exit(23)
if kind == "nix" and args[:2] == ["store", "diff-closures"]:
    sys.exit(0)
if kind == "home-manager":
    ref = args[args.index("--flake") + 1]
else:
    ref = args[-1]
options = []
for i, arg in enumerate(args):
    if arg == "--override-input":
        options += args[i:i + 3]
if options:
    options += ["--no-write-lock-file"]
result = subprocess.check_output([os.environ["REAL_NIX"], "eval", "--offline", "--json",
    ref.split("#")[0] + "#probe", *options], text=True)
with open(os.environ["TRACE"], "a") as f:
    f.write(json.dumps({"probe": json.loads(result)}) + "\\n")
if kind == "nix":
    print("/fake/generation")
'''
        for name in ("nix", "home-manager"):
            path = self.root / name
            path.write_text(mock)
            path.chmod(0o755)
        self.env["PATH"] = f"{self.root}:{os.environ['PATH']}"

    def git(self, repo, *args):
        return subprocess.check_output(["git", "-C", str(repo), *args], env=self.env,
                                       text=True, stderr=subprocess.PIPE).strip()

    def nix(self, *args):
        return subprocess.check_output([self.env["REAL_NIX"], "--offline", *args],
                                       env=self.env, text=True, stderr=subprocess.PIPE)

    def init(self, repo, flake, commit=True):
        repo.mkdir()
        self.git(repo, "init", "--quiet")
        (repo / "flake.nix").write_text(flake)
        (repo / "note").write_text("initial\n")
        self.git(repo, "add", ".")
        if commit:
            self.git(repo, "commit", "--quiet", "-m", "Initial fixture")

    def run_hm(self, *args, check=True):
        result = subprocess.run([str(self.wrapper), *args], env=self.env,
                                text=True, capture_output=True, timeout=30)
        if check and result.returncode:
            self.fail(result.stderr)
        return result

    def events(self):
        return [json.loads(line) for line in self.trace.read_text().splitlines()]

    def check_actions(self, expected_label, expected_marker="local"):
        paths = [self.home / "flake.lock", self.home / ".git/index", self.core / ".git/index"]
        before = [path.read_bytes() for path in paths]
        for action in ("build", "switch", "news", "diff-closures"):
            with self.subTest(action=action):
                self.trace.write_text("")
                self.run_hm(action)
                probes = [e["probe"] for e in self.events() if "probe" in e]
                self.assertEqual(probes, [{"label": expected_label, "marker": expected_marker}])
                self.assertEqual(before, [path.read_bytes() for path in paths])
                self.assertEqual(list(self.locks.iterdir()), [])

    def test_clean_home_keeps_revision_and_local_core(self):
        self.check_actions(self.git(self.home, "rev-parse", "--short", "HEAD"))

    def test_dirty_home_remains_dirty(self):
        rev = self.git(self.home, "rev-parse", "--short", "HEAD")
        (self.home / "note").write_text("dirty\n")
        self.check_actions(rev + "-dirty")

    def test_dirty_tracked_core_is_used(self):
        (self.core / "flake.nix").write_text('{ outputs = _: { marker = "dirty local"; }; }')
        self.check_actions(self.git(self.home, "rev-parse", "--short", "HEAD"), "dirty local")

    def test_disabled_local_core_needs_no_wrapper(self):
        self.env["HM_CORE_FLAKE"] = ""
        self.check_actions(self.git(self.home, "rev-parse", "--short", "HEAD"), "original")
        self.assertFalse(any("--override-input" in e.get("args", []) for e in self.events()))

    def test_nested_override_keeps_shared_input_follows(self):
        selected = self.root / "selected-packages"
        default = self.root / "default-packages"
        self.init(selected, '{ outputs = _: { marker = "selected policy"; }; }')
        self.init(default, '{ outputs = _: { marker = "default policy"; }; }')
        for repo in (self.original, self.core):
            (repo / "flake.nix").write_text('''{
              inputs.packages.url = "git+''' + default.as_uri() + '''";
              outputs = { packages, ... }: { marker = packages.marker; };
            }''')
            self.git(repo, "commit", "--quiet", "-am", "Declare shared package input")
        (self.home / "flake.nix").write_text('''{
          inputs.core.url = "git+''' + self.original.as_uri() + '''";
          inputs.core.inputs.packages.follows = "packages";
          inputs.packages.url = "git+''' + selected.as_uri() + '''";
          outputs = { self, core, ... }: { probe = {
            label = self.shortRev or self.dirtyShortRev or "unknown";
            marker = core.marker;
          }; };
        }''')
        self.nix("flake", "lock", str(self.home))
        self.git(self.home, "commit", "--quiet", "-am", "Follow selected packages")
        self.check_actions(self.git(self.home, "rev-parse", "--short", "HEAD"), "selected policy")

    def test_explicit_override_still_uses_original_input_paths(self):
        self.run_hm("switch", "--override-input", "core", "git+" + self.original.as_uri())
        probe = next(e["probe"] for e in self.events() if "probe" in e)
        self.assertEqual(probe, {
            "label": self.git(self.home, "rev-parse", "--short", "HEAD"),
            "marker": "original",
        })
        self.assertEqual(list(self.locks.iterdir()), [])

    def test_build_failure_propagates_and_cleans_up(self):
        self.env["MOCK_EVAL_FAILURE"] = "1"
        before = (self.home / "flake.lock").read_bytes()
        result = self.run_hm("switch", check=False)
        self.assertEqual(result.returncode, 23)
        self.assertEqual([e["kind"] for e in self.events()], ["home-manager"])
        self.assertEqual((self.home / "flake.lock").read_bytes(), before)
        self.assertEqual(list(self.locks.iterdir()), [])

    def test_git_diff_does_not_resolve_inputs(self):
        self.run_hm("diff")
        self.assertFalse(self.trace.exists())
        self.assertEqual(list(self.locks.iterdir()), [])

    def test_commit_tag_is_shown_for_the_resulting_generation(self):
        (self.home / "note").write_text("tagged change\n")
        self.run_hm("commit", "Label restored")
        rev = self.git(self.home, "rev-parse", "--short", "HEAD")
        self.run_hm("switch")
        probe = next(e["probe"] for e in self.events() if "probe" in e)
        self.assertEqual(probe["label"], rev)
        generation = self.root / ("test-home-manager-generation-" + probe["label"])
        generation.mkdir()
        profiles = self.root / "state/nix/profiles"
        profiles.mkdir(parents=True)
        (profiles / "home-manager-1-link").symlink_to(generation)
        (profiles / "home-manager").symlink_to("home-manager-1-link")
        self.assertIn("#hm/label-restored", self.run_hm("generations").stdout)


if __name__ == "__main__":
    unittest.main()
