#!/usr/bin/env python3
"""Offline eval contracts. No builds, installers, real HOME or private checkout."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FACTORY = ROOT / "modules/home-manager/agents-container/software.nix"
NIXPKGS = None


class SoftwareContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="agents-software-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        cls.home = cls.work / "home"
        cls.home.mkdir()
        cls.env = {**os.environ, "HOME": str(cls.home), "XDG_CACHE_HOME": str(cls.work / "cache")}
        cls.env.pop("NIX_PATH", None)
        # Deliberate decoys: an empty resource set must not discover any of them.
        for name in ("projects/prompts/default.md", ".pi/profiles/wiki/AGENTS.md",
                     ".config/agents/preferences.json"):
            path = cls.home / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("must not be discovered\n")
        cls.nixpkgs = NIXPKGS
        if cls.nixpkgs is None:
            result = subprocess.run([
                "nix", "eval", "--offline", "--impure", "--raw", "--expr",
                f"(builtins.getFlake {json.dumps(str(ROOT))}).inputs.nixpkgs2605.outPath",
            ], cwd=cls.work, env=cls.env, text=True, capture_output=True, timeout=120)
            if result.returncode:
                raise RuntimeError(result.stderr)
            cls.nixpkgs = result.stdout
        cls.prefix = f'''
          let
            pkgs = import (builtins.toPath {json.dumps(cls.nixpkgs)}) {{ system = "x86_64-linux"; }};
            mk = import (builtins.toPath {json.dumps(str(FACTORY))});
            args = {{ inherit pkgs; packages = []; environment = {{}};
              resources = {{}}; homeInit = ""; }};
            project = s: {{ packages = map (p: p.drvPath) s.packages;
              inherit (s) environment etcFiles homeInit; }};
          in
        '''

    def evaluate(self, expression, success=True, prefix=None):
        result = subprocess.run([
            "nix", "eval", "--offline", "--impure", "--json",
            # Private evaluator store, including when run inside a Nix check.
            # It can compute derivations but never uses the host daemon or builds.
            "--store", f"local?root={self.work / 'store'}", "--expr",
            (prefix or self.prefix) + expression,
        ], cwd=self.work, env=self.env, text=True, capture_output=True, timeout=120)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("error:", result.stderr)
        return result.stderr

    def test_explicit_empty_has_no_defaults(self):
        self.assertEqual(self.evaluate("project (mk args)"), {
            "packages": [], "environment": {}, "etcFiles": {}, "homeInit": "",
        })

    def test_all_inputs_required(self):
        for name in ("pkgs", "packages", "environment", "resources", "homeInit"):
            with self.subTest(name=name):
                error = self.evaluate(f'project (mk (builtins.removeAttrs args [ "{name}" ]))', False)
                self.assertIn(name, error)

    def test_no_unknown_arguments_or_legacy_etc_files(self):
        for extra in ('etcFiles = {};', 'inputs = {};', 'defaultProfile = "fixture";'):
            with self.subTest(extra=extra):
                self.evaluate(f"project (mk (args // {{ {extra} }}))", False)

    def test_caller_packages_and_order_preserved(self):
        result = self.evaluate('''let
          selected = [ pkgs.gnugrep pkgs.hello pkgs.gnugrep ];
          actual = mk (args // { packages = selected; });
        in { actual = (project actual).packages; expected = map (p: p.drvPath) selected; }''')
        self.assertEqual(result["actual"], result["expected"])
        self.assertEqual(len(result["actual"]), 3)

    def test_explicit_text_file_and_home_init_preserved(self):
        source = self.work / "synthetic project.toml"
        source.write_text('[project]\nname = "fixture"\n')
        result = self.evaluate('''project (mk (args // {
          environment = { FIXTURE_ONLY = "value with spaces"; EMPTY = ""; };
          resources = {
            "agents/projects/fixture.toml" = { source = /. + SOURCE; };
            "agents/prompts/fixture.md" = { text = "synthetic prompt\\n"; };
          };
          homeInit = "do-not-execute-this-command --caller-policy";
        }))'''.replace("SOURCE", json.dumps(str(source))))
        self.assertEqual(result["etcFiles"], {
            "agents/projects/fixture.toml": source.read_text(),
            "agents/prompts/fixture.md": "synthetic prompt\n",
        })
        self.assertEqual(result["environment"], {"FIXTURE_ONLY": "value with spaces", "EMPTY": ""})
        self.assertEqual(result["homeInit"], "do-not-execute-this-command --caller-policy")

    def test_nix_string_contexts_preserved(self):
        result = self.evaluate('''let
          value = "${pkgs.hello}/share/fixture";
          s = mk (args // { environment.FIXTURE = value;
            resources."agents/fixture" = { text = value; }; homeInit = value; });
        in map builtins.getContext [ value s.environment.FIXTURE s.etcFiles."agents/fixture" s.homeInit ]''')
        self.assertTrue(result[0])
        self.assertTrue(all(context == result[0] for context in result))

    def test_invalid_resource_names(self):
        for name in ("", "agents", "/agents/x", "agents/", "agents//x", "agents/../x",
                     "agents/./x", "other/x", "agents/x/../../passwd", "agents/a b",
                     "agents/$(touch x)", 'agents/a"b', "agents/a\nb"):
            with self.subTest(name=name):
                error = self.evaluate('project (mk (args // { resources = { '
                    + json.dumps(name) + ' = { text = "fixture"; }; }; }))', False)
                self.assertIn("resource names", error)

    def test_overlapping_resource_paths_rejected(self):
        self.evaluate('''project (mk (args // { resources = {
          "agents/a" = { text = "file"; };
          "agents/a/b" = { text = "nested"; };
        }; }))''', False)
        self.evaluate('''project (mk (args // { resources = {
          "agents/a" = { text = "file"; }; "agents/ab" = { text = "sibling"; };
        }; }))''')

    def test_invalid_resource_schema(self):
        for resource in ('null', '"raw string"', '{}', '{ text = null; }', '{ source = "relative"; }',
                         '{ source = "/absolute/string"; }', '{ text = "x"; source = ./.; }',
                         '{ text = "x"; mode = "0600"; }', '{ path = ./.; }'):
            with self.subTest(resource=resource):
                self.evaluate('project (mk (args // { resources."agents/x" = '
                              + resource + '; }))', False)

    def test_missing_source_fails_even_for_packages_projection(self):
        missing = json.dumps(str(self.work / "absent.md"))
        error = self.evaluate('''(mk (args // { resources."agents/prompt.md" = {
          source = /. + ''' + missing + '''; }; })).packages''', False)
        self.assertIn("absent.md", error)
        self.assertNotIn("each resource requires", error)

    def test_directory_source_fails(self):
        self.evaluate('''project (mk (args // { resources."agents/x" = {
          source = /. + ''' + json.dumps(str(self.home)) + '''; }; }))''', False)

    def test_invalid_top_level_values(self):
        for field in ('packages = null;', 'packages = [ "not-a-package" ];',
                      'environment = [];', 'environment = { BAD = 1; };',
                      'environment = { "BAD-NAME" = "x"; };',
                      'resources = null;', 'resources = [];', 'homeInit = null;'):
            with self.subTest(field=field):
                self.evaluate(f"project (mk (args // {{ {field} }}))", False)

    def test_no_home_inspection(self):
        self.assertEqual(self.evaluate('project (mk (args // { pkgs = { lib = pkgs.lib; }; }))'),
                         {"packages": [], "environment": {}, "etcFiles": {}, "homeInit": ""})

    def test_copied_factory_is_standalone(self):
        copied = self.work / "standalone/software.nix"
        copied.parent.mkdir(exist_ok=True)
        shutil.copyfile(FACTORY, copied)
        prefix = self.prefix.replace(json.dumps(str(FACTORY)), json.dumps(str(copied)))
        self.assertEqual(self.evaluate('project (mk args)', prefix=prefix)["etcFiles"], {})

    def test_native_contracts(self):
        harness = json.dumps(str(ROOT / "tests/agents-software.nix"))
        result = self.evaluate(f'(import (builtins.toPath {harness}) {{ inherit pkgs; }}).result')
        self.assertTrue(result)
        self.assertTrue(all(result.values()), result)

    def test_export_matches_direct_factory(self):
        flake = json.dumps(str(ROOT / "flake.nix"))
        self.assertTrue(self.evaluate('''let
          exports = (import (builtins.toPath ''' + flake + ''' )).outputs {
            self = {}; nixpkgs2605 = {};
          };
        in project (exports.lib.mkAgentsSoftware args) == project (mk args)'''))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nixpkgs", help="Explicit caller nixpkgs source; default: this core's locked input")
    args, remaining = parser.parse_known_args()
    NIXPKGS = args.nixpkgs
    unittest.main(argv=[__file__, *remaining])
