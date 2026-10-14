#!/usr/bin/env python3
"""Guards for this reviewed Pi package/installer slice, not a general scanner."""
import json
from pathlib import Path
import re
import unittest
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
PI = ROOT / "modules/programs/pi"


class PiDependencies(unittest.TestCase):
    def test_exact_recipe_edges(self):
        expected = {
            "packages.nix": {"./ask-user-question.nix", "./context-usage.nix",
                             "./subagents.nix", "./web-access.nix"},
            "ask-user-question.nix": {"./ask-user-question-herdr.patch", "./ask-user-question-mouse.patch"},
            "context-usage.nix": set(),
            "subagents.nix": {"./subagents-package-lock.json"},
            "web-access.nix": {"./web-access-package-lock.json"},
        }
        for name, paths in expected.items():
            with self.subTest(name=name):
                text = (PI / name).read_text()
                self.assertEqual(set(re.findall(r"(?<![\w/])\./[A-Za-z0-9_./-]+\.(?:nix|patch|json)", text)), paths)
                self.assertTrue(all((PI / path).is_file() for path in paths))
                self.assertNotRegex(text, r"\b(?:inputs|userConfig|homeManager|shared)\b|/home/|projects_kvm")

    def test_locked_public_npm_sources_without_lifecycle_scripts_or_host_peers(self):
        peers = {"@earendil-works/pi-ai", "@earendil-works/pi-agent-core",
                 "@earendil-works/pi-coding-agent", "@earendil-works/pi-tui", "typebox"}
        for name, version in (("subagents", "0.77.0"), ("web-access", "0.35.0")):
            data = json.loads((PI / f"{name}-package-lock.json").read_text())
            self.assertEqual(data["lockfileVersion"], 3)
            self.assertEqual(data["version"], version)
            for path, item in data["packages"].items():
                with self.subTest(package=path):
                    self.assertFalse(item.get("hasInstallScript", False))
                    if not path:
                        continue
                    url = urlsplit(item["resolved"])
                    self.assertEqual((url.scheme, url.hostname), ("https", "registry.npmjs.org"))
                    self.assertIsNone(url.username)
                    self.assertIsNone(url.password)
                    self.assertFalse(url.query or url.fragment)
                    self.assertRegex(item["integrity"], r"^sha512-[A-Za-z0-9+/]+=*$")
                    self.assertFalse(any(path.endswith("node_modules/" + peer) for peer in peers))
            recipe = (PI / f"{name}.nix").read_text()
            self.assertIn('"--ignore-scripts" "--legacy-peer-deps"', recipe)
            self.assertIn("npmDepsHash", recipe)

    def test_no_private_policy_or_reviewer_patch_in_slice(self):
        for path in PI.rglob("*"):
            if not path.is_file():
                continue
            with self.subTest(path=path):
                text = path.read_text()
                self.assertNotRegex(text, r"projects_kvm|soft-factory|swayf|reviewer-policy|packet-reviewer\.md")
                self.assertNotRegex(text, r"AGE-SECRET-KEY-|-----BEGIN [A-Z ]*PRIVATE KEY-----")
        self.assertFalse((PI / "web-search.json").exists())
        self.assertFalse((PI / "model-presets.json").exists())
        self.assertNotIn("integration.patch", (PI / "subagents.nix").read_text())

    def test_explicit_installers_and_caller_package_factory_exports(self):
        flake = (ROOT / "flake.nix").read_text()
        self.assertIn("lib.mkPiPackages = import ./modules/programs/pi/packages.nix;", flake)
        for name in ("install-ask-user-question.sh", "install-context-usage.sh",
                     "install-subagents.py", "install-web-access.py"):
            self.assertIn(f"./modules/programs/pi/{name};", flake)
        for name in ("install-subagents.py", "install-web-access.py"):
            text = (PI / name).read_text()
            self.assertIn("sys.argv[1]", text)
            self.assertNotIn("requests", text)
            self.assertNotIn("subprocess", text)


if __name__ == "__main__":
    unittest.main()
