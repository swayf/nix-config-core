#!/usr/bin/env python3
"""Bounded software factory boundary, not a general Nix dependency/license audit."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
FACTORY = ROOT / "modules/home-manager/agents-container/software.nix"


class SoftwareDependencies(unittest.TestCase):
    def test_factory_has_no_implicit_source_edges(self):
        text = FACTORY.read_text()
        self.assertNotRegex(text, r"(?<![\w/])\.\.?/")
        self.assertNotRegex(text, r"\b(?:import|fetchTarball|fetchGit|fetchTree|fetchurl|callPackage)\b")
        self.assertEqual(re.findall(r"builtins\.readFile\s+([\w.]+)", text), ["resource.source"])
        for token in ("getEnv", "pathExists", "readDir", "getFlake", "findFile"):
            self.assertNotIn(token, text)

    def test_no_personal_defaults_or_runtime_mutation(self):
        text = FACTORY.read_text()
        for token in ("inputs", "nixpkgs", "llm-agents", "swayf", "projects_kvm", "/home/", "/Users/",
                      "America/Chicago", "wiki-agent", "packet-reviewer", "model-presets",
                      "web-search.json", "dangerously-skip-permissions", "general", "presets",
                      "rubric", "runCommand", "writeShell", "builtins.exec", "https://"):
            self.assertNotIn(token, text)
        self.assertNotIn(" ? ", text.split(":", 1)[0])
        self.assertIn("inherit packages environment etcFiles homeInit;", text)

    def test_exact_export_and_no_added_inputs(self):
        flake = (ROOT / "flake.nix").read_text()
        self.assertIn("lib.mkAgentsSoftware = import ./modules/home-manager/agents-container/software.nix;", flake)
        self.assertEqual(re.findall(r"^  inputs\.(\w+)\.url", flake, re.M), ["nixpkgs2605"])
        self.assertIn("agents-software =", flake)
        self.assertIn("agents-software-dependencies =", flake)

    def test_only_audited_software_and_runtime_sources(self):
        # Mechanisms and reviewed upstream resources are explicit opt-in paths.
        # Fetched payload pins and private policy stay in the caller.
        expected = {
            "image.nix", "launcher.nix", "launcher.sh", "software.nix", "process-preflight.sh",
            "secret-environment.py", "clipboard.py", "sound.py", "sound-client.py",
            "test-launcher.py", "test-process-preflight.py", "test-secret-environment.py",
            "test-clipboard.py", "test-sound.py",
            "install-herdr-projects.py", "install-pi-prompts.py", "install-terminal-browser.py",
            "test-install-herdr-projects.py", "test-install-pi-prompts.py", "test-install-terminal-browser.py",
            "wiki-init.sh", "test-wiki-init.py", "claude-profile.py", "test-claude-profile.py",
            "install-pi-shell.py", "test-install-pi-shell.py",
            "install-herdr-navigation.py", "test-install-herdr-navigation.py",
            "install-herdr-radar.py", "test-install-herdr-radar.py",
            "install-herdr-review-plugins.py", "test-install-herdr-review-plugins.py",
            "packages.nix", "packages", "radar-package.nix", "radar-package",
            "notice-bundle.nix", "notice-bundle.py", "test-notice-bundle.py",
        }
        actual = {p.name for p in FACTORY.parent.iterdir() if p.name != "__pycache__"}
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
