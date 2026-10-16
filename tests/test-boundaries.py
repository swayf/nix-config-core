#!/usr/bin/env python3
"""Boundary guards for the explicitly reviewed incremental core file set."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = {
    ".gitignore",
    "LICENSE",
    "README.md",
    "flake.nix",
    "flake.lock",
    "modules/home-manager/desktop/build123d.nix",
    "modules/home-manager/desktop/handy.nix",
    "modules/home-manager/desktop/handy/dotool.sh",
    "modules/home-manager/desktop/handy/focus-extension/extension.js",
    "modules/home-manager/desktop/handy/focus-extension/metadata.json",
    "modules/home-manager/desktop/handy/test-dotool.py",
    "modules/home-manager/desktop/handy/test-focus-extension.mjs",
    "modules/home-manager/desktop/kitty.nix",
    "modules/home-manager/desktop/max.nix",
    "modules/home-manager/desktop/max/clipboard.py",
    "modules/home-manager/desktop/max/entrypoint.sh",
    "modules/home-manager/desktop/max/focus-extension/extension.js",
    "modules/home-manager/desktop/max/focus-extension/metadata.json",
    "modules/home-manager/desktop/max/max-session.sh",
    "modules/home-manager/desktop/max/notification-launch.py",
    "modules/home-manager/desktop/max/notification-service.py",
    "modules/home-manager/desktop/max/test_clipboard.py",
    "modules/home-manager/desktop/max/test_focus_extension.mjs",
    "modules/home-manager/desktop/max/test_weston.py",
    "modules/home-manager/desktop/max/weston-xwayland-unframed.patch",
    "modules/home-manager/desktop/max/weston.ini",
    "modules/home-manager/desktop/ms-windows-fonts.nix",
    "modules/home-manager/desktop/vscode.nix",
    "modules/home-manager/desktop/vscode/check-extension.nix",
    "modules/home-manager/desktop/vscode/session.nix",
    "modules/home-manager/desktop/vscode/session.py",
    "modules/home-manager/shell/aliases.nix",
    "modules/home-manager/shell/colors.nix",
    "modules/home-manager/shell/hm.nix",
    "modules/home-manager/shell/scripts/_hm",
    "modules/home-manager/shell/tools.nix",
    "modules/home-manager/shell/zsh.nix",
    "tests/test-vscode-session.py",
    "tests/desktop.nix",
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
    "modules/home-manager/cli.nix",
    "modules/home-manager/playwright.nix",
    "modules/nixos/wsl.nix",
    "tests/applications.nix",
    "tests/wsl.nix",
    "tests/test-wsl-dependencies.py",
    "modules/home-manager/platform.nix",
    "modules/home-manager/desktop.nix",
    "modules/home-manager/secrets.nix",
    "profiles/home-manager/desktop-common.nix",
    "profiles/home-manager/gnome.nix",
    "profiles/home-manager/kde.nix",
    "profiles/home-manager/wsl-common.nix",
    "profiles/home-manager/wsl-standalone.nix",
    "profiles/home-manager/wsl-integrated.nix",
    "tests/home-manager.nix",
    "tests/home-capabilities-stub.nix",
    "tests/test-home-dependencies.py",
    "tests/test-secret-editor.py",
    "tests/fixtures/device.yaml",
    "tests/fixtures/other.yaml",
    "tests/fixtures/gui-device.yaml",
    "tests/gui-proxy.nix",
    "tests/gui-helpers.nix",
    "tests/test-gui-proxy-dependencies.py",
    "modules/home-manager/mihomo.nix",
    "modules/home-manager/container-gateway.nix",
    "modules/home-manager/gui-container.nix",
    "modules/home-manager/gui-container/image.nix",
    "modules/home-manager/gui-container/applications.nix",
    "modules/home-manager/gui-container/host-desktop.nix",
    "modules/home-manager/gui-container/windows-shortcuts.nix",
    "modules/home-manager/gui-container/windows-shortcuts.py",
    "modules/home-manager/gui-container/launcher.sh",
    "modules/home-manager/gui-container/session.sh",
    "modules/home-manager/gui-container/exec.sh",
    "modules/home-manager/gui-container/test-launcher.py",
    "modules/home-manager/gui-container/test-session.py",
    "modules/home-manager/gui-container/test-windows-shortcuts.py",
    "profiles/home-manager/wsl-gui-device.nix",
    "modules/programs/pi/packages.nix",
    "modules/programs/pi/ask-user-question.nix",
    "modules/programs/pi/context-usage.nix",
    "modules/programs/pi/subagents.nix",
    "modules/programs/pi/web-access.nix",
    "modules/programs/pi/subagents-package-lock.json",
    "modules/programs/pi/web-access-package-lock.json",
    "modules/programs/pi/ask-user-question-herdr.patch",
    "modules/programs/pi/ask-user-question-mouse.patch",
    "modules/programs/pi/install-ask-user-question.sh",
    "modules/programs/pi/install-context-usage.sh",
    "modules/programs/pi/install-subagents.py",
    "modules/programs/pi/install-web-access.py",
    "modules/programs/pi/tests/test-install-ask-user-question.py",
    "modules/programs/pi/tests/test-install-context-usage.py",
    "modules/programs/pi/tests/test-install-subagents.py",
    "modules/programs/pi/tests/test-install-web-access.py",
    "tests/pi-installers.nix",
    "tests/test-pi-dependencies.py",
    "modules/home-manager/pi.nix",
    "modules/programs/pi/installers.nix",
    "modules/programs/pi/shared-models.py",
    "modules/programs/pi/disable-bell.py",
    "modules/programs/pi/install-packet-reviewer.sh",
    "modules/programs/pi/install-bell.sh",
    "modules/programs/pi/install-statusline.sh",
    "modules/programs/pi/install-compact-prompts.sh",
    "modules/programs/pi/install-compact-tools.sh",
    "modules/programs/pi/install-model-presets.sh",
    "modules/programs/pi/install-brainstorming.sh",
    "modules/programs/pi/install-lazy-capabilities.sh",
    "modules/programs/pi/install-wiki-ask.sh",
    "modules/programs/pi/install-context-handoff.sh",
    "modules/programs/pi/tests/test-shared-models.py",
    "modules/programs/pi/tests/test-workspace-installers.py",
    "tests/pi-workspace-installers.nix",
    "tests/pi-home.nix",
    "modules/programs/pi/extensions.nix",
    "modules/programs/pi/extensions/bell.ts",
    "modules/programs/pi/extensions/clear.ts",
    "modules/programs/pi/extensions/codex-limits.ts",
    "modules/programs/pi/extensions/compact-prompts.ts",
    "modules/programs/pi/extensions/compact-tools.ts",
    "modules/programs/pi/extensions/context-handoff/README.md",
    "modules/programs/pi/extensions/context-handoff/core.mjs",
    "modules/programs/pi/extensions/context-handoff/index.ts",
    "modules/programs/pi/extensions/lazy-capabilities/core.mjs",
    "modules/programs/pi/extensions/lazy-capabilities/index.ts",
    "modules/programs/pi/extensions/model-presets.ts",
    "modules/programs/pi/extensions/statusline.ts",
    "modules/programs/pi/extensions/wiki-ask/README.md",
    "modules/programs/pi/extensions/wiki-ask/SKILL.md",
    "modules/programs/pi/extensions/wiki-ask/core.mjs",
    "modules/programs/pi/extensions/wiki-ask/core.test.mjs",
    "modules/programs/pi/extensions/wiki-ask/index.ts",
    "modules/programs/pi/tests/bell.test.mjs",
    "modules/programs/pi/tests/clear.test.mjs",
    "modules/programs/pi/tests/codex-limits.test.mjs",
    "modules/programs/pi/tests/compact-prompts.test.mjs",
    "modules/programs/pi/tests/compact-tools.test.mjs",
    "modules/programs/pi/tests/context-handoff.test.mjs",
    "modules/programs/pi/tests/lazy-capabilities.test.mjs",
    "modules/programs/pi/tests/model-presets.test.mjs",
    "modules/programs/pi/tests/statusline.test.mjs",
    "modules/programs/pi/tests/test-compact-prompts-tui.py",
    "modules/programs/pi/tests/test-compact-tools-tui.py",
    "modules/programs/pi/tests/test-context-handoff-pi.py",
    "modules/programs/pi/tests/test-lazy-capabilities-pi.py",
    "modules/programs/pi/wiki-ask.nix",
    "tests/pi-extensions-native.nix",
    "tests/pi-extensions.nix",
    "tests/test-pi-extension-dependencies.py",
    "tests/test-pi-extension-load.py",
    "modules/home-manager/agents-container.nix",
    "modules/home-manager/agents-container/image.nix",
    "modules/home-manager/agents-container/software.nix",
    "modules/home-manager/agents-container/install-herdr-navigation.py",
    "modules/home-manager/agents-container/test-install-herdr-navigation.py",
    "modules/home-manager/agents-container/install-herdr-radar.py",
    "modules/home-manager/agents-container/test-install-herdr-radar.py",
    "modules/home-manager/agents-container/install-herdr-review-plugins.py",
    "modules/home-manager/agents-container/test-install-herdr-review-plugins.py",
    "modules/home-manager/agents-container/install-herdr-projects.py",
    "modules/home-manager/agents-container/install-pi-prompts.py",
    "modules/home-manager/agents-container/install-terminal-browser.py",
    "modules/home-manager/agents-container/test-install-herdr-projects.py",
    "modules/home-manager/agents-container/test-install-pi-prompts.py",
    "modules/home-manager/agents-container/install-pi-shell.py",
    "modules/home-manager/agents-container/test-install-pi-shell.py",
    "modules/home-manager/agents-container/test-install-terminal-browser.py",
    "modules/home-manager/agents-container/wiki-init.sh",
    "modules/home-manager/agents-container/test-wiki-init.py",
    "modules/home-manager/agents-container/claude-profile.py",
    "modules/home-manager/agents-container/test-claude-profile.py",
    "tests/agents-installers.nix",
    "tests/test-agents-installers-dependencies.py",
    "modules/home-manager/agents-container/packages.nix",
    "modules/home-manager/agents-container/packages/herdr.nix",
    "modules/home-manager/agents-container/packages/herdr-plus.nix",
    "modules/home-manager/agents-container/packages/herdr-navigator.nix",
    "modules/home-manager/agents-container/packages/herdr-review-plugins.nix",
    "modules/home-manager/agents-container/packages/terminal-browser.nix",
    "modules/home-manager/agents-container/packages/terminal-code.nix",
    "modules/home-manager/agents-container/packages/obsidian-wiki.nix",
    "modules/home-manager/agents-container/packages/zcode-app-cli.nix",
    "modules/home-manager/agents-container/packages/microsandbox.nix",
    "modules/home-manager/agents-container/notice-bundle.nix",
    "modules/home-manager/agents-container/notice-bundle.py",
    "modules/home-manager/agents-container/test-notice-bundle.py",
    "tests/agents-notices.nix",
    "modules/home-manager/agents-container/radar-package.nix",
    "modules/home-manager/agents-container/radar-package/herdr-radar-agent-groups.patch",
    "modules/home-manager/agents-container/radar-package/herdr-radar-status.patch",
    "modules/home-manager/agents-container/radar-package/test-herdr-radar-agent-groups.js",
    "tests/agents-radar-package.nix",
    "tests/agents-software.nix",
    "tests/test-agents-software.py",
    "tests/test-agents-software-dependencies.py",
    "modules/home-manager/agents-container/launcher.nix",
    "modules/home-manager/agents-container/launcher.sh",
    "modules/home-manager/agents-container/process-preflight.sh",
    "modules/home-manager/agents-container/secret-environment.py",
    "modules/home-manager/agents-container/clipboard.py",
    "modules/home-manager/agents-container/sound.py",
    "modules/home-manager/agents-container/sound-client.py",
    "modules/home-manager/agents-container/test-launcher.py",
    "modules/home-manager/agents-container/test-process-preflight.py",
    "modules/home-manager/agents-container/test-secret-environment.py",
    "modules/home-manager/agents-container/test-clipboard.py",
    "modules/home-manager/agents-container/test-sound.py",
    "tests/agents-helpers.nix",
    "tests/agents-home.nix",
    "tests/test-agents-dependencies.py",
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
                # The immutable image uses a fixed, non-host container HOME.
                if name.startswith("modules/home-manager/gui-container/"):
                    text = text.replace("/home/gui", "/container-home").replace("state/home/", "state/container-home/")
                if name == "modules/home-manager/desktop/max.nix":
                    text = text.replace("/home/max", "/container-home")
                if name == "modules/home-manager/agents-container/image.nix":
                    text = text.replace('/home/${username}', '/container-home/${username}')
                if name == "modules/home-manager/agents-container.nix":
                    text = text.replace('/home/${config.home.username}', '/container-home/${config.home.username}')
                self.assertNotRegex(text, r"/(?:home|Users)/")
                self.assertNotIn(".sops.yaml", text)
                self.assertNotIn("install-attempt.json", text)


if __name__ == "__main__":
    unittest.main()
