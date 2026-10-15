#!/usr/bin/env python3
"""Bounded installer source/standalone guards, not recipe or transitive license audit."""
import ast
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPERS = ROOT / "modules/home-manager/agents-container"
NAMES = ("herdr-navigation", "herdr-radar", "herdr-review-plugins", "herdr-projects", "pi-prompts", "terminal-browser")


class InstallerDependencies(unittest.TestCase):
    def test_only_standard_library_imports(self):
        expected = {
            "herdr-navigation": {"json", "os", "pathlib", "re", "subprocess", "sys", "tempfile", "tomllib"},
            "herdr-radar": {"json", "os", "pathlib", "re", "subprocess", "sys", "time", "tomllib"},
            "herdr-review-plugins": {"json", "os", "pathlib", "re", "subprocess", "sys", "tempfile", "tomllib"},
            "herdr-projects": {"argparse", "os", "pathlib", "subprocess", "tempfile", "tomllib"},
            "pi-prompts": {"argparse", "pathlib"},
            "terminal-browser": {"json", "subprocess", "sys"},
        }
        for name in NAMES:
            tree = ast.parse((HELPERS / f"install-{name}.py").read_text())
            imports = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    self.assertEqual(node.level, 0)
                    imports.add(node.module)
            self.assertEqual(imports, expected[name])

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

    def test_native_check_has_only_audited_source_files(self):
        text = (ROOT / "tests/agents-installers.nix").read_text()
        self.assertEqual(re.findall(r"\.\./modules/[^\s;]+", text), [
            "../modules/home-manager/agents-container",
            "../modules/home-manager/agents-container/test-install-herdr-navigation.py",
            "../modules/home-manager/agents-container/test-install-herdr-radar.py",
            "../modules/home-manager/agents-container/test-install-herdr-review-plugins.py",
            "../modules/home-manager/agents-container/test-install-herdr-projects.py",
            "../modules/home-manager/agents-container/test-install-pi-prompts.py",
            "../modules/home-manager/agents-container/test-install-pi-shell.py",
            "../modules/home-manager/agents-container/test-install-terminal-browser.py",
            "../modules/home-manager/agents-container/test-wiki-init.py",
            "../modules/home-manager/agents-container/test-claude-profile.py",
        ])
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
        self.assertEqual(imports, {"pathlib", "hashlib", "json", "os", "re", "shlex", "sys", "tempfile"})
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
        self.assertEqual(imports, {"json", "os", "pathlib", "shlex", "sys"})
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

    def test_copied_helpers_and_tests_are_standalone(self):
        with tempfile.TemporaryDirectory(prefix="standalone-agents-installers-") as temp:
            work = Path(temp)
            home = work / "home"
            home.mkdir()
            for name in NAMES:
                for prefix in ("install-", "test-install-"):
                    filename = f"{prefix}{name}.py"
                    shutil.copyfile(HELPERS / filename, work / filename)
            env = {"HOME": str(home), "PATH": "", "PYTHONDONTWRITEBYTECODE": "1"}
            for name in NAMES:
                with self.subTest(name=name):
                    result = subprocess.run([sys.executable, str(work / f"test-install-{name}.py")],
                                            cwd=work, env=env, capture_output=True, text=True, timeout=60)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertNotIn("skipped", result.stderr)
            for filename in ("install-pi-shell.py", "test-install-pi-shell.py"):
                shutil.copyfile(HELPERS / filename, work / filename)
            result = subprocess.run([sys.executable, str(work / "test-install-pi-shell.py")],
                                    cwd=work, env={**env, "TEST_SHELL": shutil.which("bash")},
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("skipped", result.stderr)
            for filename in ("claude-profile.py", "test-claude-profile.py"):
                shutil.copyfile(HELPERS / filename, work / filename)
            result = subprocess.run([sys.executable, str(work / "test-claude-profile.py")],
                                    cwd=work, env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("skipped", result.stderr)
            # Shell helper has an explicit, narrow Bash/coreutils tool contract.
            for filename in ("wiki-init.sh", "test-wiki-init.py"):
                shutil.copyfile(HELPERS / filename, work / filename)
            tools = work / "bin"
            tools.mkdir()
            for tool in ("bash", "mkdir", "readlink", "basename", "mv", "rm", "ln", "cat"):
                source = shutil.which(tool)
                self.assertIsNotNone(source, tool)
                (tools / tool).symlink_to(source)
            result = subprocess.run([sys.executable, str(work / "test-wiki-init.py")],
                                    cwd=work, env={**env, "PATH": str(tools)},
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("skipped", result.stderr)
            self.assertEqual(list(home.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
