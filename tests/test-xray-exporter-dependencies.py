#!/usr/bin/env python3
"""Public recipe and standalone smoke-runner dependency contracts."""
import ast
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "modules/nixos/xray-exporter-package.nix"
RUNNER = "scripts/test-xray-exporter.nix"
SMOKE = "scripts/test-xray-exporter.py"
LOCAL_PATH = r"(?<![\w$])(?:\.\.?/)[A-Za-z0-9_./-]+"


class ExporterDependencyTests(unittest.TestCase):
    def test_package_has_only_public_build_dependencies(self):
        text = (ROOT / PACKAGE).read_text()
        arguments = text.split("}:", 1)[0].strip("{\n ")
        self.assertEqual(re.findall(r"\b\w+\b", arguments),
                         ["lib", "buildGoModule", "fetchFromGitHub"])
        self.assertEqual(re.findall(LOCAL_PATH, text), [])
        self.assertNotRegex(text, r"\b(?:import|imports|inputs|userConfig|shared|config)\b")
        self.assertIn('rev = "v${version}";', text)
        self.assertIn('hash = "sha256-Ocex/D1OYOlQ2BZ835qrlXaEuMdXaglIPpVzfo0OZ5g=";', text)
        self.assertIn('vendorHash = "sha256-yRxy44SnEFa7yOJyiOgFTk+Z4s5HOJ4cMjcf8VTTfQk=";', text)

    def test_runner_source_is_only_the_smoke_script(self):
        text = (ROOT / RUNNER).read_text()
        self.assertTrue(text.startswith("{ pkgs, exporter }:\n"))
        self.assertEqual(set(re.findall(LOCAL_PATH, text)),
                         {"../.", "./test-xray-exporter.py"})
        self.assertIn("fileset = ./test-xray-exporter.py;", text)
        self.assertIn("nativeBuildInputs = [ pkgs.python3 ];", text)
        self.assertIn('XRAY_EXPORTER_COMMAND = "${exporter}/bin/xray-exporter";', text)
        self.assertNotRegex(text, r"\b(?:import|fetchurl|fetchFromGitHub|inputs|userConfig|shared)\b")

    def test_smoke_uses_stdlib_without_local_helpers(self):
        tree = ast.parse((ROOT / SMOKE).read_text())
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0)
                imports.add(node.module)
        self.assertLessEqual({name.split(".")[0] for name in imports}, sys.stdlib_module_names)
        calls = {ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
        self.assertFalse(calls & {"__import__", "eval", "exec", "os.system", "os.popen"})
        self.assertFalse(any("importlib" in call for call in calls))

    def test_processes_use_packaged_binary_with_empty_inherited_path(self):
        tree = ast.parse((ROOT / SMOKE).read_text())
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and ast.unparse(node.func).startswith("subprocess.")]
        self.assertEqual({ast.unparse(call.func) for call in calls},
                         {"subprocess.run", "subprocess.Popen"})
        self.assertTrue(calls)
        for call in calls:
            self.assertIsInstance(call.args[0], ast.List)
            self.assertEqual(ast.unparse(call.args[0].elts[0]), "COMMAND")
            keywords = {kw.arg: kw.value for kw in call.keywords}
            self.assertNotIn("shell", keywords)
            environment = keywords["env"]
            self.assertIsInstance(environment, ast.Dict)
            self.assertEqual(ast.literal_eval(environment.keys[0]), "PATH")
            self.assertEqual(ast.literal_eval(environment.values[0]), "")
            self.assertLessEqual({ast.literal_eval(key) for key in environment.keys},
                                 {"PATH", "HOME"})
        text = (ROOT / SMOKE).read_text()
        for flag in ("--listen=127.0.0.1:", "--xray-endpoint=127.0.0.1:",
                     "--with-user-metrics", "--log-path=disabled", "--scrape-timeout=1"):
            self.assertIn(flag, text)
        self.assertNotIn("--with-geoip", text)
        self.assertIn("urllib.request.ProxyHandler({})", text)
        self.assertNotRegex(text, r"/(?:home|Users)/|/nix/store/|\.sops\.yaml")


if __name__ == "__main__":
    unittest.main()
