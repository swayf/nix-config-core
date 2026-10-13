#!/usr/bin/env python3
"""Reviewed runtime source set and external tool guards, not a general Nix graph."""
import ast
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
LOCAL_PATH = r"(?<![\w$])(?:\.\.?/)[A-Za-z0-9_./-]+"


class RuntimeDependencyTests(unittest.TestCase):
    def test_runner_source_and_tools(self):
        text = (ROOT / "scripts/test-xray-runtime.nix").read_text()
        self.assertTrue(text.startswith("{ pkgs, exporter }:\n"))
        self.assertEqual(set(re.findall(LOCAL_PATH, text)), {
            "../.", "./test-xray-runtime.py", "./xray-test-fixture.py",
            "../modules/nixos/xray-render.py"})
        tools = text.split("nativeBuildInputs = [", 1)[1].split("];", 1)[0]
        self.assertEqual(tools.split(), ["pkgs.python3", "pkgs.openssl", "pkgs.xray", "pkgs.curl", "exporter"])
        self.assertIn('env -i PATH="$PATH" HOME="$TMPDIR"', text)

    def test_python_imports_and_resource_edges(self):
        expected = {
            "scripts/test-xray-runtime.py": {"contextlib", "base64", "importlib.util", "json", "os", "pathlib", "socket", "ssl", "subprocess", "tempfile", "threading", "time", "urllib.request"},
            "scripts/xray-test-fixture.py": {"base64", "http.server", "subprocess"},
        }
        for path, imports in expected.items():
            tree = ast.parse((ROOT / path).read_text())
            actual = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    actual.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    self.assertEqual(node.level, 0)
                    actual.add(node.module)
            self.assertEqual(actual, imports)
        tree = ast.parse((ROOT / "scripts/test-xray-runtime.py").read_text())
        loads = [call for call in ast.walk(tree) if isinstance(call, ast.Call) and ast.unparse(call.func) == "load"]
        self.assertEqual({ast.literal_eval(call.args[1].right) for call in loads},
                         {"modules/nixos/xray-render.py", "scripts/xray-test-fixture.py"})

    def test_only_reviewed_commands(self):
        commands = set()
        for path in ("scripts/test-xray-runtime.py", "scripts/xray-test-fixture.py"):
            tree = ast.parse((ROOT / path).read_text())
            for call in ast.walk(tree):
                if isinstance(call, ast.Call) and (ast.unparse(call.func).startswith("subprocess.") or ast.unparse(call.func) == "start") and call.args and isinstance(call.args[0], ast.List):
                    commands.add(ast.literal_eval(call.args[0].elts[0]))
                    self.assertFalse(any(kw.arg == "shell" for kw in call.keywords))
        self.assertEqual(commands, {"openssl", "xray", "xray-exporter", "curl"})

    def test_synthetic_names_and_no_private_generator(self):
        for path in ("scripts/test-xray-runtime.py", "scripts/test-xray-runtime.nix", "scripts/xray-test-fixture.py"):
            text = (ROOT / path).read_text()
            self.assertNotRegex(text, r"secrets/|sops|/home/|/Users/|AGE-SECRET-KEY-")
        fixture = (ROOT / "scripts/xray-test-fixture.py").read_text()
        self.assertNotRegex(fixture, r"/run/|/var/lib/|seed|telegram|do_POST")

    def test_real_runtime_expectations_retained(self):
        text = (ROOT / "scripts/test-xray-runtime.py").read_text()
        for marker in ('"127.0.0.1"', '"xray_up 1\\n"', '"xray_up 0\\n"',
                       'second >= first', 'retry(tunnel)', 'retry(healthy)',
                       '"scraping reset counters"', '"exporter must recover without a restart"',
                       'urllib.request.ProxyHandler({})', '"--disable"', 'os.umask(0o077)'):
            self.assertIn(marker, text)


if __name__ == "__main__":
    unittest.main()
