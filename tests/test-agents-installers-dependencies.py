#!/usr/bin/env python3
"""Bounded installer source/standalone guards, not recipe or transitive license audit."""
import ast
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPERS = ROOT / "modules/home-manager/agents-container"
NAMES = ("herdr-navigation", "herdr-radar", "herdr-review-plugins", "herdr-projects", "pi-prompts", "terminal-browser")


class InstallerDependencies(unittest.TestCase):
    def test_only_standard_library_imports(self):
        for name in NAMES:
            tree = ast.parse((HELPERS / f"install-{name}.py").read_text())
            imports = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    self.assertEqual(node.level, 0)
                    imports.add(node.module)
            self.assertLessEqual({module.split(".")[0] for module in imports}, sys.stdlib_module_names)

    def test_no_personal_sources_or_policy_payload(self):
        for name in NAMES:
            text = (HELPERS / f"install-{name}.py").read_text()
            for token in ("/home/", "/Users/", "projects_kvm", "swayf", "https://", "http://",
                          "wiki-agent", "rubric", "preset", "model", "auth.json", "sessions/",
                          "expanduser", "Path.home", "getenv", "os.environ", "shell=True"):
                self.assertNotIn(token, text)
        # Protocol/plugin identifiers and the legacy once-marker are compatibility,
        # not an embedded template, browser recipe or caller's presentation policy.
        text = (HELPERS / "install-herdr-projects.py").read_text()
        self.assertIn('parser.add_argument("source", type=Path)', text)
        self.assertIn('MARKER = ".workspace-templates-v1"', text)
        self.assertIn('PLUGIN = "zenbu-labs.terminal-browser"',
                      (HELPERS / "install-terminal-browser.py").read_text())
        text = (HELPERS / "install-pi-prompts.py").read_text()
        for name in ("profiles", "shared", "defaults"):
            self.assertIn(f'parser.add_argument("{name}", type=Path)', text)
        self.assertNotIn(".pi", text)
        self.assertNotIn("/etc/", text)
        self.assertNotIn("resolve(", text)
        self.assertNotIn("nargs=", text)
        self.assertNotIn("default=", text)

    def test_exact_path_exports_and_no_automatic_wiring(self):
        flake = (ROOT / "flake.nix").read_text()
        block = flake.split("lib.agentsInstallers = {", 1)[1].split("};", 1)[0]
        self.assertEqual(re.findall(r"(\w+) = (\./[^;]+);", block), [
            ("claudeProfile", "./modules/home-manager/agents-container/claude-profile.py"),
            ("herdrNavigation", "./modules/home-manager/agents-container/install-herdr-navigation.py"),
            ("herdrProjects", "./modules/home-manager/agents-container/install-herdr-projects.py"),
            ("herdrRadar", "./modules/home-manager/agents-container/install-herdr-radar.py"),
            ("herdrReviewPlugins", "./modules/home-manager/agents-container/install-herdr-review-plugins.py"),
            ("piPrompts", "./modules/home-manager/agents-container/install-pi-prompts.py"),
            ("piShell", "./modules/home-manager/agents-container/install-pi-shell.py"),
            ("terminalBrowser", "./modules/home-manager/agents-container/install-terminal-browser.py"),
            ("wikiInit", "./modules/home-manager/agents-container/wiki-init.sh"),
        ])
        self.assertIn("agents-installers = import ./tests/agents-installers.nix", flake)
        self.assertIn("agents-installers-dependencies =", flake)
        for path in (ROOT / "modules/home-manager/agents-container.nix",
                     HELPERS / "image.nix", HELPERS / "software.nix"):
            self.assertNotIn("agentsInstallers", path.read_text())
            for name in NAMES:
                self.assertNotIn(f"install-{name}", path.read_text())
            self.assertNotIn("wiki-init.sh", path.read_text())
            self.assertNotIn("claude-profile.py", path.read_text())
            self.assertNotIn("install-pi-shell.py", path.read_text())

    def test_native_check_runs_standalone_suites_and_composition(self):
        text = (ROOT / "tests/agents-installers.nix").read_text()
        self.assertIn("test-agents-installers-standalone.py", text)
        self.assertIn("installers.herdrNavigation", text)
        self.assertIn("installers.herdrRadar", text)
        self.assertIn("installers.herdrReviewPlugins", text)
        self.assertIn("installers.herdrProjects", text)
        self.assertIn("installers.piPrompts", text)
        self.assertIn("installers.piShell", text)
        self.assertIn("installers.terminalBrowser", text)
        self.assertIn("installers.wikiInit", text)
        self.assertIn("installers.claudeProfile", text)
        self.assertNotIn("pkgs.nix", text)
        self.assertNotIn("fetch", text)

    def test_navigation_has_no_presentation_defaults(self):
        text = (HELPERS / "install-herdr-navigation.py").read_text()
        for token in ("cloudmanic", "herdr-navigator", "prefix+", "check_updates", "quick-actions",
                      ".before-herdr-navigation", "XDG_", "HERDR_CONFIG_PATH", "Path.home", "os.environ"):
            self.assertNotIn(token, text)
        self.assertIn('validate(policy)', text)
        self.assertIn('len(sys.argv) < 4', text)
        self.assertIn('json.loads(Path(sys.argv[1]).read_text())', text)

    def test_radar_has_no_presentation_defaults(self):
        text = (HELPERS / "install-herdr-radar.py").read_text()
        for token in ("hhdebb", "one-dark", "follow_appearance", "row_label", "state-stop",
                      "state-start", ".before-herdr-radar", "XDG_", "HERDR_CONFIG_PATH", "Path.home", "os.environ"):
            self.assertNotIn(token, text)
        self.assertIn('validate(policy)', text)
        self.assertIn('len(sys.argv) != 5', text)
        self.assertIn('json.loads(Path(sys.argv[1]).read_text())', text)

    def test_review_plugins_have_no_presentation_defaults(self):
        text = (HELPERS / "install-herdr-review-plugins.py").read_text()
        for token in ("persiyanov", "annotate", "herdr-file-viewer", "prefix+", "one-dark",
                      "auto_open", "update_check", ".before-herdr-review-plugins", "XDG_",
                      "HERDR_CONFIG_PATH", "Path.home", "os.environ"):
            self.assertNotIn(token, text)
        self.assertIn('validate(policy)', text)
        self.assertIn('len(sys.argv) < 3', text)
        self.assertIn('json.loads(Path(sys.argv[1]).read_text())', text)

    def test_wiki_shell_has_only_explicit_inputs_and_data_seeds(self):
        text = (HELPERS / "wiki-init.sh").read_text()
        for token in ("$HOME", "${HOME", "XDG_CONFIG_HOME", "/etc/", "/share/", "projects_kvm",
                      "swayf", "obsidian-claw", "OBSIDIAN_VAULT_PATH", "QMD_TRANSPORT",
                      "https://", "http://", "<<EOF"):
            self.assertNotIn(token, text)
        self.assertNotRegex(text, r"(?m)^\s*(?:source|eval|\.)\s")
        self.assertIn('if [ "$#" -ne 9 ]; then', text)
        self.assertIn('cat "$config_seed" > "$config"', text)
        self.assertIn('cat "$index_seed" > "$qmd_dir/index.yml"', text)
        self.assertNotIn("default=", text)

    def test_profile_helper_has_explicit_policy_and_no_personal_defaults(self):
        text = (HELPERS / "claude-profile.py").read_text()
        tree = ast.parse(text)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0)
                imports.add(node.module)
        self.assertLessEqual({module.split(".")[0] for module in imports}, sys.stdlib_module_names)
        for token in ("/home/", "/Users/", "projects_kvm", "swayf", "https://", "http://",
                      "Path.home", "expanduser", "XDG_", "CLAUDE_CONFIG_DIR", "AGENTS_CLAUDE_PROFILE",
                      "dangerously-skip-permissions", "opus-sp-fix", '"cld"', '"cldsp"',
                      '"/bin/claude"', '"/etc/agents/', "eval(", "subprocess", "exec("):
            self.assertNotIn(token, text)
        self.assertIn('set(policy) != FIELDS', text)
        self.assertIn('policy_path = Path(argv[0]).absolute()', text)
        self.assertNotIn("resolve(", text)
        self.assertEqual(re.findall(r'os\.environ\.get\(([^)]+)\)', text), ['policy["profile_env"]'])

    def test_pi_shell_has_explicit_policy_and_no_private_defaults(self):
        text = (HELPERS / "install-pi-shell.py").read_text()
        tree = ast.parse(text)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0)
                imports.add(node.module)
        self.assertLessEqual({module.split(".")[0] for module in imports}, sys.stdlib_module_names)
        for token in ("/home/", "/Users/", "projects_kvm", "swayf", "/etc/agents",
                      ".pi/profiles", ".pi/agent", "general", "wiki", "clipboard/socket",
                      "Path.home", "expanduser", "os.environ", "getenv", "subprocess",
                      "dangerously-skip-permissions", "http://", "https://"):
            self.assertNotIn(token, text)
        self.assertIn('policy = json.loads(Path(argv[0]).read_text())', text)
        self.assertIn('home = Path(absolute(argv[1]))', text)
        self.assertIn('validate(policy)', text)
        self.assertIn('return \'"$HOME"/\' + shlex.quote(path)', text)
        self.assertNotIn("resolve(", text)


if __name__ == "__main__":
    unittest.main()
