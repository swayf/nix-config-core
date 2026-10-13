#!/usr/bin/env python3
"""Boundary guards for the explicitly reviewed incremental core file set."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = {
    ".gitignore",
    "README.md",
    "flake.nix",
    "flake.lock",
    "modules/nixos/vps-hardening.nix",
    "modules/nixos/xray-monitoring-rules.nix",
    "modules/nixos/xray-exporter-package.nix",
    "scripts/test-xray-exporter.py",
    "scripts/test-xray-exporter.nix",
    "tests/test-xray-exporter-dependencies.py",
    "modules/nixos/xray-render.py",
    "modules/nixos/xray-render-package.nix",
    "scripts/test-xray-render.py",
    "scripts/test-xray-render.nix",
    "scripts/test-xray-rules.nix",
    "scripts/test-xray-rules.json",
    "tests/test-xray-dependencies.py",
    "tests/test-xray-rules-dependencies.py",
    "tests/vps-hardening.nix",
    "tests/test-boundaries.py",
    "scripts/test-xray-runtime.py",
    "scripts/test-xray-runtime.nix",
    "scripts/xray-test-fixture.py",
    "tests/test-xray-runtime-dependencies.py",
    "modules/nixos/xray-declarative.nix",
    "modules/nixos/xray-monitoring.nix",
    "modules/nixos/xray-sops.nix",
    "modules/nixos/xray-dashboards/vps.json",
    "profiles/nixos/vps-xray.nix",
    "profiles/nixos/vps-base.nix",
    "tests/vps-base.nix",
    "tests/test-vps-base-dependencies.py",
    "scripts/xray-warp-probe.py",
    "scripts/xray-vm-fixture.py",
    "scripts/test-xray-monitoring-vm.nix",
    "tests/sops-stub.nix",
    "tests/xray-modules.nix",
    "tests/test-xray-module-dependencies.py",
    "tests/test-xray-warp-probe.py",
}


class BoundaryTests(unittest.TestCase):
    def test_reviewed_files_only_and_no_symlinks(self):
        files = set()
        for path in ROOT.rglob("*"):
            relative = path.relative_to(ROOT)
            if set(relative.parts) & {".git", "__pycache__"}:
                continue
            self.assertFalse(path.is_symlink(), str(relative))
            if path.is_file():
                files.add(relative.as_posix())
        self.assertEqual(files, ALLOWLIST)

    def test_module_has_no_private_arguments_or_imports(self):
        text = (ROOT / "modules/nixos/vps-hardening.nix").read_text()
        for token in ("userConfig", "inputs", "shared", "imports", "builtins.readFile"):
            self.assertNotRegex(text, rf"\b{re.escape(token)}\b")
        self.assertNotIn("/home/", text)
        self.assertNotRegex(text, r"\b(?:home|system)\.stateVersion\b")

    def test_monitoring_fixture_has_only_synthetic_nodes(self):
        fixture = json.loads((ROOT / "scripts/test-xray-rules.json").read_text())
        for case in fixture["tests"]:
            for series in case.get("input_series", []):
                self.assertEqual(re.findall(r'node="([^"]*)"', series["series"]), ["test-vps"])
                self.assertNotRegex(series["series"], r"https?://|(?:\d{1,3}\.){3}\d{1,3}")
            for test in case["alert_rule_test"]:
                for alert in test["exp_alerts"]:
                    self.assertEqual(alert["exp_labels"]["node"], "test-vps")

    def test_no_fleet_outputs(self):
        text = (ROOT / "flake.nix").read_text()
        for token in ("homeConfigurations", "nixosConfigurations", "windowsHosts"):
            self.assertNotRegex(text, rf"\b{token}\b")

    def test_only_public_pinned_dependency(self):
        lock = json.loads((ROOT / "flake.lock").read_text())
        self.assertEqual(lock["version"], 7)
        self.assertEqual(lock["root"], "root")
        self.assertEqual(set(lock["nodes"]), {"root", "nixpkgs2605"})
        self.assertEqual(lock["nodes"]["root"]["inputs"], {"nixpkgs2605": "nixpkgs2605"})
        node = lock["nodes"]["nixpkgs2605"]
        self.assertEqual(node["locked"]["type"], "github")
        self.assertEqual(node["locked"]["owner"], "NixOS")
        self.assertEqual(node["locked"]["repo"], "nixpkgs")
        self.assertRegex(node["locked"]["rev"], r"^[0-9a-f]{40}$")
        self.assertEqual(node["original"]["ref"], "release-26.05")

    def test_no_key_material_or_checkout_paths_in_sources(self):
        for name in sorted(name for name in ALLOWLIST if not name.startswith("tests/")):
            with self.subTest(path=name):
                text = (ROOT / name).read_text()
                self.assertNotRegex(text, r"(?i)AGE-SECRET-KEY-|-----BEGIN [A-Z ]*PRIVATE KEY-----")
                self.assertNotRegex(text, r"/(?:home|Users)/")
                self.assertNotIn(".sops.yaml", text)
                self.assertNotIn("install-attempt.json", text)


if __name__ == "__main__":
    unittest.main()
