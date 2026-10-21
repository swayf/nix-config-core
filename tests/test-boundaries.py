#!/usr/bin/env python3
"""Public-source guards, not a frozen file inventory or a full secret audit."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


def source_violations(name, text):
    # Fixed container HOME paths are not personal checkout paths. Keep these
    # exceptions scoped to their production owners, not arbitrary new files.
    if name.startswith("modules/home-manager/gui-container/"):
        text = text.replace("/home/gui", "/container-home").replace("state/home/", "state/container-home/")
    if name == "modules/home-manager/desktop/max.nix":
        text = text.replace("/home/max", "/container-home")
    if name == "modules/home-manager/agents-container/image.nix":
        text = text.replace('/home/${username}', '/container-home/${username}')
    if name == "modules/home-manager/agents-container.nix":
        text = text.replace('/home/${config.home.username}', '/container-home/${config.home.username}')
    # Generic CLI code may refer to private *filenames*, never embed their data.
    patterns = (r"(?i)AGE-SECRET-KEY-|-----BEGIN [A-Z ]*PRIVATE KEY-----",
                r"/(?:home|Users)/")
    return [pattern for pattern in patterns if re.search(pattern, text)]


def source_files():
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if set(relative.parts) & {".git", "__pycache__"}:
            continue
        yield path, relative.as_posix()


class BoundaryTests(unittest.TestCase):
    def test_no_symlinks_or_private_payload_directories(self):
        for path, name in source_files():
            self.assertFalse(path.is_symlink(), name)
            # Fleet data and encrypted/private identities stay with the caller.
            self.assertFalse(set(Path(name).parts) & {"secrets", "hosts", "identities"}, name)
            self.assertNotIn(path.name, {".sops.yaml", "install-attempt.json", "credentials.json"}, name)

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
        for path, name in source_files():
            # Synthetic top-level test fixtures are not deployed resources.
            # Everything else, including new files, is scanned without an inventory.
            if (name.startswith("tests/") or name.startswith("scripts/test-")
                    or not path.is_file() or path.is_symlink()):
                continue
            with self.subTest(path=name):
                text = path.read_bytes().decode("utf-8", errors="replace")
                self.assertEqual(source_violations(name, text), [])

    def test_source_guard_accepts_comments_and_rejects_private_data(self):
        name = "modules/nixos/new-helper.nix"
        self.assertEqual(source_violations(name, "# A harmless new helper\n{}\n"), [])
        for text in ("/home/alice/project", "/Users/alice/project", "age-secret-key-synthetic",
                     "-----BEGIN PRIVATE KEY-----"):
            with self.subTest(text=text):
                self.assertTrue(source_violations(name, text))
        self.assertTrue(source_violations(name, "/home/gui"))


if __name__ == "__main__":
    unittest.main()
