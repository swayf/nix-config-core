#!/usr/bin/env python3
"""Public state API and explicit caller-policy fixtures; no SSH or real state."""
import importlib.util
import json
import os
import runpy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
S = load("vps-state")
P = load("vps-policy")
I = load("vps-inputs")
V = load("vps")


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.legacy = self.repo / "hosts/generated/fixture"
        self.legacy.mkdir(parents=True)
        self.target = self.root / "xdg/vps"
        self.env = patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root / "xdg")})
        self.env.start()
        self.addCleanup(self.env.stop)
        for name in ("install-attempt.json", "checked.json", "credentials.json", "plan.json", "installed-known-hosts", "installer-known-hosts-a"):
            (self.legacy / name).write_bytes(b'{"fixture": "unchanged bytes and hashes"}\n')
        (self.legacy / "secrets.yaml").write_text("NOT A STATE ARTIFACT")

    def test_preview_is_readonly_and_legacy_default_preserved(self):
        before = S.entries(self.legacy)
        self.assertEqual(S.select(self.repo, "fixture"), self.legacy)
        self.assertEqual(S.copy_state(self.repo, "fixture", self.target)["files"], before)
        self.assertFalse(self.target.exists())
        self.assertEqual(S.entries(self.legacy), before)

    def test_copy_preserves_attempt_receipts_knownhosts_and_sources(self):
        before = S.entries(self.legacy)
        S.copy_state(self.repo, "fixture", self.target, copy=True)
        copied = self.target / "fixture"
        self.assertEqual(S.entries(copied), before)
        self.assertEqual(S.entries(self.legacy), before)
        self.assertFalse((copied / "secrets.yaml").exists())
        self.assertEqual(S.select(self.repo, "fixture"), copied)
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in copied.iterdir()))
        with self.assertRaises(ValueError):
            S.copy_state(self.repo, "fixture", self.target, copy=True)

    def test_missing_attempt_and_changed_legacy_fail_closed(self):
        S.copy_state(self.repo, "fixture", self.target, copy=True)
        attempt = self.target / "fixture/install-attempt.json"
        attempt.unlink()
        with self.assertRaises(ValueError):
            S.select(self.repo, "fixture")
        attempt.write_bytes((self.legacy / "install-attempt.json").read_bytes())
        (self.legacy / "credentials.json").write_text("changed legacy")
        with self.assertRaises(ValueError):
            S.select(self.repo, "fixture")

    def test_new_hosts_use_xdg_and_owner_check(self):
        state = S.select(self.repo, "new-host")
        self.assertEqual(state, self.target / "new-host")
        S.create(state, self.repo)
        self.assertEqual(S.select(self.repo, "new-host"), state)
        with self.assertRaises(ValueError):
            S.select(self.root / "another-checkout", "new-host")
        self.assertEqual(S.select(self.repo, "new-host", self.root / "explicit"), self.root / "explicit/new-host")

    def test_symlinks_refused_without_copy(self):
        (self.legacy / "checked.json").unlink()
        (self.legacy / "checked.json").symlink_to("missing")
        with self.assertRaises(ValueError):
            S.copy_state(self.repo, "fixture", self.target, copy=True)
        self.assertFalse(self.target.exists())

    def test_missing_receipt_or_pin_after_copy_fails_closed(self):
        S.copy_state(self.repo, "fixture", self.target, copy=True)
        for name in ("checked.json", "installed-known-hosts", "installer-known-hosts-a"):
            path = self.target / "fixture" / name
            saved = path.read_bytes()
            path.unlink()
            with self.subTest(name=name), self.assertRaises(ValueError):
                S.select(self.repo, "fixture")
            path.write_bytes(saved)
        self.assertEqual(S.select(self.repo, "fixture"), self.target / "fixture")

    def test_directory_cannot_hide_install_attempt(self):
        attempt = self.legacy / "install-attempt.json"
        attempt.unlink()
        attempt.mkdir()
        with self.assertRaisesRegex(ValueError, "artifact type"):
            S.copy_state(self.repo, "fixture", self.target, copy=True)

    def test_nested_diagnostics_preserved_and_symlink_refused(self):
        error = self.legacy / "errors/nested/fixture.json"
        error.parent.mkdir(parents=True)
        error.write_bytes(b'{"synthetic": true}\n')
        S.copy_state(self.repo, "fixture", self.target, copy=True)
        self.assertEqual((self.target / "fixture/errors/nested/fixture.json").read_bytes(), error.read_bytes())
        error.unlink()
        error.symlink_to(self.legacy / "plan.json")
        with self.assertRaises(ValueError):
            S.entries(self.legacy)

    def test_invalid_names_and_symlink_destination_refused(self):
        for name in ("../escape", "A", "", "/absolute"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                S.select(self.repo, name, self.target)
        link = self.root / "link"
        link.symlink_to(self.repo, target_is_directory=True)
        with self.assertRaises(ValueError):
            S.copy_state(self.repo, "fixture", link, copy=True)


class PolicyTests(unittest.TestCase):
    def test_explicit_policy_and_tool_pin_required(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "VPS_CLI_POLICY"):
                P.policy()
            with self.assertRaisesRegex(ValueError, "revision"):
                I.tool_pin()

    def test_policy_rejects_traversal_and_unknown_fields(self):
        valid = json.loads((ROOT / "tests/vps-cli-policy.json").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            for bad in (valid | {"factoryFiles": ["../escape"]}, valid | {"nativeAddressFile": "/private"},
                        valid | {"extra": True}, valid | {"legacyAdminUser": "root"}):
                path.write_text(json.dumps(bad))
                with patch.dict(os.environ, {"VPS_CLI_POLICY": str(path)}), self.assertRaises(ValueError):
                    P.policy()

    def test_legacy_admin_is_injected_not_personal(self):
        with patch.object(V, "source_policy", return_value={"legacyAdminUser": "operator"}):
            self.assertEqual(V.legacy_admin({}), "operator")
            self.assertEqual(V.legacy_admin({"adminUser": "another"}), "another")

    def test_source_set_has_no_private_layout_or_recipient(self):
        for name in I.TOOL_FILES:
            text = (ROOT / "scripts" / name).read_text()
            for forbidden in ("swayf", "lt1-vps", "shared/proxy.nix", "vps-core-adapter.nix"):
                with self.subTest(file=name, forbidden=forbidden):
                    self.assertTrue(forbidden not in text, f"{name}: private dependency {forbidden}")


class InputsTests(unittest.TestCase):
    def test_selected_inputs_policy_and_pin_are_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            policy = repo / "policy.json"
            value = json.loads((ROOT / "tests/vps-cli-policy.json").read_text())
            policy.write_text(json.dumps(value))
            with patch.dict(os.environ, {"VPS_CORE_REV": "b" * 40, "VPS_CORE_NAR_HASH": "sha256-fixture",
                                         "VPS_CLI_POLICY": str(policy)}):
                prepare = runpy.run_path(str(ROOT / "scripts/vps-test-inputs.py"))["prepare"]
                prepare(repo, "fixture")
                host = repo / "hosts/generated/fixture"
                for name in ("vless.json", "machine.json"):
                    (host / name).write_text("{}")
                before = V.source_digest(repo, "fixture")
                (host / "checked.json").write_text("unrelated receipt")
                registry = repo / "hosts/generated/inventory.json"
                registry.write_text(json.dumps({"fixture": {"system": "x86_64-linux"}, "other": {}}))
                self.assertEqual(V.source_digest(repo, "fixture"), before)
                policy.write_text(json.dumps(value | {"legacyAdminUser": "another"}))
                self.assertNotEqual(V.source_digest(repo, "fixture"), before)
                policy.write_text(json.dumps(value))
                (host / "vless.json").write_text("changed")
                self.assertNotEqual(V.source_digest(repo, "fixture"), before)
                with patch.dict(os.environ, {"VPS_CORE_REV": "c" * 40}), self.assertRaisesRegex(V.Error, "pin mismatch"):
                    V.source_digest(repo, "fixture")
                native = repo / "hosts/native"
                native.mkdir()
                for name in ("configuration.nix", "disk-config.nix", "vless.json", "operations.json", "secrets.yaml"):
                    (native / name).write_text("{}")
                for name in value["nativeFiles"]:
                    path = repo / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("{}")
                def fingerprint():
                    return V.selected_digest(repo, "native", native, native / "secrets.yaml", native / "monitoring.yaml")
                before = fingerprint()
                (repo / value["nativeAddressFile"]).write_text("changed")
                self.assertNotEqual(fingerprint(), before)


if __name__ == "__main__":
    unittest.main()
