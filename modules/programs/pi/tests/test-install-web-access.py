"""Test the real all-profile installer without Pi, credentials or networking."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("web_access", ROOT / "install-web-access.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
# Deliberately synthetic caller policy, not a user's routing/model preference.
DEFAULTS = {
    "searchRouting": {"providers": ["fixture-search"]},
    "fetchRouting": {"providers": ["fixture-fetch"]},
    "searchApiKey": "$EXAMPLE_SEARCH_KEY",
}
PACKAGE = "/nix/store/old-pi-web-access-0.35.0"


class InstallWebAccessTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.agent = self.home / ".pi/agent"
        self.custom = self.home / ".pi/profiles/custom"
        self.custom.mkdir(parents=True)
        self.agent.mkdir(parents=True)
        self.xdg = self.home / ".config"

    def run_install(self, package=PACKAGE):
        installer.install(self.home, package, DEFAULTS, self.xdg)

    def test_all_profiles_and_xdg_with_literal_env_references(self):
        self.run_install()
        for directory in [self.agent, self.custom] + [
            self.home / ".pi/profiles" / name for name in ("general", "code", "wiki")
        ]:
            self.assertEqual(json.loads((directory / "settings.json").read_text())["packages"], [PACKAGE])
            self.assertEqual(json.loads((directory / "web-search.json").read_text()), DEFAULTS)
            self.assertEqual((directory / "web-search.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads((self.xdg / "pi/web-search.json").read_text()), DEFAULTS)
        self.assertEqual(DEFAULTS["searchApiKey"], "$EXAMPLE_SEARCH_KEY")

    def test_preserves_models_filters_and_upgrades_one_package(self):
        settings = self.custom / "settings.json"
        original = {
            "defaultModel": "custom-model", "defaultProvider": "custom-provider",
            "enabledModels": ["custom-model"],
            "packages": ["npm:another-extension", {"source": "npm:pi-web-access@0.34.0", "skills": []},
                         "npm:pi-web-access"],
        }
        settings.write_text(json.dumps(original))
        self.run_install()
        self.run_install("/nix/store/new-pi-web-access-0.36.0")
        result = json.loads(settings.read_text())
        self.assertEqual(result["defaultModel"], original["defaultModel"])
        self.assertEqual(result["defaultProvider"], original["defaultProvider"])
        self.assertEqual(result["enabledModels"], original["enabledModels"])
        self.assertEqual(result["packages"], ["npm:another-extension",
                         {"source": "/nix/store/new-pi-web-access-0.36.0", "skills": []}])
        self.assertEqual(json.loads(settings.with_name("settings.json.before-web-access").read_text()), original)

    def test_same_version_rebuild_upgrades_every_existing_profile(self):
        self.run_install()
        directories = [self.agent] + sorted((self.home / ".pi/profiles").iterdir())
        originals = {}
        for directory in directories:
            settings = directory / "settings.json"
            original = {
                "defaultModel": f"model-{directory.name}",
                "packages": ["npm:another-extension", {"source": PACKAGE, "skills": []}],
            }
            settings.write_text(json.dumps(original))
            originals[directory] = original
        rebuilt = "/nix/store/renamed-pi-web-access-0.35.0"
        self.run_install(rebuilt)
        for directory, original in originals.items():
            with self.subTest(profile=directory.name):
                result = json.loads((directory / "settings.json").read_text())
                self.assertEqual(result, {
                    **original,
                    "packages": ["npm:another-extension", {"source": rebuilt, "skills": []}],
                })
        before = {d: (d / "settings.json").stat().st_mtime_ns for d in directories}
        self.run_install(rebuilt)
        self.assertEqual(before, {d: (d / "settings.json").stat().st_mtime_ns for d in directories})

    def test_routing_wins_but_unrelated_settings_remain(self):
        config = self.agent / "web-search.json"
        original = {"provider": "gemini", "searchProvider": "all", "proxy": "http://localhost:3128",
                    "exaApiKey": "private-test-key", "fetchRouting": {"extraOption": True}}
        config.write_text(json.dumps(original))
        self.run_install()
        result = json.loads(config.read_text())
        self.assertNotIn("provider", result)
        self.assertNotIn("searchProvider", result)
        self.assertEqual(result["proxy"], original["proxy"])
        self.assertEqual(result["exaApiKey"], original["exaApiKey"])
        self.assertTrue(result["fetchRouting"]["extraOption"])
        self.assertEqual(result["searchRouting"], DEFAULTS["searchRouting"])
        backup = config.with_name("web-search.json.before-web-access")
        self.assertEqual(json.loads(backup.read_text()), original)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)

    def test_repeated_installation_does_not_rewrite(self):
        self.run_install()
        paths = list(self.home.rglob("*.json"))
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
        self.run_install()
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths})
        self.assertFalse(list(self.home.rglob("*.before-web-access")))

    def test_invalid_json_is_not_overwritten(self):
        settings = self.agent / "settings.json"
        settings.write_text("{invalid")
        with self.assertRaises(json.JSONDecodeError):
            self.run_install()
        self.assertEqual(settings.read_text(), "{invalid")

    def test_dangling_settings_link_is_preserved(self):
        settings = self.custom / "settings.json"
        settings.symlink_to(self.home / "missing")
        self.run_install()
        self.assertTrue(settings.is_symlink())
        self.assertEqual(settings.readlink(), self.home / "missing")


if __name__ == "__main__":
    unittest.main()
