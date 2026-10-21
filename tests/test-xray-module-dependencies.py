#!/usr/bin/env python3
"""Narrow G6 resource/import guards; includes optional VM runner, not a VM execution."""
import ast
import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
LOCAL_PATH = r"(?<![\w$])(?:\.\.?/)[A-Za-z0-9_./-]+"
MODULES = ("xray-declarative.nix", "xray-monitoring.nix", "xray-sops.nix")


class ModuleDependencyTests(unittest.TestCase):
    def test_runtime_modules_have_no_private_or_sops_imports(self):
        expected = {
            "xray-declarative.nix": {"../../scripts/xray-warp-probe.py", "./xray-render.py"},
            "xray-monitoring.nix": {"./xray-monitoring-rules.nix", "./xray-exporter-package.nix", "./xray-dashboards"},
        }
        for name, paths in expected.items():
            text = (ROOT / "modules/nixos" / name).read_text()
            self.assertEqual(set(re.findall(LOCAL_PATH, text)), paths)
            self.assertNotRegex(text, r"\b(?:inputs|shared|userConfig|sops|imports|secretFile)\b")
            self.assertNotRegex(text, r"secrets/|/home/|/Users/")

    def test_profile_has_only_reviewed_modules_and_no_host(self):
        text = (ROOT / "profiles/nixos/vps-xray.nix").read_text()
        self.assertEqual(set(re.findall(LOCAL_PATH, text)), {
            "../../modules/nixos/vps-hardening.nix", "../../modules/nixos/xray-declarative.nix",
            "../../modules/nixos/xray-monitoring.nix"})
        self.assertNotRegex(text, r"\b(?:inputs|shared|userConfig|sops|users|networking)\b")

    def test_sops_dependency_is_explicit_and_has_no_default_files(self):
        text = (ROOT / "modules/nixos/xray-sops.nix").read_text()
        self.assertIn("{ config, lib, sopsModule, ... }:", text)
        self.assertIn("imports = [ sopsModule ./xray-declarative.nix ./xray-monitoring.nix ];", text)
        self.assertEqual(set(re.findall(LOCAL_PATH, text)), {"./xray-declarative.nix", "./xray-monitoring.nix"})
        self.assertNotRegex(text, r"\b(?:inputs|shared|userConfig|fetchurl)\b|age1[a-z0-9]+")
        self.assertNotRegex(text, r"secrets/|\.sops\.yaml|/home/|/Users/")

    def test_dashboard_has_public_node_scoped_queries(self):
        path = ROOT / "modules/nixos/xray-dashboards/vps.json"
        dashboard = json.loads(path.read_text())
        self.assertEqual(dashboard["uid"], "xray-vps")
        self.assertFalse(dashboard["editable"])
        self.assertTrue(dashboard["panels"])
        self.assertIn("node", {item["name"] for item in dashboard["templating"]["list"]})
        expressions = [target["expr"] for panel in dashboard["panels"] for target in panel["targets"]]
        self.assertTrue(expressions)
        for expr in expressions:
            self.assertIn('node="$node"', expr)
        # HTTP health and StatsService availability are different signals.
        for metric in ("up", "xray_up", "xray_traffic_uplink_bytes_total", "xray_traffic_downlink_bytes_total"):
            self.assertTrue(any(re.search(rf"\b{metric}\{{", expr) for expr in expressions), metric)
        self.assertNotRegex(path.read_text(), r"https?://|age1[a-z0-9]+")

    def test_vm_source_and_synthetic_identities(self):
        text = (ROOT / "scripts/test-xray-monitoring-vm.nix").read_text()
        self.assertTrue(text.startswith("{ pkgs, modules }:\n"))
        self.assertEqual(set(re.findall(LOCAL_PATH, text)), {"../.", "./xray-vm-fixture.py", "./xray-test-fixture.py"})
        self.assertIn("defaults.virtualisation.restrictNetwork = true;", text)
        self.assertIn("modules.xray-declarative", text)
        self.assertIn("modules.xray-monitoring", text)
        self.assertNotRegex(text, r"\binputs\b|\bsops\b|/home/|/Users/")
        for marker in ("server.reboot()", "without-alice", "invalid-config", "XrayStatsUnavailable", "ExporterUnavailable", "saved_history_query"):
            self.assertIn(marker, text)

    def test_vm_helper_has_only_shared_fixture_import(self):
        text = (ROOT / "scripts/xray-vm-fixture.py").read_text()
        tree = ast.parse(text)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0)
                imports.add(node.module)
        self.assertLessEqual({name.split(".")[0] for name in imports}, sys.stdlib_module_names)
        self.assertIn('Path(__file__).with_name("xray-test-fixture.py")', text)
        self.assertNotRegex(text, r"/home/|/Users/|\bsops\b")
        self.assertIn('"alice": {"uuid": F.UUID}', text)
        self.assertIn('"bob": {"uuid": F.SECOND_UUID}', text)

    def test_probe_has_no_registration_decryption_or_host_defaults(self):
        text = (ROOT / "scripts/xray-warp-probe.py").read_text()
        self.assertNotRegex(text, r"wgcf|\bsops\b|\bssh\b|secrets/")
        self.assertIn('parser.add_argument("--renderer", type=Path, required=True)', text)
        self.assertIn('sys.stdin.buffer.read(1024 * 1024 + 1)', text)
        tree = ast.parse(text)
        commands = {ast.literal_eval(n.args[0].elts[0]) for n in ast.walk(tree)
                    if isinstance(n, ast.Call) and ast.unparse(n.func).startswith("subprocess.")
                    and n.args and isinstance(n.args[0], ast.List)}
        self.assertEqual(commands, {"xray", "curl"})


if __name__ == "__main__":
    unittest.main()
