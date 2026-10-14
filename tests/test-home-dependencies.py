#!/usr/bin/env python3
"""Reviewed HM profile resources; no implicit caller checkout or identity."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HomeDependencies(unittest.TestCase):
    def test_imports_are_core_local(self):
        for directory in ("modules/home-manager", "profiles/home-manager"):
            for path in (ROOT / directory).glob("*.nix"):
                text = path.read_text()
                for relative in re.findall(r"(?<![\w/])(\.{1,2}/[\w./-]+\.nix)", text):
                    dependency = (path.parent / relative).resolve()
                    self.assertTrue(dependency.is_relative_to(ROOT))
                    self.assertTrue(dependency.is_file(), (path, relative))

    def test_caller_owned_data_and_dependencies(self):
        for directory in ("modules/home-manager", "profiles/home-manager"):
            for path in (ROOT / directory).glob("*.nix"):
                text = path.read_text()
                for token in ("userConfig", "inputs.", "personal.yaml", "recipients", "lt1-", "swayf"):
                    self.assertNotIn(token, text, path)
                self.assertNotRegex(text, r"\bshared\.[A-Za-z_]")
                if path.name == "agents-container.nix":
                    # Container HOME is derived from standard HM identity,
                    # not a host lookup or a caller identity declaration.
                    text = text.replace('/home/${config.home.username}', '/container-home/${config.home.username}')
                    text = text.replace('username = config.home.username;', 'containerUser = standardHMUser;')
                self.assertNotRegex(text, r"/(?:home|Users)/|AGE-SECRET-KEY-|PRIVATE KEY")
                self.assertNotRegex(text, r"(?:home|system)\.stateVersion\s*=")
                self.assertNotRegex(text, r"(?:home\.)?(?:username|homeDirectory)\s*=")

    def test_wsl_has_no_desktop_imports(self):
        for path in (ROOT / "profiles/home-manager").glob("wsl-*.nix"):
            self.assertNotRegex(path.read_text(), r"desktop-common|gnome|kde|plasma|overlays")

    def test_synthetic_ciphertext_only(self):
        for path in (ROOT / "tests/fixtures").glob("*.yaml"):
            text = path.read_text()
            self.assertNotRegex(text, r"age1|AGE-SECRET-KEY-|https?://")
            self.assertIn("ENC[synthetic-not-decryptable]", text)


if __name__ == "__main__":
    unittest.main()
