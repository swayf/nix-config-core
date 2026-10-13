#!/usr/bin/env python3
"""Dependency guards for the reviewed renderer group, not a general Nix graph."""
import ast
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DependencyTests(unittest.TestCase):
    def test_python_imports_are_stdlib_only(self):
        for name in ("modules/nixos/xray-render.py", "scripts/test-xray-render.py"):
            with self.subTest(path=name):
                tree = ast.parse((ROOT / name).read_text())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        modules = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        self.assertEqual(node.level, 0)
                        modules = [node.module]
                    else:
                        continue
                    for module in modules:
                        self.assertIn(module.split(".")[0], sys.stdlib_module_names)
        # The test's only dynamic local module load is the renderer itself.
        tree = ast.parse((ROOT / "scripts/test-xray-render.py").read_text())
        loads = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == "load"]
        self.assertEqual(len(loads), 1)
        self.assertEqual(ast.literal_eval(loads[0].args[0]), "render")
        self.assertEqual(ast.literal_eval(loads[0].args[1].right), "modules/nixos/xray-render.py")

    def test_renderer_external_dependency_is_only_openssl(self):
        tree = ast.parse((ROOT / "modules/nixos/xray-render.py").read_text())
        commands = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, {"eval", "exec", "__import__"})
                if (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "subprocess"):
                    self.assertEqual(node.func.attr, "run")
                    commands.append(ast.literal_eval(node.args[0]))
        self.assertEqual(commands, [["openssl", "pkey", "-inform", "DER", "-pubout", "-outform", "DER"]])
        text = (ROOT / "modules/nixos/xray-render.py").read_text()
        self.assertNotIn("__file__", text)
        package = (ROOT / "modules/nixos/xray-render-package.nix").read_text()
        self.assertIn("runtimeInputs = [ python3 openssl ];", package)

    def test_reviewed_local_resource_edges(self):
        expected = {
            "modules/nixos/xray-render-package.nix": {"./xray-render.py"},
            "scripts/test-xray-render.nix": {"../.", "./test-xray-render.py",
                                             "../modules/nixos/xray-render.py"},
        }
        for name, edges in expected.items():
            with self.subTest(path=name):
                path = ROOT / name
                text = path.read_text()
                found = set(re.findall(r"(?<![\w$])(?:\.\.?/)[A-Za-z0-9_./-]+", text))
                self.assertEqual(found, edges)
                for edge in found:
                    target = (path.parent / edge).resolve()
                    self.assertTrue(target.is_relative_to(ROOT))
                    self.assertTrue(target.exists())
                self.assertNotRegex(text, r"\b(?:fetchurl|fetchFromGitHub|fetchGit|userConfig|shared|inputs)\b")


if __name__ == "__main__":
    unittest.main()
