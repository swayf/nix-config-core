#!/usr/bin/env python3
"""Reviewed GUI/proxy resource edges, not an arbitrary Nix dependency scanner."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class GuiProxyDependencies(unittest.TestCase):
    def test_resource_edges(self):
        expected = {
            "modules/home-manager/gui-container.nix": {"./gui-container/image.nix", "./container-gateway.nix", "./gui-container/launcher.sh", "./gui-container/host-desktop.nix"},
            "modules/home-manager/gui-container/image.nix": {"./applications.nix", "./exec.sh", "./session.sh"},
            "modules/home-manager/gui-container/windows-shortcuts.nix": {"./host-desktop.nix", "./windows-shortcuts.py"},
        }
        for name, paths in expected.items():
            text = (ROOT / name).read_text()
            edges = set(re.findall(r"(?<![\w/])\./[A-Za-z0-9_./-]+\.(?:nix|sh|py)", text))
            self.assertEqual(edges, paths, name)
            self.assertTrue(all((ROOT / name).parent.joinpath(path).is_file() for path in paths))

    def test_caller_sources_only(self):
        text = (ROOT / "modules/home-manager/mihomo.nix").read_text()
        self.assertIn("${cfg.ruleSeed}/domains_all.lst", text)
        self.assertIn("${cfg.ruleSeed}/ipsum.lst", text)
        self.assertIn('sopsFile = secretFile "mhnodes";', text)
        self.assertNotIn("inputs", text)
        self.assertNotRegex(text, r"(?:import|readFile)\s+\.")
        for name in ("gui-container.nix", "mihomo.nix", "container-gateway.nix"):
            text = (ROOT / "modules/home-manager" / name).read_text()
            for token in ("userConfig", "shared.proxy", "herdr", "projects_kvm", "lt1-", "swayf", "personal.yaml"):
                self.assertNotIn(token, text)

    def test_bounded_profile(self):
        text = (ROOT / "profiles/home-manager/wsl-gui-device.nix").read_text()
        self.assertIn("sopsModule", text)
        self.assertIn("mihomoRuleSeed", text)
        self.assertIn('allowedKeys = [ "mhnodes" ]', text)
        for token in ("agents-container", "pi.nix", "remote", "inputs", "stateVersion", "home.username"):
            self.assertNotIn(token, text)

    def test_fail_closed_gateway(self):
        text = (ROOT / "modules/home-manager/container-gateway.nix").read_text()
        self.assertLess(text.index("nft -f"), text.index("ip route replace default"))
        self.assertLess(text.index("ip route replace default"), text.index("touch /tmp/ready"))
        self.assertIn("policy drop", text)
        gui = (ROOT / "modules/home-manager/gui-container.nix").read_text()
        self.assertNotRegex(gui, r"directPort\s*=")
        self.assertIn('tunDevice = "gui-tun";', gui)


if __name__ == "__main__":
    unittest.main()
