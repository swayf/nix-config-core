#!/usr/bin/env python3
"""WSL dependencies are explicit caller pins, with no private resources."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WslDependencies(unittest.TestCase):
    def test_explicit_dependencies(self):
        text = (ROOT / "modules/nixos/wsl.nix").read_text()
        self.assertIn("imports = [ wslModule homeManagerModule ];", text)
        self.assertIn("imports = wslHomeModules;", text)
        self.assertIn("nix.registry.nixpkgs.flake = nixpkgsInput;", text)
        for token in ("inputs.", "userConfig", "shared", "../", "swayf", "lt1-", "/home/"):
            self.assertNotIn(token, text)
        self.assertNotRegex(text, r"system\.stateVersion\s*=|wsl\.defaultUser\s*=")

    def test_runtime_helper_and_kernel_devices(self):
        text = (ROOT / "modules/nixos/wsl.nix").read_text()
        for token in ("WSL_FLAKE", "--no-write-lock-file", "sudo nixos-rebuild", "switch --rollback"):
            self.assertIn(token, text)
        self.assertIn("z /dev/net/tun", text)
        self.assertIn("z /dev/fuse", text)
        self.assertNotIn("pkgs.sudo", text)
        self.assertNotIn("c /dev/", text)


if __name__ == "__main__":
    unittest.main()
