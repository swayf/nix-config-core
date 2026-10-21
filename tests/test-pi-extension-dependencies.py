#!/usr/bin/env python3
"""Pi public exports, import and policy boundaries, not a frozen file inventory."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
PI = ROOT / "modules/programs/pi"
EXT = PI / "extensions"
EXPORTS = {
    "bell": "bell.ts", "statusline": "statusline.ts",
    "compactPrompts": "compact-prompts.ts", "compactTools": "compact-tools.ts",
    "modelPresets": "model-presets.ts", "lazyCapabilities": "lazy-capabilities",
    "wikiAsk": "wiki-ask", "contextHandoff": "context-handoff",
    "codexLimits": "codex-limits.ts", "clear": "clear.ts",
}
PEERS = {"@earendil-works/pi-ai", "@earendil-works/pi-coding-agent", "@earendil-works/pi-tui"}


class ExtensionDependencies(unittest.TestCase):
    def test_public_exports_without_private_or_legacy_resources(self):
        exported = dict(re.findall(r"([A-Za-z]+) = \./extensions/([^;]+);", (PI / "extensions.nix").read_text()))
        # Existing API remains available; additive exports/resources are allowed.
        self.assertLessEqual(EXPORTS.items(), exported.items())
        for name in exported.values():
            target = EXT / name
            self.assertTrue(target.exists(), target)
            self.assertTrue(target.resolve().is_relative_to(EXT.resolve()), target)
        self.assertFalse(any(p.is_symlink() for p in EXT.rglob("*")))
        for path in EXT.rglob("*"):
            if not path.is_file():
                continue
            text = path.read_text()
            with self.subTest(path=path):
                self.assertNotRegex(text, r"projects_kvm|soft-factory|swayf|obsidian-claw|erpics|ASAF|gpt-6-luna")
                self.assertNotRegex(text, r"AGE-SECRET-KEY-|-----BEGIN [A-Z ]*PRIVATE KEY-----")
                self.assertNotRegex(text, r"/(?:home|Users)/")
                self.assertNotIn("reviewer-policy", text)
        self.assertFalse((PI / "model-presets.json").exists())
        self.assertFalse((PI / "web-search.json").exists())
        self.assertFalse((PI / "agents").exists())

    def test_source_imports_only_node_peers_and_local_mechanisms(self):
        local_edges = {}
        for path in EXT.rglob("*"):
            if path.suffix not in (".ts", ".mjs"):
                continue
            text = path.read_text()
            # Static source imports; generated test probe code uses the same peer set.
            imports = re.findall(r'(?:from\s+|import\s*\()["\']([^"\']+)["\']', text)
            relative = set()
            for name in imports:
                if name.startswith("node:") or name in PEERS:
                    continue
                self.assertTrue(name.startswith("./"), (path, name))
                target = path.parent / name
                self.assertTrue(target.is_file(), target)
                self.assertEqual(target.resolve().parent, path.parent.resolve())
                relative.add(name)
            local_edges[path.relative_to(EXT).as_posix()] = relative
        for directory in (name for name in EXPORTS.values() if not name.endswith(".ts")):
            self.assertEqual(local_edges[f"{directory}/index.ts"], {"./core.mjs"})
            self.assertEqual(local_edges[f"{directory}/core.mjs"], set())
        self.assertEqual(local_edges["wiki-ask/core.test.mjs"], {"./core.mjs"})

    def test_no_implicit_model_policy_and_explicit_factory(self):
        mechanism = (EXT / "wiki-ask/core.mjs").read_text()
        entry = (EXT / "wiki-ask/index.ts").read_text()
        factory = (PI / "wiki-ask.nix").read_text()
        self.assertIn("loadConfig(cwd, { defaultModel } = {})", mechanism)
        self.assertIn('stringField("model", defaultModel)', mechanism)
        self.assertIn("No wiki-ask model configured", mechanism)
        self.assertNotIn("DEFAULT_MODEL", mechanism)
        self.assertNotRegex(mechanism, r"process\.env\.(?:PI_MODEL|WIKI_ASK_DEFAULT_MODEL)")
        self.assertIn("loadConfig(ctx.cwd, { defaultModel })", entry)
        self.assertIn("registerWikiAsk(pi);", entry)
        self.assertIn("{ pkgs, defaultModel }:", factory)
        self.assertIn("builtins.toJSON defaultModel", factory)
        self.assertIn('source + "/index.ts"', factory)
        self.assertIn('source = "${./extensions/wiki-ask}";', factory)
        self.assertNotIn("builtins.readFile", factory)
        self.assertNotRegex(factory, r"\b(?:inputs|userConfig|inventory)\b")

    def test_additive_exports_without_new_inputs_or_hm_defaults(self):
        flake = (ROOT / "flake.nix").read_text()
        for token in ("lib.piExtensions = import ./modules/programs/pi/extensions.nix;",
                      "lib.mkPiWikiAsk = import ./modules/programs/pi/wiki-ask.nix;",
                      "lib.mkPiExtensionChecks = import ./tests/pi-extensions-native.nix;"):
            self.assertIn(token, flake)
        hm = (ROOT / "modules/home-manager/pi.nix").read_text()
        self.assertIn("default = null;", hm)
        self.assertNotIn("extensions.nix", hm)
        default = (ROOT / "tests/pi-extensions.nix").read_text()
        self.assertNotIn("WIKI_ASK_TEST_CLI", default)
        native = (ROOT / "tests/pi-extensions-native.nix").read_text()
        self.assertIn("{ pkgs, pi }:", native)
        self.assertIn("WIKI_ASK_TEST_CLI=${pi}/bin/pi", native)


if __name__ == "__main__":
    unittest.main()
