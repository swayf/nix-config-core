#!/usr/bin/env python3
"""Narrow import/resource guards for the reviewed VPS profile, not a general Nix graph."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
LOCAL_PATH = r"(?<![\w$])(?:\.\.?/)[A-Za-z0-9_./-]+"


class VpsProfileDependencyTests(unittest.TestCase):
    def test_profile_has_only_reviewed_local_edges(self):
        path = ROOT / "profiles/nixos/vps-base.nix"
        text = path.read_text()
        paths = set(re.findall(LOCAL_PATH, text))
        self.assertEqual(paths, {
            "../../modules/nixos/xray-declarative.nix",
            "../../modules/nixos/xray-monitoring.nix",
            "../../modules/nixos/vps-hardening.nix",
        })
        for relative in paths:
            self.assertTrue((path.parent / relative).is_file())
        self.assertTrue(text.startswith("# VPS policy/profile;"))
        self.assertIn("{ config, lib, pkgs, ... }:", text)
        self.assertNotRegex(text, r"\b(?:vpsModules|inputs|shared|userConfig|sopsModule|secretFile|fetchurl|getFlake)\b")
        self.assertNotRegex(text, r"secrets/|/home/|/Users/|age1[a-z0-9]+")

    def test_host_and_credentials_are_caller_owned(self):
        text = (ROOT / "profiles/nixos/vps-base.nix").read_text()
        self.assertNotRegex(text, r"\b(?:stateVersion|fileSystems|hardware)\s*(?:\.|=)|hostName\s*=|authorizedKeys\.keys\s*=\s*\[")
        self.assertNotRegex(text, r"\b(?:materialFile|botTokenFile|sopsFile)\s*=")
        self.assertIn('default = "admin";', text)
        self.assertIn("openssh.authorizedKeys.keys = cfg.adminKeys;", text)
        self.assertIn("config = lib.mkIf config.services.vps.enable", text)
        self.assertIn("config = lib.mkIf cfg.enable", text)
        self.assertIn("(import ../../modules/nixos/vps-hardening.nix { inherit config; })", text)

    def test_standalone_eval_has_only_explicit_stub_resource(self):
        text = (ROOT / "tests/vps-base.nix").read_text()
        self.assertTrue(text.startswith("# Standalone profile evaluation;"))
        self.assertIn("{ nixpkgs, modules }:", text)
        self.assertEqual(set(re.findall(LOCAL_PATH, text)), {"./sops-stub.nix"})
        self.assertNotRegex(text, r"\b(?:private|candidate|getFlake|fetchurl)\s*[.=]|\.\./home-manager")
        for marker in ("modules.vps-base", "modules.xray-sops", "runtimeOnly", "disabled", "rejectsAdmins", "sopsContract"):
            self.assertIn(marker, text)
        self.assertIn('home = "/srv/fixture-home/alice";', text)

    def test_export_is_local_profile_not_fleet_or_new_input(self):
        text = (ROOT / "flake.nix").read_text()
        self.assertIn("vps-base = ./profiles/nixos/vps-base.nix;", text)
        self.assertNotRegex(text, r"\b(?:nixosConfigurations|homeConfigurations|windowsHosts)\b")
        self.assertEqual(re.findall(r"inputs\.([\w-]+)\.url\s*=", text), ["nixpkgs2605"])


if __name__ == "__main__":
    unittest.main()
