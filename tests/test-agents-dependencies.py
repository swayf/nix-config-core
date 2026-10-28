#!/usr/bin/env python3
"""Bounded Agents source edges/security contracts, not a dependency scanner."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
HM = ROOT / "modules/home-manager"


class AgentsDependencies(unittest.TestCase):
    def test_exact_edges(self):
        edges = {
            "agents-container.nix": {"./agents-container/launcher.nix", "./agents-container/image.nix",
                "./container-gateway.nix", "./agents-container/secret-environment.py",
                "./agents-container/clipboard.py", "./agents-container/sound.py", "./agents-container/web-links.py",
                "./agents-container/open.py"},
            "agents-container/image.nix": {"./process-preflight.sh", "./secret-environment.py", "./clipboard.py", "./web-links.py"},
            "agents-container/launcher.nix": {"./launcher.sh"},
        }
        for name, expected in edges.items():
            text = (HM / name).read_text()
            actual = set(re.findall(r"(?<![\w/])\./[A-Za-z0-9_./-]+\.(?:nix|sh|py)", text))
            self.assertEqual(actual, expected, name)
            self.assertTrue(all((HM / name).parent.joinpath(p).is_file() for p in expected))

    def test_no_private_defaults_or_arguments(self):
        # Include nested package recipes and patches, not just top-level helpers.
        for p in [HM / "agents-container.nix", *sorted((HM / "agents-container").rglob("*"))]:
            if not p.is_file() or p.name.startswith("test-") or "__pycache__" in p.parts:
                continue
            text = p.read_text()
            for token in ("inputs", "hasSecret", "secretFile", "config.platform", "config.sops",
                          "config.services.mihomo", "projects_kvm", "agents-dallas", "America/Chicago",
                          "software.nix", "sounds/", "swayf", "personal.yaml", "herdr-projects"):
                self.assertNotIn(token, text, str(p))
        image = (HM / "agents-container/image.nix").read_text()
        self.assertIn("  software,", image)
        self.assertNotIn("software ?", image)

    def test_gateway_is_reused_and_fail_closed(self):
        self.assertFalse((HM / "agents-container/gateway.nix").exists())
        gateway = (HM / "container-gateway.nix").read_text()
        self.assertIn("directPort ? null", gateway)
        self.assertLess(gateway.index("nft -f"), gateway.index("ip route replace default"))
        self.assertLess(gateway.index("ip route replace default"), gateway.index("touch /tmp/ready"))
        self.assertIn("policy drop", gateway)

    def test_workspace_security_contract(self):
        text = (HM / "agents-container/launcher.sh").read_text()
        work = text.split("Creating workspace container", 1)[1].split("Starting workspace container", 1)[0]
        for token in ("--init", "--shm-size=1g", "--pids-limit=4096", "--cap-drop=ALL",
                      "--cap-add=SETUID", "--cap-add=SETGID", "--userns=keep-id", "--network=\"container:$gateway\""):
            self.assertIn(token, work)
        for token in ("--privileged", "--cap-add=NET_ADMIN", "--cap-add=SYS_ADMIN", "--ipc=host",
                      "docker.sock", "daemon-socket", "src=/nix", "wayland-0", "/pulse/"):
            self.assertNotIn(token, work)
        self.assertIn("dst=/run/agents-secret-environment,ro=true", work)
        self.assertIn("dst=/run/agents-nix-auth,ro=true", work)

    def test_entrypoint_order_and_no_auto_start(self):
        image = (HM / "agents-container/image.nix").read_text()
        entry = image.split('entrypoint = pkgs.writeShellScript', 1)[1]
        self.assertLess(entry.index("agents_process_preflight"), entry.index("secret-environment.py"))
        module = (HM / "agents-container.nix").read_text()
        self.assertNotIn("WantedBy", module)
        self.assertIn("cfg.software != null", module)
        self.assertIn("cfg.proxy.port != null", module)
        self.assertIn("sound.enable = cfg.sound.enable", module)


if __name__ == "__main__":
    unittest.main()
