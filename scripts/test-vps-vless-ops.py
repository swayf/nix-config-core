#!/usr/bin/env python3
"""Profile operations tests: no real server, token, SSH, deploy or key rotation."""
import contextlib
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid
from types import SimpleNamespace
import unittest
from unittest import mock
import runpy

PREPARE_INPUTS = runpy.run_path(str(Path(__file__).with_name("vps-test-inputs.py")))["prepare"]

ROOT = Path(__file__).resolve().parent

def load(filename, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

V = load("vps.py", "vps")
O = load("vps-vless-ops.py", "ops")
REAL_ENCRYPT, REAL_ATTACHED, REAL_DECRYPT = O.Host.encrypt, O.attached, O.Host.decrypt
ADMIN, HOST = "age1" + "a" * 58, "age1" + "b" * 58
CIPHERTEXT = f"sops:\n  age:\n  - recipient: {ADMIN}\n  - recipient: {HOST}\n".encode()
TOKEN = "123456:TEST_ONLY_TOKEN"
SYSTEM = "/nix/store/" + "a" * 32 + "-nixos-system-fixture"


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "checkout"
        self.repo.mkdir()
        (self.repo / "flake.nix").write_text("{}")
        (self.repo / "flake.lock").write_text("{}")
        self.directory = self.repo / "hosts/generated/fixture"
        self.directory.mkdir(parents=True)
        self.write(self.directory / "vless.json", {"serverName": "example.org", "target": "example.org:443",
                                                  "clients": ["default", "alice"]})
        self.key = Path(self.tmp.name) / "admin"
        self.key.write_text("TEST KEY, NOT USED")
        self.write(self.directory / "machine.json", {"adminUser": "ops", "sshPort": 3333,
                  "adminKeyFiles": [str(self.key) + ".pub"], "chatId": 0, "warpDomains": ["ipinfo.io"]})
        self.write(self.directory / "plan.json", {"connection": {"address": "203.0.113.8", "key": "/SOURCE-NOT-ADMIN"}})
        self.write(self.directory / "credentials.json", {"admin_recipient": ADMIN, "host_recipient": HOST,
                  "host_public_key": "ssh-ed25519 AAAATEST fixture"})
        (self.directory / "secrets.yaml").write_bytes(CIPHERTEXT)
        self.material = {"private_key": "PRIVATE-NEVER-PRINT", "public_key": "PUBLIC", "short_id": "aabb",
                         "clients": {"default": {"uuid": "DEFAULT-UUID"}, "alice": {"uuid": "ALICE-UUID"}}}
        self.payload = {"material": json.dumps(self.material), "warp": "{}", "warp_account": "PRESERVE"}
        self.encrypted = []
        self.output = io.StringIO()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(O.Host, "decrypt", side_effect=self.decrypt).start()
        mock.patch.object(O.Host, "encrypt", side_effect=self.encrypt).start()
        mock.patch.object(V, "evaluate", side_effect=self.evaluate).start()
        self.remote = mock.patch.object(O, "attached", return_value=0).start()
        self.run = mock.patch.object(V, "run", return_value=b"").start()

    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def decrypt(self, path):
        return json.loads(json.dumps({"bot_token": TOKEN} if "monitoring" in path.name else self.payload))

    def encrypt(self, payload):
        self.encrypted.append(json.loads(json.dumps(payload)))
        return CIPHERTEXT

    def evaluate(self, repo, name, attribute, raw=False):
        return {"services.vps.adminUser": "operator", "services.vps.sshPort": 22,
                "services.vps.enable": True, "services.xray.declarative.settings": self.host().public.copy(),
                "services.xray.declarative.secretFile": str(self.directory / "secrets.yaml"),
                "services.xrayMonitoring.telegram.secretFile": str(self.directory / "monitoring.yaml"),
                "services.xray.declarative.statsPort": 10085,
                "services.xray.declarative.warp.enable": False,
                "services.xrayMonitoring.telegram.enable": False,
                "services.xrayMonitoring.telegram.chatId": self.host().settings()["chatId"],
                "services.xray.package.outPath": "/nix/store/test-xray"}[attribute]

    def args(self, *argv):
        return V.parser().parse_args(["--repo", str(self.repo), *argv])

    def invoke(self, *argv):
        args = self.args(*argv)
        with contextlib.redirect_stdout(self.output):
            return O.dispatch(V, args, self.repo, args.state_dir)

    def host(self):
        return O.Host(V, self.repo, "fixture")

    def test_list_is_public_local_and_has_no_side_effects(self):
        before = {p: p.read_bytes() for p in self.repo.rglob("*") if p.is_file()}
        self.invoke("list")
        listed = json.loads(self.output.getvalue())
        self.assertEqual(listed[0]["clients"], ["default", "alice"])
        self.assertNotIn("UUID", self.output.getvalue())
        self.assertNotIn("PRIVATE", self.output.getvalue())
        self.run.assert_not_called()
        self.remote.assert_not_called()
        self.assertEqual(before, {p: p.read_bytes() for p in self.repo.rglob("*") if p.is_file()})

    def test_list_host_and_generated_overrides_native(self):
        self.write(self.repo / "hosts/fixture/vless.json", {"clients": ["native"]})
        self.invoke("list", "fixture")
        self.assertEqual(json.loads(self.output.getvalue())["layout"], "generated")

    def test_invalid_host_symlink_and_duplicate_client_rejected(self):
        with self.assertRaises(V.Error):
            self.invoke("list", "../fixture")
        config = self.directory / "vless.json"
        public = json.loads(config.read_text())
        public["clients"].append("alice")
        self.write(config, public)
        with self.assertRaises(V.Error):
            self.host()
        config.unlink()
        config.symlink_to(self.repo / "flake.nix")
        with self.assertRaises(V.Error):
            self.host()

    def test_add_preserves_keys_existing_uuids_and_warp(self):
        self.invoke("client", "fixture", "add", "bob", "--force")
        changed = json.loads(self.encrypted[-1]["material"])
        for key in ("private_key", "public_key", "short_id"):
            self.assertEqual(changed[key], self.material[key])
        for name in self.material["clients"]:
            self.assertEqual(changed["clients"][name], self.material["clients"][name])
        self.assertIn("bob", changed["clients"])
        self.assertEqual(self.encrypted[-1]["warp_account"], "PRESERVE")
        self.assertEqual(self.host().clients, ["default", "alice", "bob"])
        self.remote.assert_not_called()
        self.assertNotIn("PRIVATE", self.output.getvalue())

    def test_add_reuses_disabled_credentials_without_rotation(self):
        self.payload["material"] = json.dumps(self.material | {"clients": self.material["clients"] | {"bob": {"uuid": "OLD"}}})
        self.invoke("client", "fixture", "add", "bob", "--force")
        self.assertEqual(json.loads(self.encrypted[-1]["material"])["clients"]["bob"], {"uuid": "OLD"})

    def test_add_existing_is_noop(self):
        self.invoke("client", "fixture", "add", "alice", "--force")
        self.assertEqual(self.encrypted, [])

    def test_remove_disables_public_client_but_preserves_ciphertext(self):
        self.invoke("client", "fixture", "remove", "alice", "--force")
        self.assertEqual(self.host().clients, ["default"])
        self.assertEqual((self.directory / "secrets.yaml").read_bytes(), CIPHERTEXT)
        self.assertEqual(self.encrypted, [])
        self.remote.assert_not_called()

    def test_default_diagnostic_client_cannot_be_removed(self):
        with self.assertRaisesRegex(V.Error, "default diagnostic"):
            self.invoke("client", "fixture", "remove", "default", "--force")

    def test_sync_only_generates_missing_clients(self):
        public = self.host().public
        public["clients"].append("bob")
        self.write(self.directory / "vless.json", public)
        self.invoke("client", "fixture", "sync", "--force")
        clients = json.loads(self.encrypted[-1]["material"])["clients"]
        self.assertEqual(clients["alice"]["uuid"], "ALICE-UUID")
        self.assertIn("bob", clients)

    def test_invalid_client_and_sync_arguments_make_no_change(self):
        for argv in (("add", "../bad"), ("add",), ("sync", "alice"), ("link", "disabled")):
            with self.subTest(argv=argv), self.assertRaises(V.Error):
                self.invoke("client", "fixture", *argv, "--force")
        self.assertEqual(self.encrypted, [])

    def test_rotation_changes_only_selected_uuid(self):
        self.invoke("client", "fixture", "rotate", "alice", "--force")
        rotated = json.loads(self.encrypted[-1]["material"])
        self.assertNotEqual(rotated["clients"]["alice"], self.material["clients"]["alice"])
        rotated["clients"]["alice"] = self.material["clients"]["alice"]
        self.assertEqual(rotated, self.material)

    def test_rotation_refusal_makes_no_change(self):
        with mock.patch.object(O.os, "isatty", return_value=True), mock.patch("builtins.input", return_value="no"):
            with self.assertRaises(V.Error):
                self.invoke("client", "fixture", "rotate", "alice")
        self.assertEqual(self.encrypted, [])

    def test_overwrite_refusal_precedes_all_writes(self):
        config = (self.directory / "vless.json").read_bytes()
        with mock.patch.object(V, "confirm_overwrite", side_effect=[None, V.Error("declined")]):
            with self.assertRaises(V.Error):
                self.invoke("client", "fixture", "add", "bob")
        self.assertEqual((self.directory / "vless.json").read_bytes(), config)
        self.assertEqual((self.directory / "secrets.yaml").read_bytes(), CIPHERTEXT)

    def test_link_explicitly_exports_only_selected_link(self):
        self.invoke("client", "fixture", "link", "alice")
        link = self.output.getvalue().strip()
        self.assertTrue(link.startswith("vless://ALICE-UUID@203.0.113.8:443?"))
        self.assertIn("sni=example.org", link)
        self.assertTrue(link.endswith("#fixture-alice"))
        self.assertNotIn("DEFAULT-UUID", link)
        self.assertNotIn("PRIVATE", link)
        self.assertEqual(self.encrypted, [])

    def test_link_address_override(self):
        self.invoke("client", "fixture", "link", "alice", "--address", "203.0.113.9")
        self.assertIn("@203.0.113.9:443", self.output.getvalue())

    def test_telegram_hidden_token_and_signed_chat_id(self):
        with mock.patch.object(O.getpass, "getpass", return_value=TOKEN) as prompt:
            self.invoke("telegram", "fixture", "set", "--chat-id", "-100123", "--force")
        prompt.assert_called_once()
        self.assertEqual(self.encrypted[-1], {"bot_token": TOKEN})
        self.assertEqual(self.host().settings()["chatId"], -100123)
        self.assertNotIn(TOKEN, self.output.getvalue())
        self.remote.assert_not_called()

    def test_telegram_chat_only_preserves_existing_token(self):
        (self.directory / "monitoring.yaml").write_bytes(CIPHERTEXT)
        with mock.patch.object(O.getpass, "getpass", return_value=""):
            self.invoke("telegram", "fixture", "set", "--chat-id", "123", "--force")
        self.assertEqual(self.encrypted[-1]["bot_token"], TOKEN)

    def test_telegram_disable_preserves_encrypted_token(self):
        (self.directory / "monitoring.yaml").write_bytes(CIPHERTEXT)
        self.invoke("telegram", "fixture", "disable", "--force")
        self.assertEqual(self.host().settings()["chatId"], 0)
        self.assertEqual((self.directory / "monitoring.yaml").read_bytes(), CIPHERTEXT)
        self.assertEqual(self.encrypted, [])

    def test_invalid_token_chat_and_token_argv_rejected(self):
        for chat in ("0", str(2**63), str(-(2**63) - 1)):
            with self.subTest(chat=chat), self.assertRaises(V.Error):
                self.invoke("telegram", "fixture", "set", "--chat-id", chat, "--force")
        with mock.patch.object(O.getpass, "getpass", return_value="secret-invalid"):
            with self.assertRaises(V.Error):
                self.invoke("telegram", "fixture", "set", "--chat-id", "1", "--force")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.args("telegram", "fixture", "set", "--token", TOKEN)
        self.assertEqual(self.encrypted, [])

    def test_dynamic_recipients_checked_without_lt1_admin_constant(self):
        host = self.host()
        self.assertEqual(set(host.recipients()), {ADMIN, HOST})
        (self.directory / "secrets.yaml").write_text(CIPHERTEXT.decode().replace(HOST, "age1" + "c" * 58))
        with self.assertRaises(V.Error):
            self.host().recipients()

    def test_real_encrypt_uses_explicit_recipients_and_stdin_only(self):
        host = self.host()
        self.run.return_value = CIPHERTEXT
        self.assertEqual(REAL_ENCRYPT(host, {"bot_token": TOKEN}), CIPHERTEXT)
        command = self.run.call_args.args[0]
        self.assertEqual(command[command.index("--age") + 1], ",".join(sorted([ADMIN, HOST])))
        self.assertNotIn(TOKEN, " ".join(str(a) for a in command))
        self.assertEqual(json.loads(self.run.call_args.args[1]), {"bot_token": TOKEN})
        self.run.return_value = CIPHERTEXT.replace(HOST.encode(), ("age1" + "c" * 58).encode())
        with self.assertRaises(V.Error):
            REAL_ENCRYPT(host, {"bot_token": TOKEN})

    def test_decrypt_uses_saved_identity_but_allows_explicit_override(self):
        identity = Path(self.tmp.name) / "age.txt"
        identity.write_text("TEST IDENTITY")
        identity.chmod(0o600)
        host = self.host()
        host.credentials["admin_identity"] = str(identity)
        self.run.return_value = b'{"material": "{}"}'
        with mock.patch.dict(O.os.environ, {}, clear=True):
            REAL_DECRYPT(host, host.secret)
            self.assertEqual(self.run.call_args.kwargs["env"]["SOPS_AGE_KEY_FILE"], str(identity))
        with mock.patch.dict(O.os.environ, {"SOPS_AGE_KEY_FILE": "/explicit"}, clear=True):
            REAL_DECRYPT(host, host.secret)
            self.assertEqual(self.run.call_args.kwargs["env"]["SOPS_AGE_KEY_FILE"], "/explicit")

    def test_hidden_token_failure_cannot_write_or_echo_token(self):
        with mock.patch.object(O.getpass, "getpass", side_effect=O.getpass.GetPassWarning("unavailable")):
            with self.assertRaisesRegex(V.Error, "hidden input"):
                self.invoke("telegram", "fixture", "set", "--chat-id", "1", "--force")
        self.assertEqual(self.encrypted, [])
        self.assertFalse((self.directory / "monitoring.yaml").exists())

    def test_production_connection_uses_admin_not_source_key(self):
        args = self.args("ssh", "fixture")
        with O.administrative_connection(V, args, self.host()) as c:
            self.assertEqual(c["key"], str(self.key))
            self.assertEqual(c["user"], "ops")
            self.assertEqual(c["port"], 3333)
            known = Path(c["known_hosts"])
            self.assertIn("[203.0.113.8]:3333 ssh-ed25519 AAAATEST", known.read_text())
            self.assertIn("StrictHostKeyChecking=yes", V.ssh_args(c))
        self.assertFalse(known.exists())
        self.assertFalse((self.directory / "installed-known-hosts").exists())

    def test_connection_overrides_and_public_key_rejection(self):
        known = Path(self.tmp.name) / "known_hosts"
        known.write_text("PIN")
        args = self.args("ssh", "fixture", "--user", "old-admin", "--ssh-port", "2222", "--address", "203.0.113.9",
                         "--known-hosts", str(known))
        with O.administrative_connection(V, args, self.host()) as c:
            self.assertEqual((c["user"], c["port"], c["address"]), ("old-admin", 2222, "203.0.113.9"))
            self.assertEqual(c["known_hosts"], str(known))
        args.key = Path(str(self.key) + ".pub")
        with self.assertRaises(V.Error):
            with O.administrative_connection(V, args, self.host()):
                pass

    def test_tunnel_only_binds_and_forwards_loopback(self):
        for service, remote in O.UIS.items():
            self.invoke("tunnel", "fixture", service, "--local-port", "13000")
            command = self.remote.call_args.args[0]
            self.assertIn("127.0.0.1:13000:127.0.0.1:" + str(remote), command)
            self.assertIn("ExitOnForwardFailure=yes", command)
            self.assertIn("ForkAfterAuthentication=no", command)
            self.assertIn("ControlPath=none", command)
            self.assertIn("GatewayPorts=no", command)
            self.assertIn("-N", command)
            self.assertIn("-T", command)
            self.assertNotIn("-g", command)
        self.assertFalse((self.repo / ".vps.lock").exists())

    def test_read_only_operations_are_not_blocked_by_mutation_lock(self):
        with (self.repo / ".vps.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.invoke("status", "fixture")
            self.invoke("logs", "fixture", "grafana", "--follow", "--lines", "50")
            self.invoke("ssh", "fixture")
            with self.assertRaises(V.Error):
                self.invoke("client", "fixture", "add", "bob", "--force")
        self.assertIn("-t", self.remote.call_args.args[0])

    def test_logs_and_restart_use_allowlisted_services(self):
        self.invoke("logs", "fixture", "grafana", "--follow", "--lines", "50")
        self.assertIn("journalctl --no-pager -u grafana.service -n 50 --follow", self.remote.call_args.args[0][-1])
        self.invoke("restart", "fixture", "xray", "--force")
        self.assertEqual(self.remote.call_args.args[0][-1], "sudo -n systemctl restart xray.service")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.args("restart", "fixture", "xray;reboot")
        with self.assertRaises(V.Error):
            self.invoke("logs", "fixture", "--lines", "0")

    def test_restart_refusal_does_not_connect(self):
        with mock.patch.object(O.os, "isatty", return_value=True), mock.patch("builtins.input", return_value="no"):
            with self.assertRaises(V.Error):
                self.invoke("restart", "fixture", "xray")
        self.remote.assert_not_called()

    def test_runtime_validation_uses_private_temporary_file_and_cleans_it(self):
        renderer = SimpleNamespace(render=mock.Mock(return_value={"private": "TEST_SECRET"}))
        files = []
        def run(command, **kwargs):
            path = Path(command[-1])
            files.append(path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(json.loads(path.read_text()), {"private": "TEST_SECRET"})
            raise V.Error("Xray rejected candidate")
        with mock.patch.object(V, "load_renderer", return_value=renderer), mock.patch.object(V, "run", side_effect=run):
            with self.assertRaises(V.Error):
                O.validate_runtime(V, self.host())
        self.assertFalse(files[0].exists())
        self.assertFalse(files[0].parent.exists())

    def test_runtime_validation_rejects_unwired_clients_or_telegram(self):
        renderer = SimpleNamespace(render=mock.Mock(return_value={}))
        for mismatch in ("clients", "telegram"):
            def evaluate(repo, name, attribute, raw=False):
                if mismatch == "clients" and attribute == "services.xray.declarative.settings":
                    return {"clients": ["default"]}
                return self.evaluate(repo, name, attribute, raw)
            if mismatch == "telegram":
                settings = self.host().settings() | {"chatId": 123}
                self.write(self.directory / "machine.json", settings)
            with self.subTest(mismatch=mismatch), mock.patch.object(V, "load_renderer", return_value=renderer), \
                    mock.patch.object(V, "evaluate", side_effect=evaluate), self.assertRaises(V.Error):
                O.validate_runtime(V, self.host())
        self.run.assert_not_called()

    def test_runtime_validation_rejects_other_ciphertext_or_endpoint(self):
        other = self.directory / "other.yaml"
        other.write_bytes(b"OTHER HOST")
        for mismatch in ("ciphertext", "endpoint"):
            def evaluate(repo, name, attribute, raw=False):
                if mismatch == "ciphertext" and attribute == "services.xray.declarative.secretFile":
                    return str(other)
                if mismatch == "endpoint" and attribute == "services.xray.declarative.settings":
                    return self.host().public | {"serverName": "other.example"}
                return self.evaluate(repo, name, attribute, raw)
            with self.subTest(mismatch=mismatch), mock.patch.object(V, "evaluate", side_effect=evaluate), self.assertRaises(V.Error):
                O.validate_runtime(V, self.host())
        self.run.assert_not_called()

    def test_source_fingerprint_tracks_config_not_reports(self):
        PREPARE_INPUTS(self.repo, "fixture")
        host = self.host()
        before = O.fingerprint(V, host)
        self.write(self.directory / "verified.json", {"test": "report"})
        self.assertEqual(O.fingerprint(V, host), before)
        lock = self.repo / "flake.lock"
        previous = lock.read_bytes()
        changed = json.loads(previous)
        changed['nodes']['core']['locked']['rev'] = 'c' * 40
        self.write(lock, changed)
        with self.assertRaises(V.Error):
            O.fingerprint(V, host)
        lock.write_bytes(previous)
        self.assertEqual(O.fingerprint(V, host), before)
        self.write(self.directory / "machine.json", host.settings() | {"chatId": 123})
        self.assertNotEqual(O.fingerprint(V, host), before)

    def test_apply_check_has_no_ssh_copy_switch_or_confirmation(self):
        with mock.patch.object(O, "fingerprint", return_value="same"), mock.patch.object(O, "validate_runtime") as validate:
            self.run.return_value = SYSTEM.encode()
            self.invoke("apply", "fixture", "--check")
        validate.assert_called_once()
        self.assertEqual(self.run.call_count, 1)
        self.assertEqual(self.run.call_args.args[0][1], "build")
        self.remote.assert_not_called()

    def test_apply_build_failure_and_stale_sources_prevent_ssh(self):
        for failure in ("build", "stale"):
            with self.subTest(failure=failure), mock.patch.object(O, "fingerprint", side_effect=["before", "after"]), \
                    mock.patch.object(O, "validate_runtime"), mock.patch.object(O, "administrative_connection") as connection:
                self.run.side_effect = V.Error("build failed") if failure == "build" else None
                self.run.return_value = SYSTEM.encode()
                with self.assertRaises(V.Error):
                    self.invoke("apply", "fixture", "--force")
                connection.assert_not_called()
        self.run.side_effect = None

    def test_apply_refusal_never_copies_or_switches(self):
        self.run.return_value = SYSTEM.encode()
        with mock.patch.object(O, "fingerprint", return_value="same"), mock.patch.object(O, "validate_runtime"), \
                mock.patch.object(O.os, "isatty", return_value=True), mock.patch("builtins.input", return_value="no"):
            with self.assertRaises(V.Error):
                self.invoke("apply", "fixture")
        self.assertEqual(self.run.call_count, 1)

    def test_apply_copy_is_pinned_and_switch_is_explicit_without_disko(self):
        commands = []
        def run(command, **kwargs):
            commands.append(command)
            if command[1] == "build":
                return SYSTEM.encode()
            if command[1] == "copy":
                wrapper = Path(kwargs["env"]["PATH"].split(os.pathsep)[0]) / "ssh"
                text = wrapper.read_text()
                self.assertIn("StrictHostKeyChecking=yes", text)
                self.assertIn("-p 3333", text)
                self.assertNotIn("exec ssh ", text)
                self.assertIn("--no-check-sigs", command)
            return b""
        with mock.patch.object(O, "fingerprint", return_value="same"), mock.patch.object(O, "validate_runtime"), \
                mock.patch.object(V, "run", side_effect=run), mock.patch.object(O.shutil, "which", return_value="/bin/ssh"):
            self.invoke("apply", "fixture", "--force")
        joined = "\n".join(" ".join(str(a) for a in c) for c in commands)
        self.assertIn("switch-to-configuration switch", joined)
        self.assertNotIn("disko", joined)
        self.assertNotIn("nixos-anywhere", joined)
        self.assertNotIn("root@", joined)

    def test_copy_failure_or_source_change_prevents_activation(self):
        for failure in ("copy", "stale"):
            calls = []
            def run(command, **kwargs):
                calls.append(command)
                if command[1] == "build":
                    return SYSTEM.encode()
                if command[1] == "copy" and failure == "copy":
                    raise V.Error("copy failed")
                return b""
            with self.subTest(failure=failure), mock.patch.object(O, "fingerprint", side_effect=["a", "a", "b"]), \
                    mock.patch.object(O, "validate_runtime"), mock.patch.object(V, "run", side_effect=run):
                with self.assertRaises(V.Error):
                    self.invoke("apply", "fixture", "--force")
            self.assertFalse(any("switch-to-configuration" in str(c) for c in calls))

    def test_native_host_local_operations_and_telegram(self):
        native = self.repo / "hosts/native"
        self.write(native / "vless.json", self.host().public)
        self.write(native / "operations.json", {"chatId": 0})
        secret = self.repo / "secrets/hosts/native.yaml"
        secret.parent.mkdir(parents=True)
        secret.write_bytes(CIPHERTEXT)
        self.invoke("client", "native", "add", "bob")
        self.assertIn("bob", json.loads((native / "vless.json").read_text())["clients"])
        with mock.patch.object(O.getpass, "getpass", return_value=TOKEN):
            self.invoke("telegram", "native", "set", "--chat-id", "1")
        self.assertEqual(json.loads((native / "operations.json").read_text())["chatId"], 1)
        self.assertTrue((secret.parent / "native-monitoring.yaml").is_file())

    def test_native_ssh_requires_key_and_uses_profile_port_not_legacy_3333(self):
        self.write(self.repo / "hosts/native/vless.json", self.host().public)
        with self.assertRaises(V.Error):
            self.invoke("ssh", "native", "--address", "203.0.113.9")
        self.invoke("ssh", "native", "--key", str(self.key), "--address", "203.0.113.9")
        command = self.remote.call_args.args[0]
        self.assertEqual(command[command.index("-p") + 1], "22")
        self.assertIn("operator@203.0.113.9", command)

    def test_foreground_process_terminated_on_interruption(self):
        process = mock.Mock()
        process.wait.side_effect = [KeyboardInterrupt(), 0]
        process.poll.return_value = None
        with mock.patch.object(O.subprocess, "Popen", return_value=process), self.assertRaises(KeyboardInterrupt):
            REAL_ATTACHED(["ssh"])
        process.terminate.assert_called_once()

    def test_main_dispatch_list_works_without_creating_workflow_state(self):
        with mock.patch.object(V.sys, "argv", ["vps-vless", "--repo", str(self.repo), "list"]), \
                contextlib.redirect_stdout(self.output):
            self.assertEqual(V.main(), 0)
        self.assertEqual(json.loads(self.output.getvalue())[0]["host"], "fixture")

    def test_list_error_without_host_is_handled_not_secondary_crash(self):
        (self.repo / "hosts/generated/fixture/vless.json").write_text("invalid")
        with mock.patch.object(V.sys, "argv", ["vps-vless", "--repo", str(self.repo), "list"]), \
                contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(V.main(), 1)
        self.assertIn("failure category", stderr.getvalue())


@unittest.skipUnless(all(O.shutil.which(t) for t in ("sops", "age-keygen", "openssl")),
                     "real SOPS/age/OpenSSL are available in the Nix check")
class SopsIntegrationTests(unittest.TestCase):
    def test_real_encrypted_client_and_telegram_workflow(self):
        with tempfile.TemporaryDirectory(prefix="vps-vless-integration-") as directory:
            root = Path(directory)
            repo = root / "checkout"
            host_dir = repo / "hosts/generated/fixture"
            host_dir.mkdir(parents=True)
            (repo / "flake.nix").write_text("{}")
            identities, recipients = [], []
            for name in ("admin", "host"):
                identity = root / (name + ".age")
                subprocess.run(["age-keygen", "-o", str(identity)], capture_output=True, check=True)
                recipients.append(subprocess.run(["age-keygen", "-y", str(identity)],
                                  capture_output=True, check=True).stdout.decode().strip())
                identities.append(identity)
            generator = V.load(ROOT / "vps-credentials.py", "integration_generator")
            private, public = generator.keypair()
            material = {"private_key": private, "public_key": public, "short_id": "aabbccdd",
                        "clients": {"default": {"uuid": str(uuid.uuid4())}}}
            payload = {"material": json.dumps(material), "warp": "PRESERVED_WARP"}
            ciphertext = subprocess.run(["sops", "--config", "/dev/null", "encrypt", "--age", ",".join(recipients),
                          "--input-type", "json", "--output-type", "yaml", "/dev/stdin"],
                          input=json.dumps(payload).encode(), capture_output=True, check=True).stdout
            (host_dir / "secrets.yaml").write_bytes(ciphertext)
            (host_dir / "vless.json").write_bytes(O.json_bytes({"serverName": "example.org", "target": "example.org:443", "clients": ["default"]}))
            (host_dir / "machine.json").write_bytes(O.json_bytes({"chatId": 0}))
            (host_dir / "credentials.json").write_bytes(O.json_bytes({"admin_recipient": recipients[0], "host_recipient": recipients[1],
                                                                   "admin_identity": str(identities[0])}))
            output = io.StringIO()
            def invoke(*argv):
                args = V.parser().parse_args([*argv, "--force"])
                with contextlib.redirect_stdout(output):
                    return O.dispatch(V, args, repo, None)
            # Explicit identity beats any operator-specific environment in the harness.
            with mock.patch.dict(O.os.environ, {"SOPS_AGE_KEY_FILE": str(identities[0])}):
                invoke("client", "fixture", "add", "alice")
                host = O.Host(V, repo, "fixture")
                decoded = host.decrypt(host.secret)
                new_material = json.loads(decoded["material"])
                self.assertEqual(new_material["private_key"], private)
                self.assertEqual(new_material["clients"]["default"], material["clients"]["default"])
                self.assertEqual(decoded["warp"], "PRESERVED_WARP")
                self.assertEqual(set(host.recipients()), set(recipients))
                alice = new_material["clients"]["alice"]["uuid"]
                invoke("client", "fixture", "link", "alice", "--address", "203.0.113.8")
                self.assertIn("vless://" + alice + "@203.0.113.8:443?", output.getvalue())
                self.assertNotIn(private, output.getvalue())
                unchanged = host.secret.read_bytes()
                invoke("client", "fixture", "sync")
                self.assertEqual(host.secret.read_bytes(), unchanged)
                invoke("client", "fixture", "remove", "alice")
                self.assertEqual(host.secret.read_bytes(), unchanged)
                invoke("client", "fixture", "add", "alice")
                self.assertEqual(json.loads(host.decrypt(host.secret)["material"])["clients"]["alice"]["uuid"], alice)
                invoke("client", "fixture", "rotate", "alice")
                self.assertNotEqual(json.loads(host.decrypt(host.secret)["material"])["clients"]["alice"]["uuid"], alice)
                with mock.patch.object(O.getpass, "getpass", return_value=TOKEN):
                    invoke("telegram", "fixture", "set", "--chat-id", "-100123")
                self.assertEqual(host.decrypt(host.monitoring)["bot_token"], TOKEN)
                self.assertEqual(host.settings()["chatId"], -100123)
                self.assertNotIn(TOKEN, host.monitoring.read_text())
                self.assertNotIn(TOKEN, output.getvalue())
                monitor = host.monitoring.read_bytes()
                invoke("telegram", "fixture", "disable")
                self.assertEqual(host.settings()["chatId"], 0)
                self.assertEqual(host.monitoring.read_bytes(), monitor)


if __name__ == "__main__":
    unittest.main()
