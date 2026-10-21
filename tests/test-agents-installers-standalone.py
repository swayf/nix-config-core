#!/usr/bin/env python3
"""Run the nine installer suites once, outside the checkout and inherited HOME/PATH."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPERS = ROOT / "modules/home-manager/agents-container"
NAMES = ("herdr-navigation", "herdr-radar", "herdr-review-plugins", "herdr-projects", "pi-prompts", "terminal-browser")


class StandaloneInstallers(unittest.TestCase):
    def test_copied_helpers_and_tests_are_standalone(self):
        with tempfile.TemporaryDirectory(prefix="standalone-agents-installers-") as temp:
            work = Path(temp)
            home = work / "home"
            home.mkdir()
            env = {"HOME": str(home), "PATH": "", "PYTHONDONTWRITEBYTECODE": "1"}

            def run(helper, test, environment):
                for filename in (helper, test):
                    shutil.copyfile(HELPERS / filename, work / filename)
                result = subprocess.run([sys.executable, str(work / test)], cwd=work,
                                        env=environment, capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn("skipped", result.stderr)
                # Preserve suite counts/diagnostics in the Nix log.
                print(f"{test}:\n{result.stderr}", file=sys.stderr)

            for name in NAMES:
                with self.subTest(name=name):
                    run(f"install-{name}.py", f"test-install-{name}.py", env)
            shell = shutil.which("bash")
            self.assertIsNotNone(shell)
            run("install-pi-shell.py", "test-install-pi-shell.py", {**env, "TEST_SHELL": shell})
            run("claude-profile.py", "test-claude-profile.py", env)
            tools = work / "bin"
            tools.mkdir()
            for tool in ("bash", "mkdir", "readlink", "basename", "mv", "rm", "ln", "cat"):
                source = shutil.which(tool)
                self.assertIsNotNone(source, tool)
                (tools / tool).symlink_to(source)
            run("wiki-init.sh", "test-wiki-init.py", {**env, "PATH": str(tools)})
            self.assertEqual(list(home.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
