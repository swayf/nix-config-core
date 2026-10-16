#!/usr/bin/env python3
"""Offline source-VPS WARP protocol tests; no SSH, Cloudflare requests or accounts."""
import base64
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SOURCE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V = load("vps", "vps.py")
W = load("vps_warp", "vps-warp.py")
HELPER = (SOURCE / "vps-wgcf.py").read_text()
KEY = base64.b64encode(bytes(range(32))).decode()
PROFILE = f"""[Interface]
PrivateKey = {KEY}
Address = 172.16.0.2/32
[Peer]
PublicKey = {KEY}
Endpoint = engage.cloudflareclient.com:2408
"""
ACCOUNT = "SECRET-ACCOUNT-TOKEN"


def elf(program_type=1, machine=62):
    ident = b"\x7fELF\x02\x01\x01" + bytes(9)
    header = struct.pack("<16sHHIQQQIHHHHHH", ident, 2, machine, 1, 0, 64, 0, 0, 64, 56, 1, 0, 0, 0)
    return header + struct.pack("<I", program_type) + bytes(52)


class RemoteWarpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binary = self.root / "wgcf"
        self.binary.write_bytes(elf())
        self.payload = W.request(self.binary, HELPER)
        self.c = V.connection("203.0.113.5", "root", 2222, "/source-key", "/verified-hosts")

    def test_portable_binary_required_no_glibc_or_wrong_architecture(self):
        for binary in (b"not ELF", elf(3), elf(2), elf(machine=183), elf()[:65]):
            with self.subTest(binary=binary[:20]), self.assertRaises(W.WorkerError):
                W.portable_binary(binary)
        W.portable_binary(elf())

    def test_worker_uses_private_files_vps_environment_and_cleans_on_success(self):
        paths = []
        def run(command, **options):
            root = Path(options["cwd"])
            paths.append(root)
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            worker = root.parent
            self.assertEqual((worker / "wgcf").stat().st_mode & 0o777, 0o700)
            self.assertEqual((worker / "lt1-warp.py").stat().st_mode & 0o777, 0o600)
            self.assertEqual(os.environ["PATH"].split(":")[0], str(worker))
            self.assertEqual(os.environ["HTTPS_PROXY"], "https://vps-proxy.test")
            self.assertNotIn("WGCF_ACCESS_TOKEN", os.environ)
            self.assertNotIn("SOPS_AGE_KEY", os.environ)
            self.assertIs(options["stdin"], subprocess.DEVNULL)
            self.assertIs(options["stdout"], subprocess.PIPE)
            self.assertIs(options["stderr"], subprocess.PIPE)
            if command[1] == "register":
                self.assertIn("--accept-tos", command)
                (root / "wgcf-account.toml").write_text(ACCOUNT)
            else:
                (root / "wgcf-profile.conf").write_text(PROFILE)
            return subprocess.CompletedProcess(command, 0, ACCOUNT.encode(), b"SECRET-LOG")
        with patch.dict(os.environ, {"HTTPS_PROXY": "https://vps-proxy.test", "SOPS_AGE_KEY": "SECRET-ADMIN-KEY",
                                     "WGCF_ACCESS_TOKEN": "SECRET-OLD-ACCOUNT"}):
            original = dict(os.environ)
            with patch.object(subprocess, "run", side_effect=run) as tool:
                value = W.execute(self.payload)
            self.assertEqual(dict(os.environ), original)
        self.assertEqual(tool.call_count, 2)
        self.assertTrue(value["ok"])
        settings, account = W.response(json.dumps(value).encode())
        self.assertEqual(account, ACCOUNT)
        self.assertEqual(settings["secretKey"], KEY)
        self.assertTrue(paths and all(not path.exists() and not path.parent.exists() for path in paths))

    def test_failed_registration_keeps_diagnostics_not_secrets_and_cleans(self):
        paths = []
        def run(command, **options):
            root = Path(options["cwd"])
            paths.append(root)
            (root / "wgcf-account.toml").write_text(ACCOUNT)
            return subprocess.CompletedProcess(command, 1, ACCOUNT.encode(), b"403 Forbidden SECRET-TOKEN")
        with patch.object(subprocess, "run", side_effect=run) as tool, self.assertRaises(W.WorkerError) as error:
            W.execute(self.payload)
        tool.assert_called_once()
        self.assertIn("HTTP 403", str(error.exception))
        self.assertEqual(error.exception.details["registration_location"], "source-VPS")
        self.assertTrue(error.exception.details["account_file_created"])
        self.assertNotIn("SECRET", str(error.exception) + json.dumps(error.exception.details))
        self.assertTrue(all(not path.parent.exists() for path in paths))

    def test_interruption_cleans_account_files_and_restores_environment(self):
        paths = []
        def interrupted(command, **options):
            root = Path(options["cwd"])
            paths.append(root)
            (root / "wgcf-account.toml").write_text(ACCOUNT)
            raise SystemExit(129)
        original = dict(os.environ)
        with patch.object(subprocess, "run", side_effect=interrupted), self.assertRaises(SystemExit):
            W.execute(self.payload)
        self.assertEqual(dict(os.environ), original)
        self.assertTrue(paths and all(not path.parent.exists() for path in paths))

    def test_bad_transfer_platform_and_terms_never_start_wgcf(self):
        for payload in (self.payload | {"sha256": "wrong"}, self.payload | {"accept_tos": False},
                        self.payload | {"wgcf": "invalid base64"}):
            with self.subTest(payload=payload.get("sha256")), patch.object(subprocess, "run") as run, \
                 self.assertRaises(W.WorkerError):
                W.execute(payload)
            run.assert_not_called()
        with patch.object(W.platform, "machine", return_value="aarch64"), patch.object(subprocess, "run") as run, \
             self.assertRaises(W.WorkerError) as error:
            W.execute(self.payload)
        self.assertEqual(error.exception.details["reason_code"], "remote_platform")
        run.assert_not_called()

    def test_remote_response_never_exposes_untrusted_error_text(self):
        raw = {"schema": 1, "ok": False, "error": {"reason_code": ["SECRET"], "stage": "SECRET-STAGE",
               "message": "SECRET-KEY", "stdout": ACCOUNT, "http_status": "SECRET-HTTP", "exit_code": 9999}}
        with self.assertRaises(W.WorkerError) as error:
            W.response(json.dumps(raw).encode())
        self.assertNotIn("SECRET", str(error.exception) + json.dumps(error.exception.details))
        for raw in (b"SECRET-INVALID-JSON", b'{}', b'{"schema":1,"ok":true,"settings":{},"account":""}'):
            with self.subTest(raw=raw), self.assertRaises(W.WorkerError) as error:
                W.response(raw)
            self.assertEqual(error.exception.details["reason_code"], "protocol")
            self.assertNotIn("SECRET", str(error.exception))

    def test_worker_main_returns_private_protocol_and_no_traceback_on_failure(self):
        failure = subprocess.TimeoutExpired(["wgcf", "SECRET-ARG"], 90,
                                            output=ACCOUNT.encode(), stderr=b"x509: SECRET-CERT")
        stdin = io.TextIOWrapper(io.BytesIO(json.dumps(self.payload).encode()))
        stdout = io.StringIO()
        with patch.object(W.sys, "stdin", stdin), patch.object(W.signal, "signal"), \
             patch.object(subprocess, "run", side_effect=failure) as tool, contextlib.redirect_stdout(stdout):
            self.assertEqual(W.main(), 0)
        tool.assert_called_once()
        value = json.loads(stdout.getvalue())
        self.assertFalse(value["ok"])
        self.assertTrue(value["error"]["timed_out"])
        self.assertEqual(value["error"]["reason_code"], "tls_certificate")
        self.assertNotIn("SECRET", stdout.getvalue())

    def test_parent_uses_source_ssh_and_keeps_success_response_off_terminal(self):
        value = {"schema": 1, "ok": True, "settings": {"secretKey": KEY}, "account": ACCOUNT}
        with patch.dict(os.environ, {"VPS_WGCF_BINARY": str(self.binary)}), \
             patch.object(V, "remote_run", return_value=json.dumps(value).encode()) as remote, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(V.register_warp_on_vps(self.c), (value["settings"], ACCOUNT))
        remote.assert_called_once()
        c, command, raw = remote.call_args.args
        self.assertEqual(c, self.c)
        self.assertTrue(command.startswith("python3 -c "))
        self.assertNotIn(ACCOUNT, command)
        self.assertEqual(remote.call_args.kwargs["timeout"], 300)
        payload = json.loads(raw)
        self.assertEqual(base64.b64decode(payload["wgcf"]), elf())
        self.assertNotIn("/source-key", raw.decode())
        self.assertNotIn("SOPS_AGE_KEY", payload)  # No local identity/environment fields.
        self.assertNotIn(ACCOUNT, output.getvalue())

    def test_password_ssh_prompts_once_and_uses_separate_pipe_not_worker_stdin(self):
        password = b"private-test-ssh-password"
        value = {"schema": 1, "ok": True, "settings": {}, "account": ACCOUNT}
        def ssh(argv, **options):
            self.assertEqual(os.read(options["pass_fds"][0], 2048), password + b"\n")
            self.assertNotIn(password.decode(), " ".join(argv))
            self.assertNotIn(password, options["input"])
            self.assertIn("StrictHostKeyChecking=yes", argv)
            self.assertIn("2222", argv)
            self.assertIn("root@203.0.113.5", argv)
            self.assertNotIn("env", options)
            return subprocess.CompletedProcess(argv, 0, json.dumps(value).encode(), b"SECRET-SSH-BANNER")
        with patch.dict(os.environ, {"VPS_WGCF_BINARY": str(self.binary)}), \
             patch.object(V, "prompt_ssh_password", return_value=password) as prompt, \
             patch.object(V.subprocess, "run", side_effect=ssh) as tool, contextlib.redirect_stdout(io.StringIO()):
            V.register_warp_on_vps(self.c | {"key": None})
        prompt.assert_called_once()
        tool.assert_called_once()

    def test_parent_remote_failure_has_no_local_fallback_and_private_report(self):
        value = {"schema": 1, "ok": False, "error": {"stage": "wgcf register", "reason_code": "http_error",
                 "http_status": 429, "exit_code": 1, "message": ACCOUNT}}
        with patch.dict(os.environ, {"VPS_WGCF_BINARY": str(self.binary)}), \
             patch.object(V, "remote_run", return_value=json.dumps(value).encode()) as remote, \
             patch.object(subprocess, "run") as local, contextlib.redirect_stdout(io.StringIO()), \
             self.assertRaises(V.Error) as error:
            V.register_warp_on_vps(self.c)
        remote.assert_called_once()
        local.assert_not_called()
        self.assertIn("HTTP 429", str(error.exception))
        report = V.error_report(error.exception, "secrets", "vps-test")
        self.assertEqual(report["exceptions"][0]["registration_location"], "source-VPS")
        self.assertNotIn(ACCOUNT, str(error.exception) + json.dumps(report))

    def test_missing_or_dynamic_binary_fails_before_ssh_without_fallback(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(V, "remote_run") as remote, self.assertRaises(V.Error):
            V.register_warp_on_vps(self.c)
        remote.assert_not_called()
        self.binary.write_bytes(elf(3))
        with patch.dict(os.environ, {"VPS_WGCF_BINARY": str(self.binary)}), patch.object(V, "remote_run") as remote, \
             self.assertRaises(V.Error):
            V.register_warp_on_vps(self.c)
        remote.assert_not_called()


if __name__ == "__main__":
    unittest.main()
