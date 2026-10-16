#!/usr/bin/env python3
"""Offline safety/behaviour tests. No SSH, registration or real installation."""
import argparse
import base64
import contextlib
import hashlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import struct
import tarfile
import tempfile
import unittest
import warnings
from unittest.mock import patch, Mock
import runpy

PREPARE_INPUTS = runpy.run_path(str(Path(__file__).with_name("vps-test-inputs.py")))["prepare"]

TEST_ADMIN = "age1" + "a" * 58
TEST_SOURCE_KEY = "ssh-ed25519 " + base64.b64encode(
    struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + bytes(range(32, 64))).decode()
SCRIPTS = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("vps", SCRIPTS / "vps.py")
V = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)
spec = importlib.util.spec_from_file_location("vps_probe", SCRIPTS / "vps-probe.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


def facts(dynamic=False):
    return {"schema": 2, "link_details": True, "architecture": "x86_64", "virtualization": "kvm", "firmware": "bios",
      "memory_bytes": 2 * 1024**3, "secure_boot": False, "machine_id": "test-machine-id",
      "ssh_host_public_key": TEST_SOURCE_KEY + " source", "os": "ubuntu", "installer": False,
      "kexec_disabled": "0", "cpu_count": 2,
      "disks": [{"name": "/dev/vda", "type": "disk", "size": 25 * 1024**3, "model": "VIRTUAL",
                 "serial": "synthetic-disk", "wwn": None, "ro": False, "rm": False,
                 "children": [{"name": "/dev/vda1", "type": "part", "mountpoints": ["/"]}]}],
      "links": [{"ifname": "ens3", "link_type": "ether", "address": "52:54:00:12:34:56"}],
      "addresses": [{"ifname": "ens3", "addr_info": [{"family": "inet", "local": "203.0.113.5",
                    "prefixlen": 32, "scope": "global", "dynamic": dynamic,
                    "valid_life_time": 300 if dynamic else 4294967295}]}],
      "routes": [{"dst": "default", "gateway": "169.254.0.1", "dev": "ens3"},
                 {"dst": "169.254.0.1/32", "dev": "ens3", "scope": "link"}],
      "rules": [{"priority": 0, "src": "all"}, {"priority": 32766, "src": "all"}, {"priority": 32767, "src": "all"}],
      "extra_routes": [], "dns": ["1.1.1.1"], "hardware_configuration": ""}


class RendererBindingTests(unittest.TestCase):
    def test_source_execution_uses_tool_tree_not_selected_repo(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(V, "load", return_value="renderer") as loader:
            self.assertEqual(V.load_renderer(), "renderer")
        loader.assert_called_once_with(V.SOURCE.parent / "modules/nixos/xray-render.py", "vps_renderer")

    def test_packaged_renderer_is_explicit_and_shared_by_operations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "xray-render.py"
            path.write_text("def render(*args): return 'pinned renderer'\n")
            with patch.dict(os.environ, {"VPS_XRAY_RENDERER": str(path)}):
                renderer = V.load_renderer("vps_vless_renderer")
                self.assertEqual(renderer.render(), "pinned renderer")

    def test_relative_renderer_is_rejected_without_load(self):
        with patch.dict(os.environ, {"VPS_XRAY_RENDERER": "relative.py"}), patch.object(V, "load") as loader:
            with self.assertRaisesRegex(V.Error, "absolute"):
                V.load_renderer()
        loader.assert_not_called()

    def test_missing_pinned_renderer_never_falls_back(self):
        with patch.dict(os.environ, {"VPS_XRAY_RENDERER": "/missing-tool-renderer.py"}), \
                patch.object(V, "load", side_effect=FileNotFoundError) as loader:
            with self.assertRaisesRegex(V.Error, "no checkout fallback"):
                V.load_renderer()
        loader.assert_called_once_with(Path("/missing-tool-renderer.py"), "vps_renderer")


class HelpTests(unittest.TestCase):
    def help_text(self, parser, argv):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as exit:
            parser.parse_args(argv + ["--help"])
        self.assertEqual(exit.exception.code, 0)
        return output.getvalue()

    def test_all_commands_show_every_optional_parameter_default(self):
        parser = V.parser()
        commands = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction)).choices
        self.assertEqual(set(commands), {"probe", "plan", "secrets", "check", "install", "verify",
                                         "list", "client", "telegram", "ssh", "tunnel", "status", "logs", "restart", "apply"})
        for command, current in [(None, parser), *commands.items()]:
            text = self.help_text(parser, [command] if command else [])
            for action in current._actions:
                if not action.option_strings or action.default == argparse.SUPPRESS:
                    continue
                with self.subTest(command=command, option=action.option_strings[0]):
                    start = text.index("\n  " + action.option_strings[0])
                    end = text.find("\n  -", start + 1)
                    block = " ".join(text[start:end if end != -1 else None].split())
                    self.assertIn("(default:", block)
                    if action.default is not None:
                        # argparse can wrap a hyphenated path inside its default.
                        self.assertIn(f"(default: {action.default})".replace(" ", ""), block.replace(" ", ""))
                    else:
                        self.assertNotIn("(default: None)", block)

    def test_every_command_accepts_force_for_overwrite_confirmation(self):
        for argv in (["probe", "fixture", "203.0.113.5"], ["plan", "fixture", "/admin.pub"],
                     ["secrets", "fixture"], ["check", "fixture"], ["install", "fixture"], ["verify", "fixture"]):
            with self.subTest(command=argv[0]):
                self.assertFalse(V.parser().parse_args(argv).force)
                self.assertTrue(V.parser().parse_args(argv + ["--force"]).force)
                self.assertIn("--force", self.help_text(V.parser(), [argv[0]]))

    def test_runtime_fallbacks_are_explained(self):
        parser = V.parser()
        expected = {
            "probe": ["default: prompt for an SSH password"],
            "plan": ["default: probe candidates and select interactively", "empty list uses probed DNS",
                     "append to the default list"],
            "secrets": ["default: ~/.ssh/HOST/", "default: reuse an existing host-specific age identity or offer creation"],
            "verify": ["default: first adminKeyFiles path without .pub; legacy plans require --key",
                       "default: https://www.cloudflare.com/cdn-cgi/trace, then https://api.ipify.org; "
                       "first endpoint not routed through WARP"],
        }
        for command, descriptions in expected.items():
            text = " ".join(self.help_text(parser, [command]).split())
            for description in descriptions:
                with self.subTest(command=command, description=description):
                    self.assertIn(description, text)

    def test_environment_dependent_defaults_match_help(self):
        for env, repo in [({}, "/checkout"),
                          ({"VPS_FLAKE": "/flake", "XDG_STATE_HOME": "/state"}, "/flake")]:
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True), \
                 patch.object(V.os, "getcwd", return_value="/checkout"), \
                 patch.object(V.Path, "home", return_value=Path("/home/test")):
                parser = V.parser()
                args = parser.parse_args(["check", "vps-test"])
                self.assertEqual(args.repo, Path(repo))
                self.assertIsNone(args.state_dir)
                text = " ".join(self.help_text(parser, []).split())
                self.assertIn(f"(default: {repo})", text)
                self.assertIn("default: XDG_STATE_HOME/vps; existing legacy state stays in REPO/hosts/generated until explicit copy; HOST is appended", text)
                text = " ".join(self.help_text(parser, ["probe"]).split())
                self.assertIn("(default: /home/test/.ssh/known_hosts)", text)


class HostArtifactTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        (self.repo / "flake.nix").write_text("{}")
        (self.repo / "hosts").mkdir()
        (self.repo / "hosts/generated.nix").write_text("{}")
        (self.repo / "hosts/wsl-inventory.json").write_text("{}")

    def args(self, *argv):
        return V.parser().parse_args(["--repo", str(self.repo), "--state-dir", str(self.repo / "hosts/generated"), *argv])

    def test_probe_and_plan_share_host_directory_without_existing_registry(self):
        args = self.args("probe", "vps-test", "203.0.113.5", "--key", "/source-key")
        repo, state, host = V.host_paths(args)
        self.assertEqual(state, host)
        self.assertEqual(host, self.repo / "hosts/generated/vps-test")
        with patch.object(V, "confirm_probe_host_key"), patch.object(V, "inventory", return_value=facts()), \
             contextlib.redirect_stdout(io.StringIO()):
            V.probe(args, repo, state, host)
            planned = self.args("plan", "vps-test", str(Path(self.tmp.name) / "admin.pub"), "--target", "target.test")
            self.assertEqual(V.host_paths(planned), (repo, state, host))
            V.plan(planned, repo, state, host)
        self.assertEqual({path.name for path in host.iterdir()}, set(V.HOST_CONFIG_FILES) | {"probe.json", "plan.json"})
        self.assertEqual((host / "probe.json").stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(V.Error, "plan already exists"):
            V.plan(planned, repo, state, host)

    def test_cancelled_plan_preserves_probe_and_does_not_register_host(self):
        args = self.args("plan", "vps-test", "/admin.pub")
        repo, state, host = V.host_paths(args)
        observed = {"connection": {}, "facts": facts()}
        V.write_json(state / "probe.json", observed)
        with patch.object(V, "choose_target", side_effect=V.Error("cancelled")), self.assertRaises(V.Error):
            V.plan(args, repo, state, host)
        self.assertEqual(V.read_json(state / "probe.json"), observed)
        self.assertFalse((host / "plan.json").exists())
        self.assertFalse((host.parent / "inventory.json").exists())

    def test_explicit_state_override_remains_supported(self):
        root = Path(self.tmp.name) / "legacy"
        args = self.args("--state-dir", str(root), "check", "vps-test")
        repo, state, host = V.host_paths(args)
        self.assertEqual(state, root / "vps-test")
        self.assertEqual(host, repo / "hosts/generated/vps-test")
        self.assertFalse(host.exists())

    def test_plan_asks_about_existing_registry_before_creating_host_files(self):
        registry = self.repo / "hosts/generated/inventory.json"
        registry.parent.mkdir()
        registry.write_text("{}\n")
        args = self.args("plan", "vps-test", "/admin.pub", "--target", "target.test")
        repo, state, host = V.host_paths(args)
        V.write_json(state / "probe.json", {"connection": {}, "facts": facts()})
        with patch.object(V.sys.stdin, "isatty", return_value=True), \
             patch("builtins.input", return_value="n") as prompt, self.assertRaises(V.Error):
            V.plan(args, repo, state, host)
        prompt.assert_called_once()
        self.assertEqual(registry.read_text(), "{}\n")
        self.assertEqual({p.name for p in host.iterdir()}, {"probe.json"})
        with patch.object(V.sys.stdin, "isatty", return_value=True), \
             patch("builtins.input", return_value="y") as prompt, contextlib.redirect_stdout(io.StringIO()):
            V.plan(args, repo, state, host)
        prompt.assert_called_once()
        self.assertIn(str(registry), prompt.call_args.args[0])
        self.assertEqual(V.read_json(registry), {"vps-test": {"system": "x86_64-linux"}})

    def test_plan_force_reports_registry_overwrite_without_prompt(self):
        args = self.args("plan", "vps-test", "/admin.pub", "--target", "target.test", "--force")
        repo, state, host = V.host_paths(args)
        registry = host.parent / "inventory.json"
        registry.write_text("{}\n")
        V.write_json(state / "probe.json", {"connection": {}, "facts": facts()})
        with patch.object(V.sys.stdin, "isatty", return_value=False), patch("builtins.input") as prompt, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            V.plan(args, repo, state, host)
        prompt.assert_not_called()
        self.assertEqual(V.read_json(registry), {"vps-test": {"system": "x86_64-linux"}})
        self.assertIn(str(registry), output.getvalue())
        self.assertEqual(output.getvalue().count("automatically accepted (--force)"), 1)

    def test_generated_host_symlink_is_rejected(self):
        generated = self.repo / "hosts/generated"
        generated.mkdir()
        (generated / "vps-test").symlink_to(Path(self.tmp.name) / "outside")
        with self.assertRaisesRegex(V.Error, "must not be a symlink"):
            V.host_paths(self.args("check", "vps-test"))

    def test_installation_private_key_stays_outside_generated_host(self):
        repo, state, host = V.host_paths(self.args("install", "vps-test"))
        private_root = Path(self.tmp.name) / "private-state"
        def keygen(argv):
            key = Path(argv[-1])
            key.write_text("synthetic private key")
            key.chmod(0o600)
            key.with_suffix(".pub").write_text("synthetic public key")
            return b""
        with patch.dict(os.environ, {"XDG_STATE_HOME": str(private_root)}), patch.object(V, "run", side_effect=keygen):
            key = V.installation_key(state, repo, create=True)
            self.assertEqual(V.installation_key(state, repo), key)
        self.assertEqual(key, private_root / "vps/vps-test/install-ssh/vps-test-install")
        self.assertFalse((host / "install-ssh").exists())
        self.assertEqual(key.stat().st_mode & 0o777, 0o600)


class OverwriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "hosts/generated/fixture/probe.json"
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"old": true}\n')

    def test_new_files_and_external_state_do_not_prompt(self):
        with patch("builtins.input") as prompt:
            V.write_json(self.path.with_name("new.json"), {"new": True})
            outside = Path(self.tmp.name) / "state/probe.json"
            V.write_json(outside, {"old": True})
            V.write_json(outside, {"new": True})
        prompt.assert_not_called()
        self.assertEqual(V.read_json(outside), {"new": True})

    def test_each_overwrite_requires_explicit_acceptance_even_if_unchanged(self):
        for answer in ("y", "yes", " YES "):
            with self.subTest(answer=answer), patch.object(V.sys.stdin, "isatty", return_value=True), \
                 patch("builtins.input", return_value=answer) as prompt:
                V.write_json(self.path, {"old": True})
            prompt.assert_called_once()
            self.assertIn(str(self.path), prompt.call_args.args[0])
            self.assertIn("Overwrite? [y/N]", prompt.call_args.args[0])
            self.assertEqual(V.read_json(self.path), {"old": True})
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_decline_and_eof_preserve_file_without_temp_files(self):
        original = self.path.read_bytes()
        for answer in ("", "n", "no", "anything", EOFError()):
            with self.subTest(answer=answer), patch.object(V.sys.stdin, "isatty", return_value=True), \
                 patch("builtins.input", **({"side_effect": answer} if isinstance(answer, Exception)
                                           else {"return_value": answer})), self.assertRaises(V.Error):
                V.write_json(self.path, {"new": True})
            self.assertEqual(self.path.read_bytes(), original)
            self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_noninteractive_overwrite_is_refused(self):
        with patch.object(V.sys.stdin, "isatty", return_value=False), patch("builtins.input") as prompt, \
             self.assertRaisesRegex(V.Error, "interactive terminal"):
            V.write_json(self.path, {"new": True})
        prompt.assert_not_called()
        self.assertEqual(V.read_json(self.path), {"old": True})

    def test_force_accepts_overwrite_without_terminal_and_reports_it(self):
        with patch.object(V.sys.stdin, "isatty", return_value=False), patch("builtins.input") as prompt, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            V.write_json(self.path, {"new": True}, force=True)
        prompt.assert_not_called()
        self.assertEqual(V.read_json(self.path), {"new": True})
        self.assertIn(str(self.path), output.getvalue())
        self.assertIn("Overwrite? y", output.getvalue())
        self.assertIn("automatically accepted (--force)", output.getvalue())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_force_accepts_known_hosts_text_overwrite_and_reports_it(self):
        key = Path(self.tmp.name) / "admin-key"
        key.write_text("synthetic private key")
        pin = self.path.parent / "installed-known-hosts"
        pin.write_text("old pin")
        with patch.object(V.sys.stdin, "isatty", return_value=False), patch("builtins.input") as prompt, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            V.production_connection({"connection": {"address": "203.0.113.5"}}, {"sshPort": 3333},
                                    {"host_public_key": "ssh-ed25519 AAAATEST"}, self.path.parent,
                                    key=key, force=True)
        prompt.assert_not_called()
        self.assertEqual(pin.read_text(), "[203.0.113.5]:3333 ssh-ed25519 AAAATEST\n")
        self.assertIn(str(pin), output.getvalue())
        self.assertIn("automatically accepted (--force)", output.getvalue())

    def test_force_does_not_report_nonexistent_files_as_overwritten(self):
        with patch("builtins.input") as prompt, contextlib.redirect_stdout(io.StringIO()) as output:
            V.write_json(self.path.with_name("new.json"), {"new": True}, force=True)
        prompt.assert_not_called()
        self.assertEqual(output.getvalue(), "")

    def test_probe_force_is_forwarded_to_overwrite_and_reported(self):
        args = V.parser().parse_args(["probe", "fixture", "203.0.113.5", "--key", "/source-key", "--force"])
        with patch.object(V, "confirm_probe_host_key"), patch.object(V, "inventory", return_value=facts()), \
             patch.object(V.sys.stdin, "isatty", return_value=False), patch("builtins.input") as prompt, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            V.probe(args, Path(self.tmp.name), self.path.parent, self.path.parent)
        prompt.assert_not_called()
        self.assertEqual(V.read_json(self.path)["facts"], facts())
        self.assertIn("Overwrite? y", output.getvalue())
        self.assertIn("automatically accepted (--force)", output.getvalue())

    def test_generated_symlinks_are_never_followed(self):
        for target in (self.path, self.path.with_name("missing.json")):
            link = self.path.with_name("link.json")
            link.symlink_to(target)
            for force in (False, True):
                with patch("builtins.input") as prompt, self.assertRaisesRegex(V.Error, "non-regular"):
                    V.write_json(link, {"new": True}, force=force)
            prompt.assert_not_called()
            self.assertTrue(link.is_symlink())
            link.unlink()
        self.assertEqual(V.read_json(self.path), {"old": True})

    def test_probe_does_not_silently_overwrite_saved_inventory(self):
        args = V.parser().parse_args(["probe", "fixture", "203.0.113.5", "--key", "/source-key"])
        with patch.object(V, "confirm_probe_host_key"), patch.object(V, "inventory", return_value=facts()), \
             patch.object(V.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="n"), \
             self.assertRaisesRegex(V.Error, "Overwrite declined"):
            V.probe(args, Path(self.tmp.name), self.path.parent, self.path.parent)
        self.assertEqual(V.read_json(self.path), {"old": True})


class DiagnosticsTests(unittest.TestCase):
    def test_reports_preserve_context_and_captured_stderr_but_not_exception_messages(self):
        failures = [ValueError("SECRET-INPUT"), KeyError("SECRET-FIELD"),
                    FileNotFoundError(2, "SECRET-REASON", "SECRET-FILENAME"),
                    subprocess.TimeoutExpired(["tool", "SECRET-ARG"], 90,
                                              output=b"SECRET-OUT", stderr=b"SECRET-ERR")]
        for failure in failures:
            with self.subTest(kind=type(failure).__name__):
                try:
                    with V.secrets_step("WARP registration/profile"):
                        raise failure
                except V.Error as error:
                    report = V.error_report(error, "secrets", "vps-test")
            text = json.dumps(report)
            if isinstance(failure, subprocess.TimeoutExpired):
                self.assertEqual(report["exceptions"][-1]["stderr"], "SECRET-ERR")
                self.assertFalse(report["redacted"])
                for secret in ("SECRET-ARG", "SECRET-OUT"):
                    self.assertNotIn(secret, text)
            else:
                self.assertNotIn("SECRET", text)
                self.assertTrue(report["redacted"])
            self.assertEqual(report["exceptions"][0]["stage"], "WARP registration/profile")
            self.assertEqual(report["exceptions"][-1]["type"], type(failure).__name__)
            self.assertTrue(report["exceptions"][-1]["frames"])
            self.assertFalse(report["implicit_retry"])
            if isinstance(failure, OSError):
                self.assertEqual(report["exceptions"][-1]["errno"], 2)
            if isinstance(failure, subprocess.TimeoutExpired):
                self.assertEqual(report["exceptions"][-1]["timeout_seconds"], 90)

    def test_reports_include_safe_file_names_and_json_position_not_input(self):
        report = V.error_report(FileNotFoundError(2, "SECRET-REASON", "/SECRET-DIR/credentials.json"),
                                "check", "SECRET-HOST")
        self.assertEqual(report["exceptions"][0]["input_file"], "credentials.json")
        self.assertEqual(report["host"], "<invalid>")
        self.assertNotIn("SECRET", json.dumps(report))
        try:
            json.loads('{"SECRET-TOKEN": invalid}')
        except json.JSONDecodeError as error:
            report = V.error_report(error, "check", "vps-test")
        self.assertEqual(report["exceptions"][0]["input_line"], 1)
        self.assertGreater(report["exceptions"][0]["input_column"], 1)
        self.assertNotIn("SECRET", json.dumps(report))

    def test_tool_report_contains_status_timing_and_no_command_arguments(self):
        with patch.object(V.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 42, stdout=b"SECRET-OUT", stderr=b"SECRET-ERR")):
            try:
                with V.secrets_step("SOPS encryption"):
                    V.run(["sops", "--password", "SECRET-ARG"], data=b"SECRET-INPUT")
            except V.Error as error:
                error.details.update({"unrecognized": "SECRET-ATTRIBUTE", "stdout": "SECRET-OUT",
                                      "reason_code": "http_error", "http_status": 403, "account_file_created": True})
                report = V.error_report(error, "secrets", "vps-test")
        for secret in ("SECRET-OUT", "SECRET-ARG", "SECRET-INPUT", "SECRET-ATTRIBUTE"):
            self.assertNotIn(secret, json.dumps(report))
        self.assertFalse(report["redacted"])
        tool = report["exceptions"][0]
        self.assertEqual(tool["stderr"], "SECRET-ERR")
        self.assertEqual(tool["tool"], "sops")
        self.assertEqual(tool["exit_code"], 42)
        self.assertEqual(tool["timeout_seconds"], 60)
        self.assertGreaterEqual(tool["elapsed_seconds"], 0)
        self.assertEqual(tool["reason_code"], "http_error")
        self.assertEqual(tool["http_status"], 403)
        self.assertTrue(tool["account_file_created"])

    def test_main_saves_private_reports_for_later_and_debug_is_redacted(self):
        for debug in (False, True):
            with self.subTest(debug=debug), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                state = root / "state"
                state.mkdir()
                stderr = io.StringIO()
                argv = ["vps", *(["--debug"] if debug else []), "check", "vps-test"]
                failure = RuntimeError("SECRET-INPUT") if debug else ValueError("SECRET-INPUT")
                with patch("sys.argv", argv), patch.object(V, "host_paths", return_value=(root, state, root / "host")), \
                     patch.object(V, "check", side_effect=failure) as action, \
                     patch.object(V.signal, "signal"), patch.object(V.os, "umask"), contextlib.redirect_stderr(stderr):
                    self.assertEqual(V.main(), 1)
                action.assert_called_once()
                files = list((state / "errors").glob("*.json"))
                self.assertEqual(len(files), 1)
                self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
                report = V.read_json(files[0])
                self.assertEqual(report["command"], "check")
                self.assertEqual(report["host"], "vps-test")
                self.assertNotIn("SECRET", files[0].read_text() + stderr.getvalue())
                self.assertIn("failure category: " + type(failure).__name__, stderr.getvalue())
                self.assertIn(str(files[0]), stderr.getvalue())
                self.assertEqual("redacted debug report" in stderr.getvalue(), debug)

    def test_captured_stderr_is_saved_privately_but_never_printed_even_in_debug(self):
        raw_stderr = "Ошибка nix\nRAW-SECRET-STDERR\n".encode() + b"\xff\n"
        for debug in (False, True):
            for timed_out in (False, True):
                with self.subTest(debug=debug, timed_out=timed_out), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    state = root / "state"
                    state.mkdir()
                    terminal = io.StringIO()
                    argv = ["vps", *(["--debug"] if debug else []), "check", "vps-test"]
                    options = {"side_effect": subprocess.TimeoutExpired(
                        ["nix", "SECRET-ARG"], 60, output=b"SECRET-OUT", stderr=raw_stderr)} if timed_out else {
                        "return_value": subprocess.CompletedProcess([], 42, stdout=b"SECRET-OUT", stderr=raw_stderr)}

                    def fail(*args):
                        with V.secrets_step("test tool failure"):
                            V.run(["nix", "SECRET-ARG"], data=b"SECRET-INPUT")

                    with patch("sys.argv", argv), patch.object(V, "host_paths", return_value=(root, state, root / "host")), \
                         patch.object(V, "check", side_effect=fail) as action, \
                         patch.object(V.subprocess, "run", **options) as tool, \
                         patch.object(V.signal, "signal"), patch.object(V.os, "umask"), contextlib.redirect_stderr(terminal):
                        self.assertEqual(V.main(), 1)
                    action.assert_called_once()
                    tool.assert_called_once()
                    files = list((state / "errors").glob("*.json"))
                    self.assertEqual(len(files), 1)
                    self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
                    report = V.read_json(files[0])
                    self.assertFalse(report["redacted"])
                    self.assertFalse(report["implicit_retry"])
                    self.assertEqual(report["exceptions"][0]["stderr"], raw_stderr.decode("utf-8", errors="replace"))
                    for secret in ("SECRET-ARG", "SECRET-OUT", "SECRET-INPUT"):
                        self.assertNotIn(secret, files[0].read_text())
                    for secret in ("RAW-SECRET-STDERR", "SECRET-ARG", "SECRET-OUT", "SECRET-INPUT"):
                        self.assertNotIn(secret, terminal.getvalue())
                    self.assertIn("raw stderr may contain secrets", terminal.getvalue())
                    if debug:
                        debug_text = terminal.getvalue().split("redacted debug report", 1)[1]
                        self.assertNotIn('"stderr":', debug_text)
                        self.assertIn('"redacted": true', debug_text)

    def test_captured_stderr_is_not_truncated_and_accepts_text_or_bytes(self):
        text = "Ошибка nix\n" + "x" * (64 * 1024) + "\n"
        for stderr in (text, text.encode(), "", b""):
            with self.subTest(kind=type(stderr).__name__, length=len(stderr)):
                report = V.error_report(V.Error("Tool failed", details={"stderr": stderr}), "check", "vps-test")
                self.assertEqual(report["exceptions"][0]["stderr"], text if stderr else "")
                self.assertFalse(report["redacted"])
        report = V.error_report(V.Error("Tool failed", details={"stderr": None}), "check", "vps-test")
        self.assertNotIn("stderr", report["exceptions"][0])
        self.assertTrue(report["redacted"])

    def test_report_write_failure_does_not_mask_original_problem(self):
        stderr = io.StringIO()
        args = argparse.Namespace(command="check", name="vps-test", debug=False)
        with patch.object(V, "write_json", side_effect=PermissionError("SECRET-PATH")), \
             contextlib.redirect_stderr(stderr):
            V.report_failure(V.Error("Compiled SSH policy differs"), args, Path("/state"))
        self.assertIn("Compiled SSH policy differs", stderr.getvalue())
        self.assertIn("could not save diagnostic report", stderr.getvalue())
        self.assertNotIn("SECRET", stderr.getvalue())


class ProfileTests(unittest.TestCase):
    def test_supported_profile_and_known_disk(self):
        self.assertEqual(V.platform_check(facts())["name"], "/dev/vda")
        for change in ({"architecture": "aarch64"}, {"virtualization": "lxc"}, {"secure_boot": True},
                       {"memory_bytes": 512 * 1024**2}, {"machine_id": ""}):
            with self.subTest(change=change), self.assertRaises(V.Error):
                V.platform_check(facts() | change)

    def test_multi_disk_lvm_readonly_small_or_unknown_device_refused(self):
        original = facts()
        for kind in ("multi", "lvm", "readonly", "small", "unknown"):
            f = copy.deepcopy(original)
            disk = f["disks"][0]
            if kind == "multi":
                f["disks"].append(disk.copy() | {"name": "/dev/vdb"})
            elif kind == "lvm":
                disk["children"][0]["type"] = "lvm"
            elif kind == "readonly":
                disk["ro"] = True
            elif kind == "small":
                disk["size"] = 1024**3
            else:
                disk["name"] = "/dev/mmcblk0"
            with self.subTest(kind=kind), self.assertRaises(V.Error):
                V.platform_check(f)

    def test_stacked_links_and_old_incomplete_inventory_are_rejected(self):
        for details in ({"linkinfo": {"info_kind": "vlan", "info_data": {"id": 100}}},
                        {"linkinfo": {"info_kind": "bridge"}}, {"master": "br0"}, {"link_index": 2}):
            f = facts(); f["links"][0].update(details)
            with self.subTest(details=details), self.assertRaises(V.Error):
                V.network_plan(f, "static", [])
        f = facts() | {"schema": 1, "link_details": False}
        with self.assertRaises(V.Error):
            V.platform_check(f)

    def test_missing_optional_executable_allows_dns_fallback(self):
        with patch.object(P.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(P.command("resolvectl", "dns", "ens3", required=False), "")
            with self.assertRaises(ValueError):
                P.command("ip", "-j", "link")

    def test_direct_probe_respects_warp_selection(self):
        self.assertEqual(V.direct_destination({"warpDomains": ["ipinfo.io"]})[1], "trace")
        url, mode = V.direct_destination({"warpDomains": ["ipinfo.io", "cloudflare.com"]})
        self.assertEqual((url, mode), ("https://api.ipify.org", "ip"))
        with self.assertRaises(V.Error):
            V.direct_destination({"warpDomains": ["cloudflare.com", "ipify.org"]})
        self.assertEqual(V.direct_destination({"warpDomains": ["cloudflare.com", "ipify.org"]}, "https://direct.test/ip"),
                         ("https://direct.test/ip", "ip"))
        with self.assertRaises(V.Error):
            V.direct_destination({"warpDomains": ["cloudflare.com"]}, "https://www.cloudflare.com/ip")
        for url in ("http://direct.test/ip", "https://token@direct.test/ip", "https://direct.test/ip?token=secret"):
            with self.subTest(url=url), self.assertRaises(V.Error):
                V.direct_destination({"warpDomains": []}, url)

    def test_auto_network_policy_matches_observed_addresses(self):
        for dynamic, expected in ((False, "static"), (True, "dhcp")):
            with self.subTest(dynamic=dynamic):
                self.assertEqual(V.network_plan(facts(dynamic), "auto", []),
                                 V.network_plan(facts(dynamic), expected, []))
        f = facts(True)
        f["addresses"][0]["addr_info"][0].pop("dynamic")
        self.assertEqual(V.network_plan(f, "auto", [])["mode"], "dhcp")

    def test_auto_static_preserves_multiple_permanent_addresses(self):
        f = facts()
        f["addresses"][0]["addr_info"].append({"family": "inet", "local": "10.24.104.198",
            "prefixlen": 8, "scope": "global", "valid_life_time": 4294967295})
        f["routes"].append({"dst": "10.0.0.0/8", "dev": "ens3", "protocol": "kernel", "scope": "link"})
        n = V.network_plan(f, "auto", [])
        self.assertEqual(n["mode"], "static")
        self.assertEqual(n["config"]["address"], ["203.0.113.5/32", "10.24.104.198/8"])

    def test_auto_rejects_mixed_or_multiple_dynamic_addresses(self):
        for dynamic in (False, True):
            f = facts(True)
            f["addresses"][0]["addr_info"].append({"family": "inet", "local": "203.0.113.6",
                "prefixlen": 32, "scope": "global", "dynamic": dynamic})
            with self.subTest(dynamic=dynamic), self.assertRaisesRegex(V.Error, "custom network profile"):
                V.network_plan(f, "auto", [])

    def test_static_preserves_offlink_gateway_without_ubuntu_uuid(self):
        n = V.network_plan(facts(), "static", [])
        self.assertEqual(n["config"]["matchConfig"], {"MACAddress": "52:54:00:12:34:56"})
        self.assertEqual(n["config"]["address"], ["203.0.113.5/32"])
        self.assertTrue(n["config"]["routes"][1]["GatewayOnLink"])
        self.assertNotIn("fileSystems", json.dumps(n))

    def test_gateway_host_route_accepts_bare_address_and_explicit_prefix(self):
        for mode in ("static", "dhcp"):
            expected = V.network_plan(facts(mode == "dhcp"), mode, [])
            for destination in ("169.254.0.1", "169.254.0.1/32"):
                f = facts(mode == "dhcp")
                f["routes"][1].update(dst=destination, protocol="static")
                f["routes"].append({"dev": "ens3", "dst": "10.0.0.0/8",
                                    "prefsrc": "10.24.104.198", "protocol": "kernel", "scope": "link"})
                with self.subTest(mode=mode, destination=destination):
                    self.assertEqual(V.network_plan(f, mode, []), expected)

    def test_non_gateway_or_non_link_host_routes_are_rejected(self):
        for destination, scope in (("169.254.0.2", "link"), ("169.254.0.2/32", "link"),
                                   ("169.254.0.0/16", "link"), ("169.254.0.1", "global"),
                                   ("169.254.0.1/32", "global"), ("invalid", "link"),
                                   (None, "link")):
            f = facts()
            f["routes"][1].update(dst=destination, scope=scope, protocol="static")
            with self.subTest(destination=destination, scope=scope), \
                 self.assertRaisesRegex(V.Error, "Non-connected routes require a custom network profile"):
                V.network_plan(f, "static", [])

    def test_dhcp_is_not_frozen_and_static_lease_rejected(self):
        n = V.network_plan(facts(True), "dhcp", [])
        self.assertEqual(n["config"]["networkConfig"]["DHCP"], "ipv4")
        self.assertNotIn("address", n["config"])
        with self.assertRaises(V.Error):
            V.network_plan(facts(True), "static", [])
        with self.assertRaises(V.Error):
            V.network_plan(facts(), "dhcp", [])

    def test_ipv6_is_ignored_without_a_flag_in_every_network_mode(self):
        for mode, dynamic in (("auto", False), ("auto", True), ("static", False), ("dhcp", True)):
            f = facts(dynamic)
            f["addresses"][0]["addr_info"].extend([
                {"family": "inet6", "scope": "global", "local": "2001:db8::1"},
                {"family": "inet6", "scope": "link", "local": "fe80::1"}])
            f["addresses"].append({"ifname": "ens4", "addr_info": [
                {"family": "inet6", "scope": "global", "local": "2001:db8:1::1"}]})
            with self.subTest(mode=mode, dynamic=dynamic):
                network = V.network_plan(f, mode, [])
                self.assertEqual(network, V.network_plan(facts(dynamic), mode, []))
                self.assertFalse(network["config"]["networkConfig"]["IPv6AcceptRA"])
                self.assertEqual(network["config"]["networkConfig"]["LinkLocalAddressing"], "no")

    def test_ipv6_only_source_still_requires_ipv4(self):
        f = facts()
        f["addresses"][0]["addr_info"] = [
            {"family": "inet6", "scope": "global", "local": "2001:db8::1"}]
        with self.assertRaisesRegex(V.Error, "No usable IPv4"):
            V.network_plan(f, "auto", [])

    def test_unsupported_networks_fail_closed(self):
        original = facts()
        for kind in ("multi-default", "policy", "custom-table", "custom-route", "extra-ipv4-uplink", "loopback-dns"):
            f = copy.deepcopy(original)
            if kind == "multi-default":
                f["routes"].append(f["routes"][0].copy())
            elif kind == "policy":
                f["rules"].append({"priority": 100, "src": "all"})
            elif kind == "custom-table":
                f["extra_routes"] = [{"table": 100}]
            elif kind == "custom-route":
                f["routes"].append({"dst": "10.0.0.0/8", "dev": "ens3"})
            elif kind == "extra-ipv4-uplink":
                f["addresses"].append({"ifname": "ens4", "addr_info": [
                    {"family": "inet", "scope": "global", "local": "203.0.113.6", "prefixlen": 32}]})
            else:
                f["dns"] = ["127.0.0.53"]
            with self.subTest(kind=kind), self.assertRaises(V.Error):
                V.network_plan(f, "static", [])

    def test_shell_and_path_injection_refused(self):
        for name in ("../root", "a/b", "a;touch", "a\nb", "A", "-a"):
            with self.assertRaises(V.Error):
                V.validate_name(name)
        for name in ("bad;host", "https://host.test", "a..b", "-host.test", "${bad}"):
            with self.assertRaises(V.Error):
                V.domain(name)
        with self.assertRaises(ValueError):
            V.connection("203.0.113.5;touch", "root", 22, "/key", "/known")
        with self.assertRaises(V.Error):
            V.connection("203.0.113.5", "root;touch", 22, "/key", "/known")

    def test_inventory_uses_only_readonly_python_and_strict_ssh(self):
        c = V.connection("203.0.113.5", "operator", 22, "/key with spaces", "/known")
        with patch.object(V, "run", return_value=json.dumps(facts()).encode()) as run, \
             patch.object(V.getpass, "getpass") as prompt:
            self.assertEqual(V.inventory(c)["os"], "ubuntu")
        prompt.assert_not_called()
        argv = run.call_args.args[0]
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertIn("BatchMode=yes", argv)
        self.assertEqual(argv[-1], "sudo -n python3 -")
        self.assertIn("/key with spaces", argv)
        self.assertNotIn("nixos-anywhere", " ".join(argv))

    def test_listener_scope_not_just_firewall(self):
        good = "\n".join(f"LISTEN 0 128 127.0.0.1:{p} 0.0.0.0:*" for p in V.PRIVATE_PORTS)
        V.listener_check(good, 3333)
        with self.assertRaises(V.Error):
            V.listener_check(good.replace("127.0.0.1:3000", "0.0.0.0:3000"), 3333)
        with self.assertRaises(V.Error):
            V.listener_check(good + "\nLISTEN 0 128 0.0.0.0:22 0.0.0.0:*", 3333)
        with self.assertRaises(V.Error):
            V.listener_check("", 3333)

    def test_dns_stubs_are_allowed_but_llmnr_is_not_whitelisted(self):
        good = "\n".join(f"LISTEN 0 128 127.0.0.1:{p} 0.0.0.0:*" for p in V.PRIVATE_PORTS)
        for address in ("127.0.0.53", "127.0.0.54", "127.0.0.53%lo", "127.0.0.54%lo",
                        "::1", "[::1%lo]", "[::1]%lo"):
            with self.subTest(address=address):
                V.listener_check(good + f"\nLISTEN 0 128 {address}:53 *:*", 3333)
        for address in ("127.0.0.1%lo", "[::1%lo]", "[::1]%lo"):
            with self.subTest(private_address=address):
                V.listener_check(good.replace("127.0.0.1:", address + ":"), 3333)
        for address in ("0.0.0.0", "0.0.0.0%eth0", "[::]", "[::%eth0]", "[::]%eth0",
                        "[fe80::1]%eth0", "*", "*%eth0"):
            with self.subTest(address=address), self.assertRaisesRegex(V.Error, "non-loopback TCP listener: 5355"):
                V.listener_check(good + f"\nLISTEN 0 128 {address}:5355 *:*", 3333)
            with self.subTest(private_address=address), self.assertRaisesRegex(V.Error, "outside loopback"):
                V.listener_check(good.replace("127.0.0.1:3000", address + ":3000"), 3333)
            for port in (3333, 443):
                with self.subTest(public_address=address, port=port):
                    V.listener_check(good + f"\nLISTEN 0 128 {address}:{port} *:*", 3333)

    def test_malformed_listener_endpoints_fail_without_disclosing_input(self):
        good = "\n".join(f"LISTEN 0 128 127.0.0.1:{p} 0.0.0.0:*" for p in V.PRIVATE_PORTS)
        for endpoint in ("untrusted-endpoint", "untrusted-endpoint:443", "untrusted-endpoint:3000",
                         "[untrusted-endpoint]%lo:443", "127.0.0.1:service", "127.0.0.1:*",
                         "127.0.0.1:0", "127.0.0.1:-1", "127.0.0.1:65536"):
            with self.subTest(endpoint=endpoint), self.assertRaisesRegex(V.Error, "Unexpected ss") as error:
                V.listener_check(good + f"\nLISTEN 0 128 {endpoint} *:*", 3333)
            self.assertNotIn(endpoint, str(error.exception))


class IPv6AcceptanceTests(unittest.TestCase):
    def test_disabled_check_requires_kernel_or_all_interface_flags(self):
        cases = (
            ("boot-disabled", "1", {}, "", "", 0, True),
            ("live-switch", "0", {"all": 1, "default": 1, "lo": 1, "ens3": 1}, "", "", 0, True),
            ("enabled-interface", "0", {"all": 1, "default": 1, "ens3": 0}, "", "", 0, False),
            ("future-interface-enabled", "0", {"all": 1, "default": 0}, "", "", 0, False),
            ("missing-flags", "0", {}, "", "", 0, False),
            ("link-local-address", "0", {"all": 1, "default": 1}, "fe80::1", "", 0, False),
            ("ipv6-route", "0", {"all": 1, "default": 1}, "", "default via fe80::1", 0, False),
            ("ip-failure", "0", {"all": 1, "default": 1}, "", "", 1, False),
        )
        for name, boot, flags, addresses, routes, ip_exit, success in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "boot").write_text(boot)
                for interface, value in flags.items():
                    directory = root / "conf" / interface
                    directory.mkdir(parents=True)
                    (directory / "disable_ipv6").write_text(str(value))
                command = V.IPV6_DISABLED_CHECK.replace("/sys/module/ipv6/parameters/disable", str(root / "boot"))
                command = command.replace("/proc/sys/net/ipv6/conf/", str(root / "conf") + "/")
                ip = ('ip() { test "$IP_EXIT" = 0 || return "$IP_EXIT"; '
                      'case "$*" in "-6 -o address show") printf %s "$ADDRESSES";; '
                      '"-6 route show table all") printf %s "$ROUTES";; *) return 1;; esac; }; ')
                result = subprocess.run(["sh", "-c", ip + command], capture_output=True,
                    env=os.environ | {"IP_EXIT": str(ip_exit), "ADDRESSES": addresses, "ROUTES": routes})
                self.assertEqual(result.returncode == 0, success, result.stderr)


class PasswordProbeTests(unittest.TestCase):
    def setUp(self):
        self.c = V.connection("203.0.113.5", "root", 22, None, "/known")

    def test_probe_parser_positional_address_and_optional_key(self):
        args = V.parser().parse_args(["probe", "vps-test", "203.0.113.5"])
        self.assertEqual((args.address, args.user, args.port, args.key), ("203.0.113.5", "root", 22, None))
        args = V.parser().parse_args(["probe", "vps-test", "203.0.113.5", "--key", "/key"])
        self.assertEqual(args.key, Path("/key"))
        for argv in (["probe", "vps-test"], ["probe", "vps-test", "--address", "203.0.113.5"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                V.parser().parse_args(argv)

    def password_mocks(self):
        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(V.shutil, "which", return_value="/bin/sshpass"))
        stack.enter_context(patch.object(V.sys.stdin, "isatty", return_value=True))
        return stack

    def test_password_uses_separate_pipe_and_never_enters_report(self):
        fds = []
        def ssh(argv, data=None, timeout=60, pass_fds=()):
            self.assertEqual(argv[:2], ["sshpass", "-d"])
            self.assertEqual(pass_fds, (int(argv[2]),))
            fds.extend(pass_fds)
            self.assertEqual(os.read(pass_fds[0], 2048), b"private-test-password\n")
            self.assertNotIn("private-test-password", " ".join(argv))
            self.assertIn("StrictHostKeyChecking=yes", argv)
            self.assertIn("UserKnownHostsFile=/known", argv)
            self.assertIn("PubkeyAuthentication=no", argv)
            self.assertIn("PreferredAuthentications=password", argv)
            self.assertIn("NumberOfPasswordPrompts=1", argv)
            self.assertNotIn("-i", argv)
            self.assertEqual(argv[-1], "python3 -")
            self.assertEqual(data, (SCRIPTS / "vps-probe.py").read_bytes())
            self.assertEqual(timeout, 90)
            return json.dumps(facts()).encode()
        with tempfile.TemporaryDirectory() as tmp, self.password_mocks(), \
             patch("builtins.input", return_value="n") as temporary_key, \
             patch.object(V, "confirm_probe_host_key") as confirm_key, \
             patch.object(V.getpass, "getpass", return_value="private-test-password") as prompt, \
             patch.object(V, "run", side_effect=ssh), contextlib.redirect_stdout(io.StringIO()):
            state = Path(tmp)
            args = argparse.Namespace(name="vps-test", address="203.0.113.5", user="root", port=22,
                                      key=None, known_hosts=Path("/known"))
            V.probe(args, state, state, state / "host")
            report = V.read_json(state / "probe.json")
            self.assertIsNone(report["connection"]["key"])
            self.assertNotIn("private-test-password", json.dumps(report))
            self.assertEqual((state / "probe.json").stat().st_mode & 0o777, 0o600)
        confirm_key.assert_called_once_with(self.c)
        temporary_key.assert_called_once()
        prompt.assert_called_once()
        with self.assertRaises(OSError):
            os.fstat(fds[0])

    def test_missing_tool_or_terminal_refused_before_password_prompt(self):
        for tool, tty in ((None, True), ("/bin/sshpass", False)):
            with self.subTest(tool=tool, tty=tty), \
                 patch.object(V.shutil, "which", return_value=tool), \
                 patch.object(V.sys.stdin, "isatty", return_value=tty), \
                 patch.object(V.getpass, "getpass") as prompt, patch.object(V, "run") as run, \
                 self.assertRaises(V.Error):
                V.inventory(self.c)
            prompt.assert_not_called()
            run.assert_not_called()

    def test_cannot_hide_input_refused(self):
        def prompt(*args):
            warnings.warn("Cannot hide input", V.getpass.GetPassWarning)
        with self.password_mocks(), patch.object(V.getpass, "getpass", side_effect=prompt), \
             patch.object(V, "run") as run, self.assertRaisesRegex(V.Error, "Cannot hide"):
            V.inventory(self.c)
        run.assert_not_called()

    def test_invalid_password_refused_before_execution(self):
        for password in ("", "a\nb", "a\rb", "a" * 1025):
            with self.subTest(length=len(password)), self.password_mocks(), \
                 patch.object(V.getpass, "getpass", return_value=password), patch.object(V, "run") as run, \
                 self.assertRaises(V.Error):
                V.inventory(self.c)
            run.assert_not_called()

    def test_failed_auth_does_not_retry_or_publish_output_and_closes_pipe(self):
        fds = []
        def ssh(argv, **kwargs):
            fds.extend(kwargs["pass_fds"])
            self.assertEqual(os.read(fds[0], 2048), b"private-test-password\n")
            self.assertNotIn("private-test-password", " ".join(argv))
            self.assertNotIn("env", kwargs)
            return subprocess.CompletedProcess(argv, 5, stdout=b"private-test-password", stderr=b"private-test-password")
        with self.password_mocks(), patch.object(V.getpass, "getpass", return_value="private-test-password") as prompt, \
             patch.object(V.subprocess, "run", side_effect=ssh) as run, self.assertRaises(V.Error) as error:
            V.inventory(self.c)
        self.assertNotIn("private-test-password", str(error.exception))
        prompt.assert_called_once()
        run.assert_called_once()
        with self.assertRaises(OSError):
            os.fstat(fds[0])

    def test_failure_diagnostics_are_static_and_do_not_publish_tool_output(self):
        cases = [
            (1, b"invalid argument", "remote inventory prerequisites"),
            (2, b"conflicting arguments", "remote inventory prerequisites"),
            (3, b"runtime error", "remote inventory prerequisites"),
            (4, b"parse error", "remote inventory prerequisites"),
            (5, b"", "authentication rejected"),
            (6, b"", "unknown SSH host key"),
            (7, b"", "SSH host key changed"),
            (255, b"REMOTE HOST IDENTIFICATION HAS CHANGED!", "SSH host key changed"),
            (255, b"Host key verification failed.", "SSH host key verification failed"),
            (255, b"No ED25519 host key is known and you have requested strict checking.",
             "SSH host key verification failed"),
            (255, b"Permission denied (password).", "authentication rejected"),
            (255, b"Connection refused", "connection refused"),
            (255, b"Connection timed out", "server unreachable"),
            (255, b"No route to host", "server unreachable"),
            (255, b"Network is unreachable", "server unreachable"),
            (255, b"unclassified failure", "connection/authentication failed"),
            (42, b"remote failure", "remote inventory prerequisites"),
        ]
        for code, diagnostic, expected in cases:
            with self.subTest(code=code, diagnostic=diagnostic), \
                 patch.object(V.subprocess, "run", return_value=subprocess.CompletedProcess(
                     [], code, stdout=b"secret-stdout", stderr=diagnostic + b"\nsecret-stderr")) as run, \
                 self.assertRaises(V.Error) as error:
                V.run(["/bin/sshpass", "-d", "3", "ssh"])
            message = str(error.exception)
            self.assertIn(f"exit {code}", message)
            self.assertIn(expected, message)
            self.assertIn("no retry", message)
            self.assertNotIn("secret-stdout", message)
            self.assertNotIn("secret-stderr", message)
            run.assert_called_once()

    def test_other_tool_errors_still_withhold_output(self):
        with patch.object(V.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 5, stdout=b"secret-stdout", stderr=b"secret-stderr")), self.assertRaises(V.Error) as error:
            V.run(["sops"])
        self.assertEqual(str(error.exception),
                         "Command failed: sops (exit 5); output withheld to protect secrets; check tools/configuration; no retry")
        self.assertEqual(error.exception.details["tool"], "sops")
        self.assertEqual(error.exception.details["exit_code"], 5)

    def test_missing_tool_and_timeout_diagnostics_withhold_exception_details(self):
        cases = [(FileNotFoundError("SECRET-DETAIL"), "Required tool not found: ssh-to-age"),
                 (subprocess.TimeoutExpired(["ssh-to-age", "SECRET-ARG"], 60,
                                          output=b"SECRET-OUTPUT", stderr=b"SECRET-STDERR"),
                  "Command timed out: ssh-to-age (60s)")]
        for failure, expected in cases:
            with self.subTest(expected=expected), patch.object(V.subprocess, "run", side_effect=failure) as run, \
                 self.assertRaises(V.Error) as error:
                V.run(["ssh-to-age"])
            self.assertIn(expected, str(error.exception))
            self.assertNotIn("SECRET", str(error.exception))
            run.assert_called_once()

    def test_secrets_stage_diagnostics_withhold_untrusted_exception_text(self):
        for failure in (ValueError("SECRET-PROFILE"), KeyError("SECRET-TOKEN"),
                        OSError("SECRET-PATH"), FileNotFoundError("SECRET-FILE"),
                        subprocess.TimeoutExpired(["wgcf", "SECRET-ARG"], 90, output=b"SECRET-OUTPUT")):
            with self.subTest(failure=type(failure).__name__), self.assertRaises(V.Error) as error:
                with V.secrets_step("WARP registration/profile"):
                    raise failure
            self.assertIn("secrets / WARP registration/profile:", str(error.exception))
            self.assertIn("no implicit retry", str(error.exception))
            self.assertNotIn("SECRET", str(error.exception))

    def test_key_required_for_non_probe_ssh(self):
        with self.assertRaisesRegex(V.Error, "requires an SSH key"):
            V.ssh_args(self.c)


class ProbeHostKeyTests(unittest.TestCase):
    def setUp(self):
        self.c = V.connection("203.0.113.5", "root", 2222, None, "/custom-known")

    def test_native_confirmation_without_authentication_or_collector(self):
        for code in (0, 255):
            with self.subTest(code=code), patch.object(V.sys.stdin, "isatty", return_value=True), \
                 patch.object(V.subprocess, "run", return_value=subprocess.CompletedProcess(
                     [], code, stdout=b"", stderr=b"root@203.0.113.5: Permission denied (publickey,password).\n")) as run:
                V.confirm_probe_host_key(self.c)
            argv = run.call_args.args[0]
            self.assertEqual(argv[:3], ["ssh", "-p", "2222"])
            for option in ("StrictHostKeyChecking=ask", "BatchMode=no", "PreferredAuthentications=none",
                           "PubkeyAuthentication=no", "PasswordAuthentication=no", "KbdInteractiveAuthentication=no",
                           "NumberOfPasswordPrompts=0", "UserKnownHostsFile=/custom-known"):
                self.assertIn(option, argv)
            self.assertEqual(argv[-2:], ["root@203.0.113.5", "true"])
            self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
            self.assertTrue(run.call_args.kwargs["capture_output"])
            self.assertEqual(run.call_args.kwargs["env"]["LC_ALL"], "C")
            self.assertNotIn("input", run.call_args.kwargs)
            self.assertNotIn("-i", argv)
            self.assertNotIn("sshpass", argv)

    def test_unattended_probe_never_prompts_or_enrolls(self):
        with patch.object(V.sys.stdin, "isatty", return_value=False), patch.object(V.subprocess, "run") as run:
            V.confirm_probe_host_key(self.c)
        run.assert_not_called()
        self.assertIn("StrictHostKeyChecking=yes", V.ssh_args(self.c, password=True))

    def test_rejected_changed_or_unreachable_host_stops_before_inventory(self):
        failures = [b"Host key verification failed.", b"REMOTE HOST IDENTIFICATION HAS CHANGED!",
                    b"Connection refused", b"Permission denied banner\nHost key verification failed.",
                    b"Failed to add the host to the list of known hosts\nPermission denied (password)."]
        for key in (None, Path("/admin-key")):
            for stderr in failures:
                with self.subTest(key=key, stderr=stderr), \
                     patch.object(V.sys.stdin, "isatty", return_value=True), \
                     patch.object(V.subprocess, "run", return_value=subprocess.CompletedProcess(
                         [], 255, stdout=b"private-output", stderr=stderr + b"\nprivate-output")), \
                     patch.object(V, "inventory") as inventory, patch.object(V.getpass, "getpass") as password, \
                     patch.object(V, "write_json") as write_json, self.assertRaises(V.Error) as error:
                    args = argparse.Namespace(address="203.0.113.5", user="root", port=2222,
                                              key=key, known_hosts=Path("/custom-known"))
                    V.probe(args, Path("/repo"), Path("/state"), Path("/host"))
                inventory.assert_not_called()
                password.assert_not_called()
                write_json.assert_not_called()
                self.assertNotIn("private-output", str(error.exception))

    def test_probe_confirms_host_before_inventory_for_password_and_key(self):
        for key in (None, Path("/admin-key")):
            events = []
            with self.subTest(key=key), \
                 patch.object(V, "confirm_probe_host_key", side_effect=lambda c: events.append("confirm")), \
                 patch.object(V, "prompt_ssh_password", return_value=b"test-password"), \
                 patch.object(V.sys.stdin, "isatty", return_value=False), \
                 patch.object(V, "inventory", side_effect=lambda c, **kw: events.append("inventory") or facts()), \
                 patch.object(V, "write_json"), contextlib.redirect_stdout(io.StringIO()):
                args = argparse.Namespace(name="vps-test", address="203.0.113.5", user="root", port=2222,
                                          key=key, known_hosts=Path("/custom-known"))
                V.probe(args, Path("/repo"), Path("/state"), Path("/host"))
            self.assertEqual(events, ["confirm", "inventory"])


class ProbeHostKeyReplacementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "known_hosts"
        self.c = V.connection("203.0.113.5", "root", 2222, None, self.path)
        self.new_key = "ssh-ed25519 " + base64.b64encode(
            struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + bytes(range(32))).decode()
        self.unrelated = "# keep this comment\n203.0.113.99 " + TEST_SOURCE_KEY + " other host\n"
        self.real_run = subprocess.run
        self.ssh_calls = []
        self.reset_known_hosts()

    def reset_known_hosts(self, hashed=False):
        self.target = (self.c["address"] if self.c["port"] == 22
                       else f'[{self.c["address"]}]:{self.c["port"]}')
        self.path.write_text(self.unrelated + self.target + " " + TEST_SOURCE_KEY + " old host\n")
        if hashed:
            self.real_run(["ssh-keygen", "-H", "-f", str(self.path)], check=True, capture_output=True)
            # Keep unrelated entries byte-for-byte, whether hashed or not.
            self.unrelated = "\n".join(self.path.read_text().splitlines()[:2]) + "\n"
            self.path.with_name("known_hosts.old").unlink()
        self.path.chmod(0o640)
        self.original = self.path.read_bytes()
        self.ssh_calls = []

    def handshake(self, argv, **kwargs):
        if str(argv[0]) != "ssh":
            return self.real_run(argv, **kwargs)  # Exercise real OpenSSH hashed/port-aware removal.
        self.ssh_calls.append(argv)
        if len(self.ssh_calls) == 1:
            return subprocess.CompletedProcess(argv, 255, stdout=b"private-output",
                stderr=b"REMOTE HOST IDENTIFICATION HAS CHANGED!\nprivate-output")
        candidate = Path(next(arg.split("=", 1)[1] for arg in argv if arg.startswith("UserKnownHostsFile=")))
        self.assertNotEqual(candidate, self.path)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(candidate.read_bytes(), self.unrelated.encode())
        self.assertEqual(candidate.stat().st_mode & 0o777, 0o600)
        self.assertEqual(candidate.parent.stat().st_mode & 0o777, 0o700)
        candidate.write_text(self.unrelated + self.target + " " + self.new_key + "\n")
        return subprocess.CompletedProcess(argv, 255, stdout=b"private-output", stderr=b"Permission denied (password).")

    def assert_no_temporary_files(self):
        self.assertEqual({p.name for p in self.path.parent.iterdir()}, {"known_hosts"})

    def test_native_fingerprint_acceptance_atomically_replaces_plain_or_hashed_key(self):
        for port in (22, 2222):
            for hashed in (False, True):
                with self.subTest(port=port, hashed=hashed):
                    self.c["port"] = port
                    self.reset_known_hosts(hashed)
                    with patch.object(V.sys.stdin, "isatty", return_value=True), \
                         patch.object(V.subprocess, "run", side_effect=self.handshake), \
                         patch("builtins.input") as prompt, patch.object(V.getpass, "getpass") as password, \
                         contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as warning:
                        V.confirm_probe_host_key(self.c)
                    self.assertEqual(self.path.read_text(), self.unrelated + self.target + " " + self.new_key + "\n")
                    self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
                    self.assertIn("provider console", warning.getvalue())
                    self.assertIn("Accepting confirms replacement", warning.getvalue())
                    self.assertIn(str(self.path), warning.getvalue())
                    self.assertNotIn("private-output", warning.getvalue())
                    prompt.assert_not_called()  # Confirmation belongs to OpenSSH, not an automatic Python answer.
                    password.assert_not_called()
                    self.assertEqual(len(self.ssh_calls), 2)
                    for argv in self.ssh_calls:
                        for option in ("StrictHostKeyChecking=ask", "PreferredAuthentications=none",
                                       "PubkeyAuthentication=no", "PasswordAuthentication=no",
                                       "KbdInteractiveAuthentication=no", "NumberOfPasswordPrompts=0"):
                            self.assertIn(option, argv)
                        self.assertNotIn("-i", argv)
                        self.assertNotIn("sshpass", argv)
                    self.assertIn("StrictHostKeyChecking=yes", V.ssh_args(self.c, password=True))
                    self.assert_no_temporary_files()

    def test_declined_failed_or_changed_again_confirmation_keeps_original(self):
        for failure in (b"Host key verification failed.", b"Connection refused",
                        b"REMOTE HOST IDENTIFICATION HAS CHANGED!"):
            with self.subTest(failure=failure):
                self.reset_known_hosts(hashed=True)
                def reject(argv, **kwargs):
                    if str(argv[0]) == "ssh" and self.ssh_calls:
                        self.ssh_calls.append(argv)
                        return subprocess.CompletedProcess(argv, 255, stdout=b"private-output", stderr=failure)
                    return self.handshake(argv, **kwargs)
                with patch.object(V.sys.stdin, "isatty", return_value=True), \
                     patch.object(V.subprocess, "run", side_effect=reject), \
                     patch.object(V, "inventory") as inventory, patch.object(V.getpass, "getpass") as password, \
                     patch.object(V, "write_json") as write_json, contextlib.redirect_stderr(io.StringIO()), \
                     self.assertRaises(V.Error):
                    args = V.parser().parse_args(["probe", "fixture", self.c["address"], "--port", "2222",
                                                  "--known-hosts", str(self.path), "--force"])
                    V.probe(args, self.path.parent, self.path.parent, self.path.parent)
                self.assertEqual(self.path.read_bytes(), self.original)
                self.assertEqual(len(self.ssh_calls), 2)  # Never repeat replacement after a second changed key.
                inventory.assert_not_called()
                password.assert_not_called()
                write_json.assert_not_called()
                self.assert_no_temporary_files()

    def test_malformed_line_29_is_preserved_on_acceptance_and_refusal(self):
        self.unrelated += "# filler\n" * 26 + "malformed\r unrelated record\n"
        for accept in (False, True):
            with self.subTest(accept=accept):
                self.reset_known_hosts()
                old_removal = self.real_run(["ssh-keygen", "-R", self.target, "-f", str(self.path)], capture_output=True)
                self.assertEqual(old_removal.returncode, 1)
                self.assertIn(b":29: invalid line", old_removal.stderr)
                self.assertEqual(self.path.read_bytes(), self.original)
                def confirm(argv, **kwargs):
                    if not accept and str(argv[0]) == "ssh" and self.ssh_calls:
                        return subprocess.CompletedProcess(argv, 255, stdout=b"", stderr=b"Host key verification failed.")
                    return self.handshake(argv, **kwargs)
                with patch.object(V.sys.stdin, "isatty", return_value=True), \
                     patch.object(V.subprocess, "run", side_effect=confirm), \
                     contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    if accept:
                        V.confirm_probe_host_key(self.c)
                    else:
                        with self.assertRaises(V.Error):
                            V.confirm_probe_host_key(self.c)
                expected = (self.unrelated + self.target + " " + self.new_key + "\n").encode() if accept else self.original
                self.assertEqual(self.path.read_bytes(), expected)
                self.assert_no_temporary_files()

    def test_matching_host_alias_removed_without_touching_other_aliases_or_invalid_bytes(self):
        for port in (22, 2222):
            with self.subTest(port=port):
                target = self.c["address"] if port == 22 else f'[{self.c["address"]}]:{port}'
                unrelated = b"malformed\xff\rline\n" + self.unrelated.encode()
                shared = b" \t" + target.encode() + b",example.test\t" + TEST_SOURCE_KEY.encode() + b" shared comment\r\n"
                other_port = f'[{self.c["address"]}]:3333 {TEST_SOURCE_KEY} different port\n'.encode()
                self.path.write_bytes(unrelated + shared + other_port)
                V.remove_probe_host_key(self.path, target)
                self.assertEqual(self.path.read_bytes(), unrelated + shared.replace(target.encode() + b",", b"", 1) + other_port)

    def test_revocations_authorities_and_wildcards_require_manual_update(self):
        for hosts in (f"@revoked {self.target}", f"@cert-authority {self.target}", "*"):
            with self.subTest(hosts=hosts):
                original = (hosts + " " + TEST_SOURCE_KEY + "\n").encode()
                self.path.write_bytes(original)
                with self.assertRaisesRegex(V.Error, "Cannot automatically replace"):
                    V.remove_probe_host_key(self.path, self.target)
                self.assertEqual(self.path.read_bytes(), original)

    def test_trusted_handshake_without_saved_replacement_cannot_publish_removed_entries(self):
        def no_enrollment(argv, **kwargs):
            if str(argv[0]) == "ssh" and self.ssh_calls:
                return subprocess.CompletedProcess(argv, 255, stdout=b"", stderr=b"Permission denied (password).")
            return self.handshake(argv, **kwargs)
        with patch.object(V.sys.stdin, "isatty", return_value=True), \
             patch.object(V.subprocess, "run", side_effect=no_enrollment), \
             contextlib.redirect_stderr(io.StringIO()), self.assertRaisesRegex(V.Error, "No replacement"):
            V.confirm_probe_host_key(self.c)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assert_no_temporary_files()

    def test_changed_key_outside_selected_file_does_not_prompt_for_replacement(self):
        self.path.write_text(self.unrelated)
        with patch.object(V.sys.stdin, "isatty", return_value=True), \
             patch.object(V.subprocess, "run", side_effect=self.handshake), \
             contextlib.redirect_stderr(io.StringIO()) as warning, self.assertRaisesRegex(V.Error, "not found"):
            V.confirm_probe_host_key(self.c)
        self.assertEqual(len(self.ssh_calls), 1)
        self.assertEqual(warning.getvalue(), "")
        self.assertEqual(self.path.read_text(), self.unrelated)
        self.assert_no_temporary_files()

    def test_concurrent_file_update_is_not_overwritten(self):
        concurrent = self.original + b"# concurrent update\n"
        def update_during_confirmation(argv, **kwargs):
            result = self.handshake(argv, **kwargs)
            if str(argv[0]) == "ssh" and len(self.ssh_calls) == 2:
                self.path.write_bytes(concurrent)
            return result
        with patch.object(V.sys.stdin, "isatty", return_value=True), \
             patch.object(V.subprocess, "run", side_effect=update_during_confirmation), \
             contextlib.redirect_stderr(io.StringIO()), self.assertRaisesRegex(V.Error, "changed during confirmation"):
            V.confirm_probe_host_key(self.c)
        self.assertEqual(self.path.read_bytes(), concurrent)
        self.assert_no_temporary_files()

    def test_interrupt_leaves_original_and_cleans_up_staging(self):
        def interrupt(argv, **kwargs):
            if str(argv[0]) == "ssh" and self.ssh_calls:
                raise KeyboardInterrupt()
            return self.handshake(argv, **kwargs)
        with patch.object(V.sys.stdin, "isatty", return_value=True), \
             patch.object(V.subprocess, "run", side_effect=interrupt), \
             contextlib.redirect_stderr(io.StringIO()), self.assertRaises(KeyboardInterrupt):
            V.confirm_probe_host_key(self.c)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assert_no_temporary_files()


class TemporaryInstallationKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.state = self.repo / "hosts/generated/vps-test"
        self.state.mkdir(parents=True)
        env = patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root / "private")})
        env.start()
        self.addCleanup(env.stop)
        self.args = V.parser().parse_args(["probe", "vps-test", "203.0.113.5"])
        self.c = V.connection("203.0.113.5", "root", 22, None, self.args.known_hosts)

    def probe_mocks(self):
        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(V, "confirm_probe_host_key"))
        stack.enter_context(patch.object(V, "prompt_ssh_password", return_value=b"test-password"))
        stack.enter_context(patch.object(V, "inventory", return_value=facts()))
        stack.enter_context(patch.object(V.sys.stdin, "isatty", return_value=True))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        return stack

    def test_probe_consent_saves_key_path_and_reuses_one_password(self):
        keyed = self.c | {"key": "/private/vps-test-install"}
        with self.probe_mocks(), patch("builtins.input", return_value="y") as consent, \
             patch.object(V, "authorize_installation_key", return_value=keyed) as authorize, \
             patch.object(V, "prompt_ssh_password", return_value=b"test-password") as password, \
             patch.object(V, "inventory", return_value=facts()) as inventory:
            V.probe(self.args, self.repo, self.state, self.state)
        password.assert_called_once_with(self.c)
        inventory.assert_called_once_with(self.c, password=b"test-password")
        authorize.assert_called_once_with(self.c, self.state, self.repo, b"test-password")
        consent.assert_called_once()
        saved = V.read_json(self.state / "probe.json")
        self.assertEqual(saved["connection"], keyed)
        self.assertNotIn("test-password", json.dumps(saved))

    def test_probe_decline_empty_eof_and_force_never_authorize(self):
        for answer in ("n", "", EOFError()):
            for force in (False, True):
                self.args.force = force
                (self.state / "probe.json").unlink(missing_ok=True)
                with self.subTest(answer=answer, force=force), self.probe_mocks(), \
                     patch("builtins.input", side_effect=[answer]) as consent, \
                     patch.object(V, "authorize_installation_key") as authorize:
                    V.probe(self.args, self.repo, self.state, self.state)
                consent.assert_called_once()
                authorize.assert_not_called()
                self.assertIsNone(V.read_json(self.state / "probe.json")["connection"]["key"])

    def test_probe_existing_key_never_offers_or_generates_pair(self):
        self.args.key = self.root / "user-key"
        with self.probe_mocks(), patch("builtins.input") as consent, \
             patch.object(V, "authorize_installation_key") as authorize, \
             patch.object(V, "prompt_ssh_password") as password:
            V.probe(self.args, self.repo, self.state, self.state)
        consent.assert_not_called()
        authorize.assert_not_called()
        password.assert_not_called()
        self.assertEqual(V.read_json(self.state / "probe.json")["connection"]["key"], str(self.args.key))

    def test_probe_declined_overwrite_stops_before_authorization(self):
        with self.probe_mocks(), patch.object(V, "confirm_overwrite", side_effect=V.Error("Overwrite declined")), \
             patch.object(V, "authorize_installation_key") as authorize, patch("builtins.input") as consent, \
             self.assertRaisesRegex(V.Error, "Overwrite declined"):
            V.probe(self.args, self.repo, self.state, self.state)
        authorize.assert_not_called()
        consent.assert_not_called()

    def test_authorization_transmits_only_public_key_and_preserves_entries(self):
        home = self.root / "remote-home"
        ssh = home / ".ssh"
        ssh.mkdir(parents=True)
        authorized = ssh / "authorized_keys"
        original = b"ssh-ed25519 existing-user-key comment"
        authorized.write_bytes(original)
        def upload(c, command, script, *, password):
            self.assertEqual(c, self.c)
            self.assertEqual((command, password), ("python3 -", b"test-password"))
            self.assertNotIn(b"PRIVATE KEY", script)
            V.run([V.sys.executable, "-"], script, env={**os.environ, "HOME": str(home)})
        with patch.object(V, "password_inventory", side_effect=upload) as upload_key, \
             patch.object(V, "remote_run", return_value=b"") as prove, \
             contextlib.redirect_stdout(io.StringIO()):
            keyed = V.authorize_installation_key(self.c, self.state, self.repo, b"test-password")
            again = V.authorize_installation_key(self.c, self.state, self.repo, b"test-password")
        key = Path(keyed["key"])
        self.assertEqual(key.name, "vps-test-install")
        self.assertEqual(keyed, again)
        self.assertEqual(upload_key.call_count, 2)
        prove.assert_called_with(keyed, "true", timeout=20)
        public = V.run(["ssh-keygen", "-y", "-f", key]).strip()
        self.assertEqual(authorized.read_bytes(), original + b"\n" + public + b"\n")
        self.assertEqual(key.stat().st_mode & 0o777, 0o600)
        self.assertEqual(key.parent.stat().st_mode & 0o777, 0o700)
        self.assertFalse(self.repo in key.parents)
        self.assertEqual(V.read_json(key.parent / "managed.json"),
                         {"host": "vps-test", "public_key": " ".join(public.decode().split()[:2])})

    def test_failed_key_auth_keeps_pair_and_does_not_publish_probe(self):
        with self.probe_mocks(), patch("builtins.input", return_value="y"), \
             patch.object(V, "password_inventory", return_value=b""), \
             patch.object(V, "remote_run", side_effect=V.Error("Key rejected")), \
             self.assertRaisesRegex(V.Error, "Key rejected"):
            V.probe(self.args, self.repo, self.state, self.state)
        key = V.installation_key(self.state, self.repo)
        self.assertTrue(key.exists())
        self.assertTrue((key.parent / "managed.json").exists())
        self.assertFalse((self.state / "probe.json").exists())

    def test_cleanup_removes_only_managed_pair_and_is_repeatable(self):
        key = V.installation_key(self.state, self.repo, create=True)
        unrelated = key.parent / "keep.txt"
        unrelated.write_text("preserve")
        with contextlib.redirect_stdout(io.StringIO()):
            V.cleanup_installation_key(self.state, self.repo)
            V.cleanup_installation_key(self.state, self.repo)
        self.assertFalse(key.exists())
        self.assertFalse(key.with_suffix(".pub").exists())
        self.assertFalse((key.parent / "managed.json").exists())
        self.assertEqual(unrelated.read_text(), "preserve")

    def test_cleanup_preserves_unmarked_and_protected_user_keys(self):
        key = V.installation_key(self.state, self.repo, create=True)
        with self.assertRaisesRegex(V.Error, "administrator key"):
            V.cleanup_installation_key(self.state, self.repo, protected_keys=[key])
        self.assertTrue(key.exists())
        (key.parent / "managed.json").unlink()
        V.cleanup_installation_key(self.state, self.repo)
        self.assertTrue(key.exists())
        self.assertTrue(key.with_suffix(".pub").exists())

    def test_cleanup_rejects_changed_pair_and_symlinks(self):
        key = V.installation_key(self.state, self.repo, create=True)
        public = key.with_suffix(".pub")
        original = public.read_text()
        public.write_text("ssh-ed25519 changed")
        with self.assertRaisesRegex(V.Error, "public SSH key changed"):
            V.cleanup_installation_key(self.state, self.repo)
        self.assertTrue(key.exists())
        public.unlink()
        target = self.root / "outside.pub"
        target.write_text(original)
        public.symlink_to(target)
        with self.assertRaisesRegex(V.Error, "Unsafe installation SSH pair"):
            V.cleanup_installation_key(self.state, self.repo)
        self.assertTrue(key.exists())
        self.assertEqual(target.read_text(), original)

    def test_cleanup_skips_checkout_state_and_key_creation_is_refused(self):
        state = self.repo / "custom-state/vps-test"
        directory = state / "install-ssh"
        directory.mkdir(parents=True, mode=0o700)
        key = directory / "vps-test-install"
        key.write_text("user file")
        V.write_json(directory / "managed.json", {"host": "vps-test", "public_key": "synthetic"})
        V.cleanup_installation_key(state, self.repo)
        self.assertEqual(key.read_text(), "user file")
        with self.assertRaisesRegex(V.Error, "outside the checkout"):
            V.installation_key(state, self.repo, create=True)

    def test_cleanup_legacy_pair_without_marker_is_not_deleted(self):
        key = V.installation_key(self.state, self.repo, create=True)
        (key.parent / "managed.json").unlink()
        legacy = key.parent / "key"
        key.rename(legacy)
        key.with_suffix(".pub").rename(legacy.with_suffix(".pub"))
        self.assertEqual(V.installation_key(self.state, self.repo), legacy)
        V.cleanup_installation_key(self.state, self.repo)
        self.assertTrue(legacy.exists())


class PlanInterfaceTests(unittest.TestCase):
    def test_defaults_and_positional_public_keys(self):
        args = V.parser().parse_args(["plan", "vps-test", "/admin.pub", "/backup.pub"])
        self.assertEqual(args.admin_key, [Path("/admin.pub"), Path("/backup.pub")])
        self.assertEqual((args.network, args.ssh_port, args.target), ("auto", 22, None))
        self.assertFalse(hasattr(args, "chat_id"))
        args = V.parser().parse_args(["plan", "vps-test", "/admin.pub", "--network", "static",
                                      "--ssh-port", "3333", "--target", "target.test",
                                      "--dns", "1.1.1.1", "--dns", "9.9.9.9",
                                      "--warp-domain", "example.test"])
        self.assertEqual((args.network, args.ssh_port, args.target), ("static", 3333, "target.test"))
        self.assertEqual(args.dns, ["1.1.1.1", "9.9.9.9"])
        self.assertFalse(hasattr(args, "ignore_ipv6"))
        self.assertEqual(args.warp_domain, ["ipinfo.io", "example.test"])

    def test_missing_or_private_key_and_removed_flags_rejected(self):
        for argv in (["plan", "vps-test"], ["plan", "vps-test", "/admin"],
                     ["plan", "vps-test", "--admin-key", "/admin.pub"],
                     ["plan", "vps-test", "/admin.pub", "--chat-id", "123"],
                     ["plan", "vps-test", "/admin.pub", "--ignore-ipv6"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                V.parser().parse_args(argv)


class AdminKeyGenerationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.pub = self.root / "ssh" / "admin.pub"
        self.private = self.pub.with_suffix("")

    def test_existing_public_key_never_prompts_or_generates(self):
        self.pub.parent.mkdir()
        self.pub.write_text("existing public key")
        with patch("builtins.input") as prompt, patch.object(V.subprocess, "run") as run:
            self.assertEqual(V.ensure_admin_key(self.pub, self.repo), self.pub)
        prompt.assert_not_called()
        run.assert_not_called()

    def test_missing_key_noninteractive_fails_without_creating_directory(self):
        with patch.object(V.sys.stdin, "isatty", return_value=False), patch("builtins.input") as prompt, \
             patch.object(V.subprocess, "run") as run, self.assertRaisesRegex(V.Error, "interactive terminal"):
            V.ensure_admin_key(self.pub, self.repo)
        prompt.assert_not_called()
        run.assert_not_called()
        self.assertFalse(self.pub.parent.exists())

    def test_declining_is_default_and_does_not_create_files(self):
        for answer in ("", "n", "no", "anything"):
            with self.subTest(answer=answer), patch.object(V.sys.stdin, "isatty", return_value=True), \
                 patch("builtins.input", return_value=answer), patch.object(V.subprocess, "run") as run, \
                 self.assertRaisesRegex(V.Error, "declined"):
                V.ensure_admin_key(self.pub, self.repo)
            run.assert_not_called()
            self.assertFalse(self.pub.parent.exists())

    def test_existing_private_key_or_dangling_symlink_never_overwritten(self):
        self.pub.parent.mkdir()
        for symlink in (False, True):
            if symlink: self.private.symlink_to("missing-private")
            else: self.private.write_text("keep private key")
            with self.subTest(symlink=symlink), patch("builtins.input") as prompt, \
                 patch.object(V.subprocess, "run") as run, self.assertRaisesRegex(V.Error, "private key already exists"):
                V.ensure_admin_key(self.pub, self.repo)
            prompt.assert_not_called()
            run.assert_not_called()
            if not symlink: self.assertEqual(self.private.read_text(), "keep private key")
            self.private.unlink()
        self.pub.symlink_to("missing-public")
        with patch("builtins.input") as prompt, self.assertRaises(V.Error):
            V.ensure_admin_key(self.pub, self.repo)
        prompt.assert_not_called()

    def test_private_key_generation_inside_checkout_or_store_rejected(self):
        for path in (self.repo / "admin.pub", self.repo / "keys/admin.pub", Path("/nix/store/vps-admin.pub")):
            with self.subTest(path=path), patch("builtins.input") as prompt, patch.object(V.subprocess, "run") as run, \
                 self.assertRaisesRegex(V.Error, "outside the checkout"):
                V.ensure_admin_key(path, self.repo)
            prompt.assert_not_called()
            run.assert_not_called()

    def test_confirmation_generates_valid_pair_with_private_permissions(self):
        real_run = subprocess.run
        def generate(command):
            self.assertEqual(command[:3], ["ssh-keygen", "-t", "ed25519"])
            self.assertNotIn("-N", command)  # Production leaves passphrase input to ssh-keygen.
            # Test only: avoid an interactive passphrase prompt.
            return real_run(command + ["-q", "-N", ""], capture_output=True)
        with patch.object(V.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="YES"), \
             patch.object(V.subprocess, "run", side_effect=generate), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(V.ensure_admin_key(self.pub, self.repo), self.pub)
        self.assertEqual(self.private.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.pub.parent.stat().st_mode & 0o777, 0o700)
        public = real_run(["ssh-keygen", "-y", "-f", str(self.private)], capture_output=True, check=True).stdout.decode().strip()
        self.assertEqual(public.split()[:2], self.pub.read_text().split()[:2])
        self.assertEqual(sorted(p.name for p in self.pub.parent.iterdir()), ["admin", "admin.pub"])

    def test_generator_failure_leaves_no_key_pair(self):
        with patch.object(V.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="y"), \
             patch.object(V.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)), \
             self.assertRaisesRegex(V.Error, "failed or cancelled"):
            V.ensure_admin_key(self.pub, self.repo)
        self.assertEqual(list(self.pub.parent.iterdir()), [])

    def test_concurrent_destination_is_not_overwritten(self):
        for destination in (self.private, self.pub):
            with self.subTest(destination=destination), tempfile.TemporaryDirectory(dir=self.root) as tmp:
                pub = Path(tmp) / "admin.pub"
                private = pub.with_suffix("")
                target = private if destination == self.private else pub
                def generate(command):
                    key = Path(command[-1])
                    key.write_text("new private")
                    key.with_suffix(".pub").write_text("new public")
                    target.write_text("existing concurrent key")
                    return subprocess.CompletedProcess(command, 0)
                with patch.object(V.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="y"), \
                     patch.object(V.subprocess, "run", side_effect=generate), self.assertRaises(FileExistsError):
                    V.ensure_admin_key(pub, self.repo)
                self.assertEqual(target.read_text(), "existing concurrent key")


class AdminAgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.bootstrap = self.root / "bootstrap"
        self.config = self.root / "config"
        self.identity = self.config / "sops/age/vps-test.txt"
        self.args = argparse.Namespace(name="vps-test", admin_recipient=None)
        self.env = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.config)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_explicit_public_recipient_needs_no_prompt_or_private_file(self):
        self.args.admin_recipient = TEST_ADMIN
        with patch("builtins.input") as prompt, patch.object(V, "run") as run:
            self.assertEqual(V.prepare_admin_age(self.args, self.repo, self.bootstrap), (TEST_ADMIN, None))
        prompt.assert_not_called()
        run.assert_not_called()
        self.assertFalse(self.config.exists())

    def test_invalid_explicit_recipient_rejected(self):
        self.args.admin_recipient = "AGE-SECRET-KEY-PRIVATE"
        with self.assertRaises(V.Error):
            V.prepare_admin_age(self.args, self.repo, self.bootstrap)

    def test_default_decline_and_noninteractive_leave_no_private_file(self):
        for interactive in (True, False):
            with self.subTest(interactive=interactive), patch.object(V.sys.stdin, "isatty", return_value=interactive), \
                 patch("builtins.input", return_value=""), patch.object(V, "run") as run, self.assertRaises(V.Error):
                V.prepare_admin_age(self.args, self.repo, self.bootstrap)
            run.assert_not_called()
            self.assertFalse(self.config.exists())

    def test_confirmed_creation_is_private_and_never_prints_material(self):
        material = b"AGE-SECRET-KEY-TEST\n"
        with patch.object(V.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="y"), \
             patch.object(V, "run", side_effect=[material, TEST_ADMIN.encode()]) as run, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            result = V.prepare_admin_age(self.args, self.repo, self.bootstrap)
        self.assertEqual(result, (TEST_ADMIN, str(self.identity)))
        self.assertEqual(self.identity.read_bytes(), material)
        self.assertEqual(self.identity.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.identity.parent.stat().st_mode & 0o777, 0o700)
        self.assertNotIn("AGE-SECRET-KEY-TEST", output.getvalue())
        self.assertEqual(run.call_args_list[1].args, (["age-keygen", "-y", "/dev/stdin"], material))
        self.assertFalse(self.bootstrap.exists())

    def test_existing_identity_is_reused_without_prompt_or_overwrite(self):
        self.identity.parent.mkdir(parents=True)
        self.identity.write_text("keep private key")
        self.identity.chmod(0o600)
        for interactive in (True, False):
            with self.subTest(interactive=interactive), patch.object(V.sys.stdin, "isatty", return_value=interactive), \
                 patch("builtins.input") as prompt, patch.object(V, "run", return_value=TEST_ADMIN.encode()) as run, \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(V.prepare_admin_age(self.args, self.repo, self.bootstrap), (TEST_ADMIN, str(self.identity)))
            prompt.assert_not_called()
            run.assert_called_once_with(["age-keygen", "-y", self.identity])
            self.assertEqual(self.identity.read_text(), "keep private key")
            self.assertNotIn("keep private key", output.getvalue())

    def test_invalid_existing_identity_recipient_does_not_trigger_creation(self):
        self.identity.parent.mkdir(parents=True)
        self.identity.write_text("keep private key")
        self.identity.chmod(0o600)
        with patch("builtins.input") as prompt, patch.object(V, "run", return_value=b"invalid") as run, \
             self.assertRaisesRegex(V.Error, "Invalid administrator age recipient"):
            V.prepare_admin_age(self.args, self.repo, self.bootstrap)
        prompt.assert_not_called()
        run.assert_called_once_with(["age-keygen", "-y", self.identity])
        self.assertEqual(self.identity.read_text(), "keep private key")

    def test_unsafe_identity_and_paths_rejected_before_prompt(self):
        self.identity.parent.mkdir(parents=True)
        self.identity.write_text("publicly readable key")
        self.identity.chmod(0o644)
        with patch("builtins.input") as prompt, self.assertRaises(V.Error):
            V.prepare_admin_age(self.args, self.repo, self.bootstrap)
        prompt.assert_not_called()
        self.identity.unlink()
        self.identity.symlink_to("missing")
        with patch("builtins.input") as prompt, self.assertRaises(V.Error):
            V.prepare_admin_age(self.args, self.repo, self.bootstrap)
        prompt.assert_not_called()
        for config in (self.repo / "config", Path("/nix/store/config"), self.bootstrap):
            with self.subTest(config=config), patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config)}), \
                 patch("builtins.input") as prompt, self.assertRaises(V.Error):
                V.prepare_admin_age(self.args, self.repo, self.bootstrap)
            prompt.assert_not_called()

    def test_invalid_generation_or_concurrent_file_never_overwritten(self):
        with patch.object(V.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="y"), \
             patch.object(V, "run", side_effect=[b"private", b"invalid"]), self.assertRaises(V.Error):
            V.prepare_admin_age(self.args, self.repo, self.bootstrap)
        self.assertFalse(self.identity.exists())
        def generate(command, *_args):
            if "-y" in command:
                self.identity.parent.mkdir(parents=True)
                self.identity.write_text("concurrent key")
                return TEST_ADMIN.encode()
            return b"new private"
        with patch.object(V.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="y"), \
             patch.object(V, "run", side_effect=generate), self.assertRaises(FileExistsError):
            V.prepare_admin_age(self.args, self.repo, self.bootstrap)
        self.assertEqual(self.identity.read_text(), "concurrent key")


class TargetSelectionTests(unittest.TestCase):
    def setUp(self):
        self.c = V.connection("203.0.113.5", "root", 22, "/key", "/known")

    def test_only_passing_candidates_offered_from_vps(self):
        def probe(c, public):
            self.assertEqual(c, self.c)
            self.assertEqual(public["target"], public["serverName"] + ":443")
            if public["serverName"] == V.REALITY_CANDIDATES[0]:
                raise V.Error("failed")
        with patch.object(V.sys.stdin, "isatty", return_value=True), patch.object(V, "run") as run, \
             patch.object(V, "preflight", side_effect=probe) as preflight, \
             patch("builtins.input", return_value="2"), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(V.choose_target(self.c), V.REALITY_CANDIDATES[2])
        self.assertEqual(preflight.call_count, len(V.REALITY_CANDIDATES))
        self.assertIn("StrictHostKeyChecking=yes", run.call_args.args[0])
        self.assertNotIn("1. " + V.REALITY_CANDIDATES[0], output.getvalue())

    def test_noninteractive_requires_explicit_target_without_network(self):
        with patch.object(V.sys.stdin, "isatty", return_value=False), patch.object(V, "run") as run, \
             self.assertRaisesRegex(V.Error, "--target"):
            V.choose_target(self.c)
        run.assert_not_called()

    def test_missing_remote_openssl_stops_before_candidates(self):
        with patch.object(V.sys.stdin, "isatty", return_value=True), patch.object(V, "run", side_effect=V.Error("tool")), \
             patch.object(V, "preflight") as preflight, self.assertRaises(V.Error):
            V.choose_target(self.c)
        preflight.assert_not_called()

    def test_all_failed_or_timed_out_requires_explicit_target(self):
        with patch.object(V.sys.stdin, "isatty", return_value=True), patch.object(V, "run"), \
             patch.object(V, "preflight", side_effect=[V.Error("failed"), subprocess.TimeoutExpired("ssh", 30)] * 2), \
             patch("builtins.input") as prompt, contextlib.redirect_stdout(io.StringIO()), self.assertRaises(V.Error):
            V.choose_target(self.c)
        prompt.assert_not_called()

    def test_invalid_selection_cancels(self):
        for answer in ("", "0", "99", "domain.test", "١"):
            with self.subTest(answer=answer), patch.object(V.sys.stdin, "isatty", return_value=True), \
                 patch.object(V, "run"), patch.object(V, "preflight"), patch("builtins.input", return_value=answer), \
                 contextlib.redirect_stdout(io.StringIO()), self.assertRaises(V.Error):
                V.choose_target(self.c)

    def test_preflight_checks_tls_certificate_and_alpn(self):
        good = b"New, TLSv1.3\nALPN protocol: h2\nVerify return code: 0 (ok)\n"
        for output, code in ((good, 0), (good.replace(b"TLSv1.3", b"TLSv1.2"), 0),
                             (good.replace(b"h2", b"http/1.1"), 0),
                             (good.replace(b"0 (ok)", b"1 (bad)"), 0), (good, 1)):
            with self.subTest(output=output, code=code), patch.object(V.subprocess, "run",
                    return_value=subprocess.CompletedProcess([], code, output, b"")) as run:
                if output == good and code == 0:
                    V.preflight(self.c, {"serverName": "target.test", "target": "target.test:443"})
                else:
                    with self.assertRaises(V.Error):
                        V.preflight(self.c, {"serverName": "target.test", "target": "target.test:443"})
                self.assertIn("StrictHostKeyChecking=yes", run.call_args.args[0])
                self.assertIn("-verify_return_error", run.call_args.args[0][-1])


class InstallerToolsTests(unittest.TestCase):
    def setUp(self):
        self.c = V.connection("203.0.113.5", "root", 22, "/key with spaces", "/known with spaces")
        self.tools = "/nix/store/" + "a" * 32 + "-vps-installer-tools"
        self.repo = Path("/checkout")

    def test_source_environment_is_readonly_without_python_or_package_changes(self):
        with patch.object(V, "remote_run", return_value=b"source\n") as remote, \
             patch.object(V, "run") as run:
            self.assertEqual(V.prepare_installer_tools(self.c, self.repo, password=b"secret"), self.c)
        command = remote.call_args.args[1]
        self.assertIn("/etc/os-release", command)
        self.assertIn('"$ID" = nixos', command)
        self.assertIn('"${VARIANT_ID-}" = installer', command)
        self.assertNotIn("python", command)
        self.assertEqual(remote.call_args.kwargs["password"], b"secret")
        run.assert_not_called()

    def test_environment_gate_does_not_copy_to_ubuntu_or_installed_nixos(self):
        with tempfile.TemporaryDirectory() as tmp:
            release = Path(tmp) / "os-release"
            def remote(c, command, **kwargs):
                command = command.replace(". /etc/os-release;", ". " + shlex.quote(str(release)) + ";")
                return subprocess.run(["bash", "-c", command], check=True, capture_output=True).stdout
            for distro, variant in (("ubuntu", ""), ("nixos", ""), ("ubuntu", "installer"), ("nixos", "installer")):
                release.write_text(f"ID={distro}\nVARIANT_ID={variant}\n")
                with self.subTest(distro=distro, variant=variant), \
                     patch.dict(os.environ, {"VPS_INSTALLER_TOOLS": self.tools}), \
                     patch.object(V.Path, "is_dir", return_value=True), \
                     patch.object(V, "remote_run", side_effect=remote), patch.object(V, "run", return_value=b"") as run:
                    c = V.prepare_installer_tools(self.c, self.repo)
                expected = distro == "nixos" and variant == "installer"
                self.assertEqual("installer_tools" in c, expected)
                self.assertEqual(run.call_count, int(expected))

    def test_host_key_failure_stops_before_copy_or_build(self):
        error = V.Error("SSH failed", details={"tool": "ssh", "exit_code": 255,
                                              "stderr": b"Host key verification failed"})
        with patch.object(V, "remote_run", side_effect=error) as remote, \
             patch.object(V, "run") as run, self.assertRaises(V.Error):
            V.prepare_installer_tools(self.c, self.repo)
        remote.assert_called_once()
        run.assert_not_called()

    def test_installer_copy_pins_ssh_identity_and_preserves_paths_with_spaces(self):
        with patch.dict(os.environ, {"VPS_INSTALLER_TOOLS": self.tools, "NIX_SSHOPTS": "-o StrictHostKeyChecking=no"}), \
             patch.object(V.Path, "is_dir", return_value=True), \
             patch.object(V, "remote_run", return_value=b"installer\n"), \
             patch.object(V, "run", return_value=b"") as run:
            c = V.prepare_installer_tools(self.c, self.repo)
        self.assertEqual(c, self.c | {"installer_tools": self.tools})
        self.assertNotIn("installer_tools", self.c)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["nix", "copy", "--to", "ssh://root@203.0.113.5", self.tools])
        options = shlex.split(run.call_args.kwargs["env"]["NIX_SSHOPTS"])
        self.assertEqual(options, V.ssh_args(self.c)[1:-1])
        self.assertIn("StrictHostKeyChecking=yes", options)
        self.assertIn("UserKnownHostsFile=/known with spaces", options)
        self.assertIn("/key with spaces", options)
        self.assertNotIn("StrictHostKeyChecking=no", options)

    def test_direct_script_builds_tools_locally_from_the_repo(self):
        c = self.c | {"port": 2222}
        with patch.dict(os.environ, {}, clear=True), patch.object(V.Path, "is_dir", return_value=True), \
             patch.object(V, "remote_run", return_value=b"installer\n"), \
             patch.object(V, "run", side_effect=[(self.tools + "\n").encode(), b""]) as run:
            result = V.prepare_installer_tools(c, self.repo)
        self.assertEqual(result["installer_tools"], self.tools)
        self.assertEqual(run.call_args_list[0].args[0],
                         ["nix", "build", "path:/checkout#vps-installer-tools", "--no-link", "--print-out-paths"])
        self.assertEqual(shlex.split(run.call_args.kwargs["env"]["NIX_SSHOPTS"])[:2], ["-p", "2222"])

    def test_unidentified_environment_and_missing_key_never_copy_tools(self):
        for response, c in ((b"untrusted output\n", self.c),
                            (b"installer\n", self.c | {"key": None}),
                            (b"installer\n", self.c | {"user": "ubuntu"})):
            with self.subTest(response=response, user=c["user"]), \
                 patch.object(V, "remote_run", return_value=response), patch.object(V, "run") as run, \
                 self.assertRaises(V.Error):
                V.prepare_installer_tools(c, self.repo)
            run.assert_not_called()

    def test_invalid_or_unavailable_local_tools_never_copy(self):
        for tools, available in (("/tmp/tools; touch /tmp/injected", True), (self.tools, False),
                                 (self.tools + "\n/nix/store/extra", True)):
            with self.subTest(tools=tools, available=available), \
                 patch.dict(os.environ, {"VPS_INSTALLER_TOOLS": tools}), \
                 patch.object(V.Path, "is_dir", return_value=available), \
                 patch.object(V, "remote_run", return_value=b"installer\n"), patch.object(V, "run") as run, \
                 self.assertRaises(V.Error):
                V.prepare_installer_tools(self.c, self.repo)
            run.assert_not_called()

    def test_copy_failure_is_not_retried(self):
        with patch.dict(os.environ, {"VPS_INSTALLER_TOOLS": self.tools}), \
             patch.object(V.Path, "is_dir", return_value=True), \
             patch.object(V, "remote_run", return_value=b"installer\n"), \
             patch.object(V, "run", side_effect=V.Error("Copy failed")) as run, \
             self.assertRaisesRegex(V.Error, "Copy failed"):
            V.prepare_installer_tools(self.c, self.repo)
        run.assert_called_once()

    def test_remote_python_works_when_installer_path_has_no_python(self):
        # Run the actual remote shell command locally, never SSH. This reproduces
        # exit 127 and proves the copied tools PATH reaches the collector process.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bin").mkdir()
            (root / "bin/python3").symlink_to(sys.executable)
            c = self.c | {"installer_tools": str(root)}
            shell = V.shutil.which("bash")
            with patch.dict(os.environ, {"PATH": str(root / "empty")}), \
                 patch.object(V, "ssh_args", return_value=[shell, "-c"]):
                with self.assertRaises(V.Error) as error:
                    V.remote_run(self.c, "python3 -", b"print('inventory')")
                self.assertEqual(error.exception.details["exit_code"], 127)
                result = V.remote_run(c, "python3 -", b"import json, os; print(json.dumps([os.geteuid(), os.environ['SSL_CERT_FILE']]))")
                # Shell builtins must work too, not be executed as external tools.
                self.assertEqual(V.remote_run(c, "set -eu; printf ready"), b"ready")
            self.assertEqual(json.loads(result), [os.geteuid(), str(root / "etc/ssl/certs/ca-bundle.crt")])

    def test_tls_preflight_uses_copied_openssl_and_explicit_ca_bundle(self):
        c = self.c | {"installer_tools": self.tools}
        with patch.object(V, "run", return_value=b"TLSv1.3\nALPN protocol: h2\nVerify return code: 0 (ok)\n") as run:
            V.preflight(c, {"serverName": "target.test", "target": "target.test:443"})
        command = run.call_args.args[0][-1]
        self.assertIn("PATH=" + self.tools + "/bin", command)
        self.assertIn("SSL_CERT_FILE=" + self.tools + "/etc/ssl/certs/ca-bundle.crt", command)
        self.assertIn("openssl s_client", command)
        self.assertIn("-verify_return_error", command)

    def test_resume_prepares_tools_before_inventory_without_kexec(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict(os.environ, {"VPS_INSTALLER_TOOLS": self.tools}), \
             patch.object(V.Path, "is_dir", return_value=True), \
             patch.object(V, "remote_run", return_value=b"installer\n") as remote, \
             patch.object(V, "run", return_value=b"") as run, \
             patch.object(V, "inventory", side_effect=lambda c: (
                 self.assertEqual(c["installer_tools"], self.tools) or facts() | {"os": "nixos", "installer": True})) as inv, \
             patch.object(V, "wait_for_installer") as wait, contextlib.redirect_stdout(io.StringIO()):
            plan = {"connection": self.c, "source_host_key": TEST_SOURCE_KEY}
            c, fresh, password = V.detect_install_environment(plan, Path(tmp), self.repo)
        self.assertTrue(fresh["installer"])
        self.assertIsNone(password)
        self.assertEqual(c["installer_tools"], self.tools)
        remote.assert_called_once()
        run.assert_called_once()
        inv.assert_called_once_with(c)
        wait.assert_not_called()


    def test_custom_source_installer_switches_to_pinned_root_before_tools_copy(self):
        source = self.c | {"user": "ubuntu", "port": 2222}
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict(os.environ, {"VPS_INSTALLER_TOOLS": self.tools}), \
             patch.object(V.Path, "is_dir", return_value=True), \
             patch.object(V, "remote_run", return_value=b"installer\n") as remote, \
             patch.object(V, "run", return_value=b"") as run, \
             patch.object(V, "inventory", return_value=facts() | {"os": "nixos", "installer": True}) as inv, \
             patch.object(V, "wait_for_installer") as wait, contextlib.redirect_stdout(io.StringIO()):
            plan = {"connection": source, "source_host_key": TEST_SOURCE_KEY}
            c, fresh, _ = V.detect_install_environment(plan, Path(tmp), self.repo)
            self.assertEqual(Path(c["known_hosts"]).read_text(), "203.0.113.5 " + TEST_SOURCE_KEY + "\n")
        self.assertEqual((c["user"], c["port"]), ("root", 22))
        self.assertTrue(fresh["installer"])
        self.assertEqual(remote.call_count, 2)
        self.assertEqual(remote.call_args_list[0].args[0], source)
        wait.assert_called_once()
        inv.assert_called_once_with(c)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][3], "ssh://root@203.0.113.5")


class InstallDetectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.repo, self.state = root / "repo", root / "state"
        self.repo.mkdir()
        self.state.mkdir()
        self.c = V.connection("203.0.113.5", "ubuntu", 2222, "/source-key", "/known")
        self.plan = {"connection": self.c, "source_host_key": TEST_SOURCE_KEY}
        prepare = patch.object(V, "prepare_installer_tools", side_effect=lambda c, repo, **kw: c)
        prepare.start()
        self.addCleanup(prepare.stop)

    def installer_facts(self):
        return facts() | {"os": "nixos", "installer": True}

    def connection_error(self, stderr, code=255, tool="ssh"):
        return V.Error("SSH output withheld", details={"tool": tool, "exit_code": code, "stderr": stderr})

    def test_custom_source_connection_is_preserved_on_ubuntu(self):
        with patch.object(V, "inventory", return_value=facts()) as inv, \
             patch.object(V, "wait_for_installer") as wait, patch("builtins.input") as prompt:
            c, fresh, password = V.detect_install_environment(self.plan, self.state, self.repo)
        self.assertEqual(c, self.c)
        self.assertEqual(fresh, facts())
        self.assertIsNone(password)
        inv.assert_called_once_with(self.c)
        wait.assert_not_called()
        prompt.assert_not_called()

    def test_custom_source_falls_back_to_authenticated_root_installer(self):
        for stderr in (b"Connection refused", b"Connection timed out", b"Permission denied (publickey).",
                       b"No route to host", b"Network is unreachable"):
            with self.subTest(stderr=stderr), \
                 patch.object(V, "inventory", side_effect=[self.connection_error(stderr), self.installer_facts()]) as inv, \
                 patch.object(V, "wait_for_installer") as wait, \
                 patch.object(V, "prompt_ssh_password") as password_prompt:
                c, fresh, password = V.detect_install_environment(self.plan, self.state, self.repo)
            self.assertEqual((c["user"], c["port"], c["key"]), ("root", 22, self.c["key"]))
            self.assertEqual(Path(c["known_hosts"]).read_text(), "203.0.113.5 " + TEST_SOURCE_KEY + "\n")
            self.assertTrue(fresh["installer"])
            self.assertIsNone(password)
            self.assertEqual([call.args[0] for call in inv.call_args_list], [self.c, c])
            wait.assert_called_once_with(c)
            password_prompt.assert_not_called()

    def test_root_source_reboot_in_progress_waits_and_detects_installer(self):
        self.c.update(user="root", port=22)
        with patch.object(V, "inventory", side_effect=[
                self.connection_error(b"Connection refused"), self.installer_facts()]) as inv, \
             patch.object(V, "wait_for_installer") as wait:
            c, fresh, _ = V.detect_install_environment(self.plan, self.state, self.repo)
        self.assertTrue(fresh["installer"])
        self.assertEqual(inv.call_count, 2)
        self.assertEqual(inv.call_args_list[0].args[0], c)
        wait.assert_called_once_with(c)

    def test_source_with_installer_variant_switches_to_root_port22(self):
        with patch.object(V, "inventory", side_effect=[self.installer_facts(), self.installer_facts()]) as inv, \
             patch.object(V, "wait_for_installer") as wait:
            c, fresh, _ = V.detect_install_environment(self.plan, self.state, self.repo)
        self.assertEqual((c["user"], c["port"]), ("root", 22))
        self.assertTrue(fresh["installer"])
        self.assertEqual([call.args[0] for call in inv.call_args_list], [self.c, c])
        wait.assert_called_once_with(c)

    def test_wrong_host_key_or_collector_error_never_uses_fallback(self):
        errors = (
            self.connection_error(b"Host key verification failed"),
            self.connection_error(b"REMOTE HOST IDENTIFICATION HAS CHANGED"),
            self.connection_error(b"No ED25519 host key is known and you have requested strict checking."),
            self.connection_error(b"Permission denied\nHost key verification failed"),
            self.connection_error(b"Permission denied", code=1),
            self.connection_error(b"unclassified SSH failure"),
            V.Error("Invalid inventory"),
            ValueError("Malformed collector response"),
        )
        for error in errors:
            with self.subTest(error=error), patch.object(V, "inventory", side_effect=error) as inv, \
                 patch.object(V, "wait_for_installer") as wait, self.assertRaises(type(error)):
                V.detect_install_environment(self.plan, self.state, self.repo)
            inv.assert_called_once()
            wait.assert_not_called()

    def test_fallback_requires_positive_installer_identification(self):
        for fresh in (facts(), facts() | {"os": "nixos", "installer": False}):
            with self.subTest(os=fresh["os"]), patch.object(V, "inventory", side_effect=[
                    self.connection_error(b"Connection refused"), fresh]), \
                 patch.object(V, "wait_for_installer"), \
                 self.assertRaisesRegex(V.Error, "not a NixOS installer"):
                V.detect_install_environment(self.plan, self.state, self.repo)

    def test_installer_root_endpoint_is_rechecked_after_custom_login(self):
        with patch.object(V, "inventory", side_effect=[self.installer_facts(), facts()]), \
             patch.object(V, "wait_for_installer"), \
             self.assertRaisesRegex(V.Error, "not a NixOS installer"):
            V.detect_install_environment(self.plan, self.state, self.repo)

    def test_failed_installer_readiness_never_collects_second_inventory(self):
        with patch.object(V, "inventory", side_effect=self.connection_error(b"Connection refused")) as inv, \
             patch.object(V, "wait_for_installer", side_effect=V.Error("SSH did not become ready")), \
             self.assertRaisesRegex(V.Error, "did not become ready"):
            V.detect_install_environment(self.plan, self.state, self.repo)
        inv.assert_called_once()

    def test_password_failure_is_not_retried_or_treated_as_installer(self):
        self.c["key"] = None
        error = self.connection_error(b"Permission denied", code=5, tool="sshpass")
        with patch.object(V, "prompt_ssh_password", return_value=b"secret") as password, \
             patch.object(V, "inventory", side_effect=error) as inv, \
             patch.object(V, "wait_for_installer") as wait, self.assertRaises(V.Error):
            V.detect_install_environment(self.plan, self.state, self.repo)
        password.assert_called_once_with(self.c)
        inv.assert_called_once_with(self.c, password=b"secret")
        wait.assert_not_called()

    def test_missing_saved_key_does_not_allow_password_only_installer_resume(self):
        self.c["key"] = None
        with patch.object(V, "prompt_ssh_password", return_value=b"secret"), \
             patch.object(V, "inventory", return_value=self.installer_facts()), \
             patch.object(V, "wait_for_installer") as wait, \
             self.assertRaisesRegex(V.Error, "saved installation SSH key is missing"):
            V.detect_install_environment(self.plan, self.state, self.repo)
        wait.assert_not_called()

    def test_unsafe_retained_identity_is_rejected_before_network_or_password(self):
        self.c["key"] = None
        V.installation_key_directory(self.state, self.repo).mkdir(mode=0o755)
        with patch.object(V, "inventory") as inv, patch.object(V, "prompt_ssh_password") as password, \
             self.assertRaisesRegex(V.Error, "unsafe installation SSH directory"):
            V.detect_install_environment(self.plan, self.state, self.repo)
        inv.assert_not_called()
        password.assert_not_called()


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.bootstrap = self.root / "bootstrap"
        self.bootstrap.mkdir(mode=0o700)
        previous = os.umask(0o077)
        try:
            (self.bootstrap / "etc/ssh").mkdir(parents=True, mode=0o700)
        finally:
            os.umask(previous)
        self.key = self.bootstrap / "etc/ssh/ssh_host_ed25519_key"
        V.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", self.key])
        self.key.chmod(0o600)
        self.credentials = {"bootstrap_dir": str(self.bootstrap),
                            "host_public_key": self.key.with_suffix(".pub").read_text().strip()}

    def test_check_normalizes_legacy_target_directories_without_exposing_private_key(self):
        before = {p: p.read_bytes() for p in (self.key, self.key.with_suffix(".pub"))}
        previous = os.umask(0o077)
        try:
            self.assertEqual(V.bootstrap_check(self.credentials, self.repo), self.bootstrap)
        finally:
            os.umask(previous)
        self.assertEqual(self.bootstrap.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.key.stat().st_mode & 0o777, 0o600)
        for relative in ("etc", "etc/ssh"):
            self.assertEqual((self.bootstrap / relative).stat().st_mode & 0o777, 0o755)
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def extract_bootstrap(self, target):
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            tar.add(self.bootstrap, arcname=".")
        archive.seek(0)
        with tarfile.open(fileobj=archive) as tar:
            tar.extractall(target, filter="tar")
        # Upstream restores /mnt only, not /mnt/etc or /mnt/etc/ssh.
        target.chmod(0o755)

    def test_preserved_tar_modes_reproduce_lockout_and_preflight_prevents_it(self):
        bad, good = self.root / "bad-target", self.root / "good-target"
        bad.mkdir(); good.mkdir()
        self.extract_bootstrap(bad)
        for relative in ("etc", "etc/ssh"):
            self.assertEqual((bad / relative).stat().st_mode & 0o777, 0o700)
        V.bootstrap_check(self.credentials, self.repo)
        self.extract_bootstrap(good)
        for relative in ("etc", "etc/ssh"):
            self.assertEqual((good / relative).stat().st_mode & 0o777, 0o755)
        self.assertEqual((good / "etc/ssh/ssh_host_ed25519_key").stat().st_mode & 0o777, 0o600)

    def test_unsafe_key_or_staging_root_is_refused_not_relaxed(self):
        for path in (self.key, self.bootstrap):
            mode = path.stat().st_mode & 0o777
            path.chmod(0o755)
            with self.subTest(path=path.name), self.assertRaisesRegex(V.Error, "permissions are too broad"):
                V.bootstrap_check(self.credentials, self.repo)
            path.chmod(mode)
        self.assertEqual((self.bootstrap / "etc/ssh").stat().st_mode & 0o777, 0o700)

    def test_mismatched_host_key_is_refused_before_normalizing_permissions(self):
        with self.assertRaisesRegex(V.Error, "host keys do not match"):
            V.bootstrap_check(self.credentials | {"host_public_key": TEST_SOURCE_KEY}, self.repo)
        self.assertEqual((self.bootstrap / "etc/ssh").stat().st_mode & 0o777, 0o700)

    def test_unexpected_files_and_symlinks_are_refused_without_chmod(self):
        extra = self.bootstrap / "extra"
        extra.write_text("unrelated")
        with self.assertRaisesRegex(V.Error, "unexpected files/symlinks"):
            V.bootstrap_check(self.credentials, self.repo)
        extra.unlink()
        directory = self.bootstrap / "etc/ssh"
        target = self.root / "outside-ssh"
        directory.rename(target)
        directory.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(V.Error, "unexpected files/symlinks"):
            V.bootstrap_check(self.credentials, self.repo)
        self.assertEqual(target.stat().st_mode & 0o777, 0o700)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        (self.repo / "flake.nix").write_text("{}")
        (self.repo / "hosts/generated").mkdir(parents=True)
        (self.repo / "hosts/generated.nix").write_text("{}")
        (self.repo / "hosts/vps-source.nix").write_text("{}")
        (self.repo / "hosts/generated/inventory.json").write_text("{}")
        (self.repo / "hosts/wsl-inventory.json").write_text("{}")
        self.state = Path(self.tmp.name) / "state"
        self.state.mkdir()
        self.host = self.repo / "hosts/generated/vps-test"
        self.c = V.connection("203.0.113.5", "root", 22, "/key", "/known")
        V.write_json(self.state / "probe.json", {"connection": self.c, "facts": facts()})
        self.key = Path(self.tmp.name) / "admin.pub"
        key = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + bytes(range(32))
        self.key.write_text("ssh-ed25519 " + base64.b64encode(key).decode() + " test")
        self.args = argparse.Namespace(name="vps-test", network="static", dns=[],
            admin_key=[self.key], admin_user="admin", target="target.test", ssh_port=3333, warp_domain=["ipinfo.io"])
        # These tests exercise provisioning/install behaviour; overwrite prompts
        # are exercised separately by OverwriteTests and registry tests below.
        overwrite_patch = patch.object(V, "confirm_overwrite")
        self.overwrite = overwrite_patch.start()
        self.addCleanup(overwrite_patch.stop)

    def create_plan(self):
        with contextlib.redirect_stdout(io.StringIO()):
            V.plan(self.args, self.repo, self.state, self.host)
        V.write_json(self.state / "credentials.json", {"bootstrap_dir": "/private/bootstrap",
            "host_public_key": "ssh-ed25519 AAAATEST", "host_recipient": "host", "admin_recipient": "admin"})
        digests = {name + "_digest": hashlib.sha256(json.dumps(V.read_json(self.state / (name + ".json")), sort_keys=True).encode()).hexdigest()
                   for name in ("plan", "credentials")}
        V.write_json(self.state / "checked.json", {"source_digest": "checked-digest", "system": "/nix/store/system", "disko": "/nix/store/disko"} | digests)

    def create_admin_private_key(self):
        key = self.key.with_suffix("")
        key.write_text("synthetic private administrator key")
        key.chmod(0o600)
        return key

    def install_args(self):
        return argparse.Namespace(name="vps-test", force=True)

    def install_mocks(self, fresh):
        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(V, "bootstrap_check", return_value=Path("/private/bootstrap")))
        stack.enter_context(patch.object(V, "source_digest", return_value="checked-digest"))
        stack.enter_context(patch.object(V, "prepare_installer_tools", side_effect=lambda c, repo, **kw: c))
        exists = Path.exists
        stack.enter_context(patch.object(Path, "exists", autospec=True,
            side_effect=lambda path: str(path) in ("/nix/store/system", "/nix/store/disko") or exists(path)))
        stack.enter_context(patch.object(V, "inventory", return_value=fresh))
        stack.enter_context(patch.object(V, "preflight"))
        stack.enter_context(patch.object(V, "wait_for_installer"))
        return stack

    def test_plan_disables_probed_ipv6_without_an_opt_out_flag(self):
        f = facts()
        f["addresses"][0]["addr_info"].append(
            {"family": "inet6", "scope": "global", "local": "2001:db8::1"})
        V.write_json(self.state / "probe.json", {"connection": self.c, "facts": f})
        self.create_plan()
        machine = V.read_json(self.host / "machine.json")
        self.assertEqual(machine["network"]["config"]["address"], ["203.0.113.5/32"])
        self.assertEqual(machine["network"]["config"]["networkConfig"]["LinkLocalAddressing"], "no")
        self.assertNotIn("ignore_ipv6", V.read_json(self.state / "plan.json"))

    def test_plan_registers_host_and_refuses_second_generation(self):
        self.create_plan()
        machine = V.read_json(self.host / "machine.json")
        self.assertEqual(machine["disk"]["serial"], "synthetic-disk")
        self.assertEqual(machine["firmware"], "bios")
        self.assertIn("vps-test", V.read_json(self.repo / "hosts/generated/inventory.json"))
        self.assertNotIn("machine_id", machine)
        self.assertIn("qemu-guest", (self.host / "hardware-configuration.nix").read_text())
        self.assertIn("EF00", (self.host / "disk-config.nix").read_text())
        with self.assertRaises(V.Error):
            V.plan(self.args, self.repo, self.state, self.host)

    def test_declining_registry_overwrite_does_not_publish_partial_plan(self):
        self.overwrite.side_effect = V.Error("Overwrite declined")
        with self.assertRaisesRegex(V.Error, "Overwrite declined"):
            V.plan(self.args, self.repo, self.state, self.host)
        self.assertFalse(self.host.exists())
        self.assertFalse((self.state / "plan.json").exists())
        self.assertEqual(V.read_json(self.repo / "hosts/generated/inventory.json"), {})

    def test_plan_initializes_missing_registry_only_when_no_hosts_exist(self):
        registry = self.repo / "hosts/generated/inventory.json"
        registry.unlink()
        self.create_plan()
        self.assertEqual(V.read_json(registry), {"vps-test": {"system": "x86_64-linux"}})
        self.assertTrue((self.host / "configuration.nix").is_file())

    def test_plan_creates_missing_generated_directory(self):
        generated = self.repo / "hosts/generated"
        (generated / "inventory.json").unlink()
        generated.rmdir()
        self.create_plan()
        self.assertEqual(V.read_json(generated / "inventory.json"), {"vps-test": {"system": "x86_64-linux"}})
        self.assertEqual({path.name for path in generated.iterdir()}, {"inventory.json", "vps-test"})
        self.assertEqual({path.name for path in self.host.iterdir()}, {
            "machine.json", "vless.json", "configuration.nix", "disk-config.nix", "hardware-configuration.nix"})

    def test_plan_refuses_to_reset_missing_registry_with_existing_hosts(self):
        registry = self.repo / "hosts/generated/inventory.json"
        registry.unlink()
        previous = self.repo / "hosts/generated/previous-host"
        previous.mkdir()
        config = previous / "configuration.nix"
        config.write_text("{ preserved = true; }")
        with patch.object(V, "choose_target") as choose, \
             self.assertRaisesRegex(V.Error, "restore the registry rather than resetting it"):
            V.plan(self.args, self.repo, self.state, self.host)
        choose.assert_not_called()
        self.assertEqual(config.read_text(), "{ preserved = true; }")
        self.assertFalse(registry.exists())
        self.assertFalse(self.host.exists())
        self.assertFalse((self.state / "plan.json").exists())

    def test_plan_missing_loader_or_invalid_registry_fails_before_generation(self):
        loader = self.repo / "hosts/generated.nix"
        registry = self.repo / "hosts/generated/inventory.json"
        loader.unlink()
        with self.assertRaisesRegex(V.Error, "restore the tracked generated-host loader"):
            V.plan(self.args, self.repo, self.state, self.host)
        loader.write_text("{}")
        for value in ([], "invalid", None):
            V.write_json(registry, value)
            with self.subTest(value=value), self.assertRaisesRegex(V.Error, "must be a JSON object"):
                V.plan(self.args, self.repo, self.state, self.host)
            self.assertEqual(V.read_json(registry), value)
        self.assertFalse(self.host.exists())
        self.assertFalse((self.state / "plan.json").exists())

    def test_cancelled_target_does_not_publish_new_empty_registry(self):
        registry = self.repo / "hosts/generated/inventory.json"
        registry.unlink()
        self.args.target = None
        with patch.object(V, "choose_target", side_effect=V.Error("cancelled")), self.assertRaises(V.Error):
            V.plan(self.args, self.repo, self.state, self.host)
        self.assertFalse(registry.exists())
        self.assertFalse(self.host.exists())
        self.assertFalse((self.state / "plan.json").exists())

    def test_plan_only_records_paths_without_reading_or_generating_keys(self):
        self.key.unlink()
        # Neither a missing file nor invalid existing contents are checked by plan.
        invalid = Path(self.tmp.name) / "invalid.pub"
        invalid.write_text("not a public key")
        self.args.admin_key = [self.key, invalid]
        with patch.object(V, "ensure_admin_key") as ensure, patch.object(V, "run") as run, \
             patch("builtins.input") as prompt:
            self.create_plan()
        ensure.assert_not_called()
        run.assert_not_called()
        prompt.assert_not_called()
        self.assertFalse(self.key.exists())
        machine = V.read_json(self.host / "machine.json")
        self.assertEqual(machine["adminKeyFiles"], [str(self.key), str(invalid)])
        self.assertEqual(machine["adminKeys"], [])

    def test_plan_records_absolute_paths_without_resolving_symlinks(self):
        link = Path(self.tmp.name) / "dangling.pub"
        link.symlink_to("missing.pub")
        self.args.admin_key = [Path(os.path.relpath(link)), Path("~/.ssh/admin.pub")]
        self.create_plan()
        self.assertEqual(V.read_json(self.host / "machine.json")["adminKeyFiles"],
                         [str(link), str(Path.home() / ".ssh/admin.pub")])

    def test_default_dhcp_plan_and_ssh_port(self):
        V.write_json(self.state / "probe.json", {"connection": self.c, "facts": facts(True)})
        self.args = V.parser().parse_args(["plan", "vps-test", str(self.key), "--target", "target.test"])
        with patch.object(V, "choose_target") as choose:
            self.create_plan()
        choose.assert_not_called()
        machine = V.read_json(self.host / "machine.json")
        self.assertEqual(machine["network"]["mode"], "dhcp")
        self.assertEqual(machine["sshPort"], 22)
        self.assertEqual(machine["adminUser"], "admin")
        self.assertEqual(machine["chatId"], 0)
        self.assertNotIn("bot_token", json.dumps(machine))
        config = (self.host / "configuration.nix").read_text()
        self.assertIn("builtins.pathExists monitoring", config)
        self.assertIn("secretFile = if telegram then monitoring else null", config)

    def test_default_static_plan_from_existing_probe_without_policy_metadata(self):
        self.args = V.parser().parse_args(["plan", "vps-test", str(self.key), "--target", "target.test"])
        self.assertEqual(self.args.network, "auto")
        self.create_plan()
        machine = V.read_json(self.host / "machine.json")
        self.assertEqual(machine["network"]["mode"], "static")
        self.assertEqual(machine["network"]["config"]["address"], ["203.0.113.5/32"])
        self.assertEqual(V.read_json(self.state / "plan.json")["network"], machine["network"])

    def test_custom_admin_user_is_independent_of_probe_user(self):
        self.args = V.parser().parse_args(["plan", "vps-test", str(self.key),
            "--admin-user", "operator", "--network", "static", "--target", "target.test"])
        self.create_plan()
        machine = V.read_json(self.host / "machine.json")
        plan = V.read_json(self.state / "plan.json")
        self.assertEqual(machine["adminUser"], "operator")
        self.assertEqual(plan["connection"]["user"], "root")
        self.assertIn('adminUser = m.adminUser;', (self.host / "configuration.nix").read_text())
        self.assertIn('inputs.core.nixosModules.vps-base', (self.host / "configuration.nix").read_text())
        self.assertNotIn('../../../modules', (self.host / "configuration.nix").read_text())
        self.create_admin_private_key()
        final = V.production_connection(plan, machine, {"host_public_key": "ssh-ed25519 AAAAFINAL"}, self.state)
        self.assertEqual(final["user"], "operator")
        self.assertIn("operator@203.0.113.5", V.ssh_args(final))

    def test_invalid_admin_users_are_rejected_by_parser(self):
        for name in ("", "root", "nobody", "Admin", "-admin", "a" * 33, "a b", "a@host", "a*", "../admin", "a\n"):
            with self.subTest(name=name), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                V.parser().parse_args(["plan", "vps-test", str(self.key), "--admin-user", name])
        for name in ("admin", "operator", "ops-user", "ops_user", "_admin", "a" * 32):
            with self.subTest(name=name):
                self.assertEqual(V.admin_user(name), name)

    def test_invalid_admin_user_does_not_create_plan(self):
        self.args.admin_user = "root"
        with self.assertRaises(ValueError):
            V.plan(self.args, self.repo, self.state, self.host)
        self.assertFalse(self.host.exists())
        self.assertFalse((self.state / "plan.json").exists())
        self.assertEqual(V.read_json(self.repo / "hosts/generated/inventory.json"), {})

    def test_invalid_target_does_not_leave_partial_host(self):
        self.args.target = "https://invalid.test"
        with self.assertRaises(V.Error):
            V.plan(self.args, self.repo, self.state, self.host)
        self.assertFalse(self.host.exists())

    def test_target_selection_and_cancel_before_files(self):
        self.args.target = None
        with patch.object(V, "choose_target", side_effect=V.Error("cancelled")), self.assertRaises(V.Error):
            V.plan(self.args, self.repo, self.state, self.host)
        self.assertFalse(self.host.exists())
        self.assertEqual(V.read_json(self.repo / "hosts/generated/inventory.json"), {})
        with patch.object(V, "choose_target", return_value="selected.test") as choose:
            self.create_plan()
        choose.assert_called_once_with(self.c)
        self.assertEqual(V.read_json(self.host / "vless.json")["serverName"], "selected.test")

    def test_password_probe_connection_is_frozen_for_secrets_and_install(self):
        source = self.c | {"key": None}
        V.write_json(self.state / "probe.json", {"connection": source, "facts": facts()})
        self.create_plan()
        self.assertEqual(V.read_json(self.state / "plan.json")["connection"], source)
        self.assertEqual(V.read_json(self.host / "machine.json")["adminKeyFiles"], [str(self.key)])

    def test_invalid_plan_does_not_create_files_or_register(self):
        self.args.network = "dhcp"
        with self.assertRaises(V.Error):
            V.plan(self.args, self.repo, self.state, self.host)
        self.assertFalse(self.host.exists())
        self.assertEqual(V.read_json(self.repo / "hosts/generated/inventory.json"), {})

    def test_disk_change_refused_before_any_installer(self):
        self.create_plan()
        changed = facts()
        changed["disks"][0]["serial"] = "other-disk"
        with self.install_mocks(changed), patch.object(V.subprocess, "run") as run, self.assertRaises(V.Error):
            V.install(self.install_args(), self.repo, self.state, self.host)
        run.assert_not_called()

    def test_install_declined_before_network_or_installer(self):
        self.create_plan()
        args = self.install_args()
        args.force = False
        for answer in ("", "n", "NO"):
            with self.install_mocks(facts()), patch.object(V.sys.stdin, "isatty", return_value=True), \
                 patch("builtins.input", return_value=answer) as prompt, \
                 patch.object(V, "inventory") as inv, patch.object(V.subprocess, "run") as run, \
                 self.assertRaisesRegex(V.Error, "Cancelled"):
                V.install(args, self.repo, self.state, self.host)
            prompt.assert_called_once()
            self.assertIn("vps-test (203.0.113.5)", prompt.call_args.args[0])
            self.assertIn("/dev/vda", prompt.call_args.args[0])
            inv.assert_not_called()
            run.assert_not_called()

    def test_install_warning_acceptance_and_force(self):
        disk = V.disks_of(facts())
        for answer in ("y", "YES", " yes "):
            with patch.object(V.sys.stdin, "isatty", return_value=True), \
                 patch("builtins.input", return_value=answer) as prompt:
                V.confirm_install_warnings(False, "vps-test", "203.0.113.5", disk)
            prompt.assert_called_once()
        with patch.object(V.sys.stdin, "isatty", return_value=False), \
             patch("builtins.input") as prompt, contextlib.redirect_stderr(io.StringIO()) as output:
            V.confirm_install_warnings(True, "vps-test", "203.0.113.5", disk)
        prompt.assert_not_called()
        for warning in ("recovery/VNC", "TCP/22", "MITM", "destroys all data"):
            self.assertIn(warning, output.getvalue())

    def test_install_warnings_require_terminal_and_handle_eof(self):
        disk = V.disks_of(facts())
        with patch.object(V.sys.stdin, "isatty", return_value=False), \
             patch("builtins.input") as prompt, self.assertRaisesRegex(V.Error, "--force"):
            V.confirm_install_warnings(False, "vps-test", "203.0.113.5", disk)
        prompt.assert_not_called()
        with patch.object(V.sys.stdin, "isatty", return_value=True), \
             patch("builtins.input", side_effect=EOFError), self.assertRaisesRegex(V.Error, "Cancelled"):
            V.confirm_install_warnings(False, "vps-test", "203.0.113.5", disk)

    def test_install_parser_defaults_and_removed_flags(self):
        self.assertFalse(V.parser().parse_args(["install", "vps-test"]).force)
        self.assertTrue(V.parser().parse_args(["install", "vps-test", "--force"]).force)
        for flag in ("--recovery-confirmed", "--installer-port-open", "--accept-installer-ssh-risk",
                     "--installer-known-hosts", "--retry-after-recovery", "--from-installer"):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                V.parser().parse_args(["install", "vps-test", flag])

    def test_single_run_confirms_once_and_pins_known_source_identity(self):
        self.create_plan()
        args = self.install_args()
        args.force = False
        installer = facts() | {"os": "nixos", "installer": True, "machine_id": "installer-machine"}
        with self.install_mocks(facts()), patch.object(V, "inventory", side_effect=[facts(), installer]) as inv, \
             patch.object(V, "preflight") as preflight, \
             patch.object(V.sys.stdin, "isatty", return_value=True), \
             patch("builtins.input", side_effect=["yes"]) as prompt, \
             patch.object(V.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(V.install(args, self.repo, self.state, self.host), 0)
        prompt.assert_called_once()
        self.assertEqual([call.args[0][call.args[0].index("--phases") + 1] for call in run.call_args_list],
                         ["kexec", "disko,install,reboot"])
        for call in run.call_args_list:
            command = call.args[0]
            self.assertEqual(command[command.index("-i") + 1], self.c["key"])
            self.assertNotIn(str(self.key.with_suffix("")), command)
            self.assertNotIn("--copy-host-keys", command)
        source, target = [call.args[0] for call in inv.call_args_list]
        self.assertEqual({k: v for k, v in source.items() if k != "known_hosts"},
                         {k: v for k, v in self.c.items() if k != "known_hosts"})
        self.assertEqual((target["user"], target["port"], target["key"]), ("root", 22, self.c["key"]))
        known = Path(target["known_hosts"])
        self.assertEqual(known.read_text(), "203.0.113.5 " + TEST_SOURCE_KEY + "\n")
        self.assertNotIn(self.key.read_text().split()[1], known.read_text())
        self.assertEqual(known.stat().st_mode & 0o777, 0o600)
        self.assertEqual([call.args[0] for call in preflight.call_args_list], [source, target])
        self.assertTrue((self.state / "install-attempt.json").exists())

    def test_legacy_plan_accepts_ipv6_changes_but_still_rejects_ipv4_changes(self):
        self.create_plan()
        machine = V.read_json(self.host / "machine.json")
        machine["network"]["config"]["networkConfig"]["LinkLocalAddressing"] = "ipv6"
        V.write_json(self.host / "machine.json", machine)
        plan = V.read_json(self.state / "plan.json") | {"ignore_ipv6": False, "network": machine["network"]}
        V.write_json(self.state / "plan.json", plan)
        checked = V.read_json(self.state / "checked.json")
        checked["plan_digest"] = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
        V.write_json(self.state / "checked.json", checked)
        for changed_ipv4 in (True, False):
            fresh = facts()
            fresh["addresses"][0]["addr_info"].append(
                {"family": "inet6", "scope": "global", "local": "2001:db8::1"})
            if changed_ipv4:
                fresh["addresses"][0]["addr_info"][0]["local"] = "203.0.113.6"
            installer = facts() | {"os": "nixos", "installer": True}
            with self.subTest(changed_ipv4=changed_ipv4), self.install_mocks(fresh), \
                 patch.object(V, "inventory", side_effect=[fresh, installer]), \
                 patch.object(V.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
                if changed_ipv4:
                    with self.assertRaisesRegex(V.Error, "Source networking changed"):
                        V.install(self.install_args(), self.repo, self.state, self.host)
                    run.assert_not_called()
                else:
                    self.assertEqual(V.install(self.install_args(), self.repo, self.state, self.host), 0)
                    self.assertEqual(run.call_count, 2)

    def test_password_install_asks_once_and_uses_separate_installation_key(self):
        self.c["key"] = None
        V.write_json(self.state / "probe.json", {"connection": self.c, "facts": facts()})
        self.create_plan()
        installer = facts() | {"os": "nixos", "installer": True}
        keyed = self.c | {"key": "/private/vps-test-install"}
        with self.install_mocks(facts()), patch.object(V, "inventory", side_effect=[facts(), installer]) as inv, \
             patch.object(V, "prompt_ssh_password", return_value=b"password") as password, \
             patch.object(V, "authorize_installation_key", return_value=keyed) as authorize, \
             patch("builtins.input") as prompt, \
             patch.object(V.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(V.install(self.install_args(), self.repo, self.state, self.host), 0)
        prompt.assert_not_called()
        password.assert_called_once_with(self.c)
        authorize.assert_called_once_with(self.c, self.state, self.repo, b"password")
        self.assertEqual(inv.call_args_list[0].kwargs, {"password": b"password"})
        self.assertEqual(inv.call_args_list[1].kwargs, {})
        self.assertEqual(inv.call_args_list[1].args[0]["key"], keyed["key"])
        self.assertEqual(run.call_count, 2)

    def test_stale_receipt_refused_before_network(self):
        self.create_plan()
        with patch.object(V, "bootstrap_check"), patch.object(V, "source_digest", return_value="changed"), \
             patch.object(V, "inventory") as inv, patch.object(V.subprocess, "run") as run, self.assertRaises(V.Error):
            V.install(self.install_args(), self.repo, self.state, self.host)
        inv.assert_not_called(); run.assert_not_called()

    def test_invalid_saved_source_key_never_runs_kexec(self):
        invalid = facts() | {"ssh_host_public_key": "ssh-ed25519 AAAA invalid"}
        V.write_json(self.state / "probe.json", {"connection": self.c, "facts": invalid})
        self.create_plan()
        with self.install_mocks(invalid), patch("builtins.input") as prompt, \
             patch.object(V.subprocess, "run") as run, \
             self.assertRaisesRegex(V.Error, "Invalid saved source"):
            V.install(self.install_args(), self.repo, self.state, self.host)
        prompt.assert_not_called()
        run.assert_not_called()
        self.assertFalse((self.state / "install-attempt.json").exists())

    def test_installer_tools_failure_after_kexec_never_formats(self):
        self.create_plan()
        with self.install_mocks(facts()), \
             patch.object(V, "prepare_installer_tools", side_effect=[self.c, V.Error("Copy failed")]) as prepare, \
             patch.object(V.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run, \
             self.assertRaisesRegex(V.Error, "Copy failed"):
            V.install(self.install_args(), self.repo, self.state, self.host)
        self.assertEqual(prepare.call_count, 2)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][run.call_args.args[0].index("--phases") + 1], "kexec")
        self.assertFalse((self.state / "install-attempt.json").exists())

    def test_changed_installer_disk_never_formats(self):
        self.create_plan()
        changed = facts() | {"os": "nixos", "installer": True}
        changed["disks"][0]["serial"] = "other-disk"
        with self.install_mocks(facts()), patch.object(V, "inventory", side_effect=[facts(), changed]), \
             patch("builtins.input") as prompt, \
             patch.object(V.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run, \
             self.assertRaisesRegex(V.Error, "changed since plan"):
            V.install(self.install_args(), self.repo, self.state, self.host)
        prompt.assert_not_called()
        run.assert_called_once()

    def test_failed_kexec_never_waits_or_formats(self):
        self.create_plan()
        with self.install_mocks(facts()), patch("builtins.input") as prompt, \
             patch.object(V, "wait_for_installer") as wait, \
             patch.object(V.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)) as run, \
             self.assertRaisesRegex(V.Error, "Kexec stage failed"):
            V.install(self.install_args(), self.repo, self.state, self.host)
        prompt.assert_not_called()
        wait.assert_not_called()
        run.assert_called_once()
        self.assertFalse((self.state / "install-attempt.json").exists())

    def test_installer_wait_failure_after_kexec_never_formats(self):
        self.create_plan()
        with self.install_mocks(facts()), \
             patch.object(V, "wait_for_installer", side_effect=V.Error("SSH did not become ready")), \
             patch("builtins.input") as prompt, \
             patch.object(V.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run, \
             self.assertRaisesRegex(V.Error, "did not become ready"):
            V.install(self.install_args(), self.repo, self.state, self.host)
        prompt.assert_not_called()
        run.assert_called_once()
        self.assertFalse((self.state / "install-attempt.json").exists())

    def test_metadata_changed_during_kexec_never_formats(self):
        self.create_plan()
        installer = facts() | {"os": "nixos", "installer": True}
        def change_credentials(_c):
            credentials = V.read_json(self.state / "credentials.json")
            credentials["admin_recipient"] = "changed"
            V.write_json(self.state / "credentials.json", credentials)
        with self.install_mocks(facts()), patch.object(V, "inventory", side_effect=[facts(), installer]), \
             patch.object(V, "wait_for_installer", side_effect=change_credentials), \
             patch("builtins.input") as prompt, \
             patch.object(V.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run, \
             self.assertRaisesRegex(V.Error, "Checked inputs changed"):
            V.install(self.install_args(), self.repo, self.state, self.host)
        prompt.assert_not_called()
        run.assert_called_once()
        self.assertFalse((self.state / "install-attempt.json").exists())

    def test_resume_pins_source_key_without_prompt_or_reusing_saved_files(self):
        self.create_plan()
        old_pin = self.state / "installer-known-hosts-stale"
        old_pin.write_text("untrusted old key")
        installer = facts() | {"os": "nixos", "installer": True}
        with self.install_mocks(installer), patch.object(V.sys.stdin, "isatty", return_value=False), \
             patch("builtins.input") as prompt, \
             patch.object(V.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(V.install(self.install_args(), self.repo, self.state, self.host), 0)
        run.assert_called_once()
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[cmd.index("--phases") + 1], "disko,install,reboot")
        prompt.assert_not_called()
        self.assertEqual(old_pin.read_text(), "untrusted old key")
        pins = [path for path in self.state.glob("installer-known-hosts-*") if path != old_pin]
        self.assertEqual(len(pins), 1)
        self.assertEqual(pins[0].read_text(), "203.0.113.5 " + TEST_SOURCE_KEY + "\n")

    def test_custom_source_port_automatically_resumes_without_kexec(self):
        self.c.update(user="ubuntu", port=2222)
        V.write_json(self.state / "probe.json", {"connection": self.c, "facts": facts()})
        self.create_plan()
        installer = facts() | {"os": "nixos", "installer": True}
        unavailable = V.Error("SSH output withheld", details={
            "tool": "ssh", "exit_code": 255, "stderr": b"Connection refused"})
        with self.install_mocks(installer), \
             patch.object(V, "inventory", side_effect=[unavailable, installer]) as inv, \
             patch.object(V, "wait_for_installer") as wait, patch("builtins.input") as prompt, \
             patch.object(V.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(V.install(self.install_args(), self.repo, self.state, self.host), 0)
        prompt.assert_not_called()
        run.assert_called_once()
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[cmd.index("--phases") + 1], "disko,install,reboot")
        target = inv.call_args_list[1].args[0]
        self.assertEqual((target["user"], target["port"]), ("root", 22))
        wait.assert_called_once_with(target)

    def test_changed_disk_on_fallback_installer_never_formats(self):
        self.c.update(user="ubuntu", port=2222)
        V.write_json(self.state / "probe.json", {"connection": self.c, "facts": facts()})
        self.create_plan()
        installer = facts() | {"os": "nixos", "installer": True}
        installer["disks"][0]["serial"] = "other-disk"
        unavailable = V.Error("SSH output withheld", details={
            "tool": "ssh", "exit_code": 255, "stderr": b"Connection refused"})
        with self.install_mocks(installer), \
             patch.object(V, "inventory", side_effect=[unavailable, installer]), \
             patch.object(V.subprocess, "run") as run, \
             self.assertRaisesRegex(V.Error, "changed since plan"):
            V.install(self.install_args(), self.repo, self.state, self.host)
        run.assert_not_called()
        self.assertFalse((self.state / "install-attempt.json").exists())

    def test_password_resume_uses_retained_key_without_requesting_password(self):
        self.c["key"] = None
        V.write_json(self.state / "probe.json", {"connection": self.c, "facts": facts()})
        self.create_plan()
        installer = facts() | {"os": "nixos", "installer": True}
        V.installation_key_directory(self.state, self.repo).mkdir(mode=0o700)
        with self.install_mocks(installer), \
             patch.object(V, "installation_key", return_value=Path("/retained-install-key")) as retained, \
             patch.object(V, "prompt_ssh_password") as password, patch("builtins.input") as prompt, \
             patch.object(V.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(V.install(self.install_args(), self.repo, self.state, self.host), 0)
        retained.assert_called_once_with(self.state, self.repo)
        password.assert_not_called()
        prompt.assert_not_called()
        self.assertEqual(run.call_args.args[0][run.call_args.args[0].index("-i") + 1], "/retained-install-key")

    def test_installer_connection_validates_saved_key_without_terminal(self):
        with patch.object(V.sys.stdin, "isatty", return_value=False), patch("builtins.input") as prompt:
            c = V.installer_connection(self.c, self.state, facts()["ssh_host_public_key"])
        prompt.assert_not_called()
        self.assertEqual((c["user"], c["port"], c["key"]), ("root", 22, self.c["key"]))
        self.assertEqual(Path(c["known_hosts"]).read_text(), "203.0.113.5 " + TEST_SOURCE_KEY + "\n")
        for key in ("", "SHA256:fingerprint", "ssh-ed25519 !!!", "ssh-ed25519 AAAA", TEST_SOURCE_KEY + "\nextra"):
            with self.subTest(key=key), patch("builtins.input") as prompt, self.assertRaises(V.Error):
                V.installer_connection(self.c, self.state, key)
            prompt.assert_not_called()

    def test_installer_pinning_handles_source_nonroot_and_custom_port(self):
        source = self.c | {"user": "ubuntu", "port": 2222}
        with patch("builtins.input") as prompt:
            c = V.installer_connection(source, self.state, facts()["ssh_host_public_key"])
        prompt.assert_not_called()
        self.assertEqual((c["user"], c["port"]), ("root", 22))
        self.assertEqual(Path(c["known_hosts"]).read_text(), "203.0.113.5 " + TEST_SOURCE_KEY + "\n")
        self.assertIn("StrictHostKeyChecking=yes", V.ssh_args(c))
        self.assertIn("UserKnownHostsFile=" + c["known_hosts"], V.ssh_args(c))

    def test_installer_wait_keeps_strict_ssh_and_retries_connection_failure(self):
        c = V.connection(self.c["address"], "root", 22, self.c["key"], "/trusted-installer")
        with patch.object(V.time, "sleep") as sleep, \
             patch.object(V.subprocess, "run", side_effect=[subprocess.CompletedProcess([], 255),
                                                          subprocess.CompletedProcess([], 0)]) as run:
            V.wait_for_installer(c)
        self.assertEqual(run.call_count, 2)
        sleep.assert_called_once()
        cmd = run.call_args.args[0]
        self.assertIn("StrictHostKeyChecking=yes", cmd)
        self.assertIn("UserKnownHostsFile=/trusted-installer", cmd)
        self.assertEqual(cmd[-1], "true")

    def test_installer_wait_retries_timeout_and_rejects_remote_command_failure(self):
        with patch.object(V.time, "sleep"), patch.object(V.subprocess, "run", side_effect=[
                subprocess.TimeoutExpired("ssh", 15), subprocess.CompletedProcess([], 0)]) as run:
            V.wait_for_installer(self.c)
        self.assertEqual(run.call_count, 2)
        with patch.object(V.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)) as run, \
             self.assertRaisesRegex(V.Error, "readiness command failed"):
            V.wait_for_installer(self.c)
        run.assert_called_once()

    def test_installer_wait_is_bounded_and_fails_closed(self):
        with patch.object(V.time, "monotonic", side_effect=[0, 0, 0, 1, 2]), \
             patch.object(V.time, "sleep"), \
             patch.object(V.subprocess, "run", return_value=subprocess.CompletedProcess([], 255)) as run, \
             self.assertRaisesRegex(V.Error, "did not become ready"):
            V.wait_for_installer(self.c, timeout=2)
        run.assert_called_once()

    def test_installed_nixos_is_not_reformatted(self):
        self.create_plan()
        installed = facts() | {"os": "nixos", "installer": False}
        with self.install_mocks(installed), patch.object(V.subprocess, "run") as run, \
             self.assertRaisesRegex(V.Error, "installed OS is not reformatted"):
            V.install(self.install_args(), self.repo, self.state, self.host)
        run.assert_not_called()
        self.assertFalse((self.state / "install-attempt.json").exists())

    def test_resumed_install_confirms_once_and_records_attempt_without_overwrite_prompt(self):
        self.create_plan()
        args = self.install_args()
        args.force = False
        fresh = facts() | {"os": "nixos", "installer": True, "machine_id": "installer-machine"}
        with self.install_mocks(fresh), patch.object(V.sys.stdin, "isatty", return_value=True), \
             patch("builtins.input", side_effect=["yes"]) as prompt, \
             patch.object(V.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(V.install(args, self.repo, self.state, self.host), 0)
        prompt.assert_called_once()
        run.assert_called_once()
        self.assertIn("--extra-files", run.call_args.args[0])
        self.assertTrue((self.state / "install-attempt.json").exists())

    def test_repeat_install_warns_and_requires_one_yes_no_before_network(self):
        self.create_plan()
        args = self.install_args()
        args.force = False
        fresh = facts() | {"os": "nixos", "installer": True}
        marker = self.state / "install-attempt.json"
        previous = {"identity": V.identity(fresh), "time": "2026-01-01T00:00:00Z"}
        V.write_json(marker, previous)
        with self.install_mocks(fresh), patch.object(V.sys.stdin, "isatty", return_value=True), \
             patch("builtins.input", return_value="n") as prompt, patch.object(V, "inventory") as inv, \
             patch.object(V.subprocess, "run") as run, contextlib.redirect_stdout(io.StringIO()) as output, \
             self.assertRaisesRegex(V.Error, "Cancelled"):
            V.install(args, self.repo, self.state, self.host)
        prompt.assert_called_once()
        inv.assert_not_called()
        run.assert_not_called()
        self.assertIn(previous["time"], output.getvalue())
        self.assertIn("not a resume", output.getvalue())
        self.assertIn("recovery", output.getvalue())
        self.assertEqual(V.read_json(marker), previous)
        self.overwrite.reset_mock()
        with self.install_mocks(fresh), patch.object(V.sys.stdin, "isatty", return_value=True), \
             patch("builtins.input", side_effect=["yes"]) as prompt, \
             patch.object(V.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(V.install(args, self.repo, self.state, self.host), 0)
        prompt.assert_called_once()
        self.overwrite.assert_not_called()
        run.assert_called_once()
        self.assertEqual(V.read_json(marker)["identity"], V.identity(fresh))
        self.assertNotEqual(V.read_json(marker)["time"], previous["time"])

    def test_failed_install_keeps_marker_without_automatic_retry(self):
        self.create_plan()
        fresh = facts() | {"os": "nixos", "installer": True}
        args = self.install_args()
        marker = self.state / "install-attempt.json"
        with self.install_mocks(fresh), patch("builtins.input") as prompt, \
             patch.object(V.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)) as run, \
             self.assertRaisesRegex(V.Error, "Installation failed"):
            V.install(args, self.repo, self.state, self.host)
        prompt.assert_not_called()
        run.assert_called_once()
        previous = V.read_json(marker)
        args.force = False
        with self.install_mocks(fresh), patch.object(V.sys.stdin, "isatty", return_value=True), \
             patch("builtins.input", return_value="n") as prompt, \
             patch.object(V.subprocess, "run") as run, \
             self.assertRaisesRegex(V.Error, "Cancelled"):
            V.install(args, self.repo, self.state, self.host)
        prompt.assert_called_once()
        run.assert_not_called()
        self.assertEqual(V.read_json(marker), previous)

    def test_force_installs_without_any_input_and_still_warns_about_repeat(self):
        self.create_plan()
        fresh = facts() | {"os": "nixos", "installer": True}
        marker = self.state / "install-attempt.json"
        for repeated in (False, True):
            with self.subTest(repeated=repeated):
                if repeated:
                    V.write_json(marker, {"identity": V.identity(fresh), "time": "2026-01-01T00:00:00Z"})
                with self.install_mocks(facts()), patch.object(V, "inventory", side_effect=[facts(), fresh]), \
                     patch.object(V.sys.stdin, "isatty", return_value=False), \
                     patch("builtins.input") as prompt, \
                     patch.object(V.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run, \
                     contextlib.redirect_stdout(io.StringIO()) as output, \
                     contextlib.redirect_stderr(io.StringIO()) as warnings:
                    self.assertEqual(V.install(self.install_args(), self.repo, self.state, self.host), 0)
                prompt.assert_not_called()
                self.assertEqual(run.call_count, 2)
                self.assertNotIn("Type exactly", output.getvalue())
                self.assertNotIn("Paste", output.getvalue())
                self.assertIn("destroys all data", warnings.getvalue())
                self.assertEqual(warnings.getvalue().count("automatically accepted (--force)"), 1)
                if repeated:
                    self.assertIn("WARNING: This repeats installation", output.getvalue())
                self.assertTrue(marker.exists())

    def test_invalid_attempt_marker_never_runs_inventory_or_installer(self):
        self.create_plan()
        marker = self.state / "install-attempt.json"
        fresh = facts() | {"os": "nixos", "installer": True}
        for value in ([], None, "invalid"):
            V.write_json(marker, value)
            with self.install_mocks(fresh), patch("builtins.input") as prompt, \
                 patch.object(V, "inventory") as inv, patch.object(V.subprocess, "run") as run, \
                 self.assertRaisesRegex(V.Error, "Invalid installation attempt marker"):
                V.install(self.install_args(), self.repo, self.state, self.host)
            prompt.assert_not_called()
            inv.assert_not_called()
            run.assert_not_called()
            self.assertEqual(V.read_json(marker), value)

    def test_plan_edit_cannot_change_the_disk_behind_erase_confirmation(self):
        self.create_plan()
        machine = V.read_json(self.host / "machine.json")
        machine["disk"]["name"] = "/dev/vdb"
        V.write_json(self.host / "machine.json", machine)
        with self.install_mocks(facts()), patch.object(V.subprocess, "run") as run, self.assertRaises(V.Error):
            V.install(self.install_args(), self.repo, self.state, self.host)
        run.assert_not_called()

    def test_metadata_edit_invalidates_check_receipt(self):
        self.create_plan()
        plan = V.read_json(self.state / "plan.json")
        plan["connection"]["address"] = "203.0.113.99"
        V.write_json(self.state / "plan.json", plan)
        with self.install_mocks(facts()), patch.object(V.subprocess, "run") as run, self.assertRaises(V.Error):
            V.install(self.install_args(), self.repo, self.state, self.host)
        run.assert_not_called()

    def test_local_check_accepts_unrelated_source_key_without_private_admin_key(self):
        self.create_plan()
        machine = V.read_json(self.host / "machine.json")
        machine["adminKeys"] = [self.key.read_text()]
        V.write_json(self.host / "machine.json", machine)
        generator = {"material": "{}", "warp": "{}"}
        renderer = Mock()
        renderer.render.return_value = {"test-secret": "DO-NOT-PRINT"}
        paths, calls = [], []
        def evaluate(_repo, _name, attribute, raw=False):
            return {"system.build.toplevel.outPath": "/nix/store/system",
                    "system.build.diskoScript.outPath": "/nix/store/disko",
                    "disko.devices.disk.main.device": "/dev/vda",
                    "users.users.admin.openssh.authorizedKeys.keys": machine["adminKeys"],
                    "services.openssh.settings": {"PermitRootLogin": "no", "PasswordAuthentication": False, "KbdInteractiveAuthentication": False,
                                                 "AllowUsers": ["admin"]},
                    "services.openssh.ports": [3333],
                    "services.resolved.enable": True,
                    "services.resolved.settings.Resolve": {"LLMNR": False, "MulticastDNS": False},
                    "networking.firewall.enable": True,
                    "networking.firewall.allowedTCPPorts": [3333, 443],
                    "networking.firewall.allowedUDPPorts": [],
                    "services.xray.declarative.settings": {"port": 443, "clients": ["default"]},
                    "services.xray.declarative.statsPort": 10085,
                    "services.xray.package.outPath": "/nix/store/xray"}[attribute]
        def run(command, *_args, **_kwargs):
            calls.append(command)
            if command[:2] == ["nix", "build"]:
                return b"/nix/store/disko\n/nix/store/system\n"  # intentionally reversed
            if command[:2] == ["nix", "eval"]:
                return b'["main"]'
            self.assertEqual(command[0], "/nix/store/xray/bin/xray")
            path = Path(command[-1]); paths.append(path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            return b""
        with patch.object(V, "source_digest", return_value="new-digest"), \
             patch.object(V, "bootstrap_check") as bootstrap, \
             patch.object(V, "run", side_effect=run), patch.object(V, "evaluate", side_effect=evaluate), \
             patch.object(V, "payloads", return_value=[generator, {"bot_token": "test"}]), \
             patch.object(V, "load", return_value=renderer), contextlib.redirect_stdout(io.StringIO()):
            V.check(argparse.Namespace(name="vps-test", vm_test=False), self.repo, self.state, self.host)
        bootstrap.assert_called_once_with(V.read_json(self.state / "credentials.json"), self.repo)
        checked = V.read_json(self.state / "checked.json")
        self.assertEqual(checked["system"], "/nix/store/system")
        self.assertEqual(checked["disko"], "/nix/store/disko")
        self.assertTrue(paths and all(not p.exists() for p in paths))
        self.assertTrue(all(cmd[0] not in ("ssh", "ssh-keygen") for cmd in calls))
        self.assertNotEqual(self.c["key"], str(self.key.with_suffix("")))
        self.assertFalse(self.key.with_suffix("").exists())
        def wrong_admin(repo, name, attribute, raw=False):
            value = evaluate(repo, name, attribute, raw)
            return value | {"AllowUsers": ["operator"]} if attribute == "services.openssh.settings" else value
        with patch.object(V, "run", side_effect=run), patch.object(V, "evaluate", side_effect=wrong_admin), \
             patch.object(V, "bootstrap_check"), \
             patch.object(V, "source_digest", return_value="new-digest"), \
             self.assertRaisesRegex(V.Error, "planned key-only administrator"):
            V.check(argparse.Namespace(name="vps-test", vm_test=False), self.repo, self.state, self.host)

        for policy in ({}, {"LLMNR": True, "MulticastDNS": False},
                       {"LLMNR": "resolve", "MulticastDNS": False},
                       {"LLMNR": False, "MulticastDNS": True}):
            def wrong_dns(repo, name, attribute, raw=False):
                if attribute == "services.resolved.settings.Resolve":
                    return policy
                return evaluate(repo, name, attribute, raw)
            with self.subTest(policy=policy), patch.object(V, "run", side_effect=run), \
                 patch.object(V, "bootstrap_check"), patch.object(V, "evaluate", side_effect=wrong_dns), \
                 patch.object(V, "source_digest", return_value="new-digest"), \
                 self.assertRaisesRegex(V.Error, "disable LLMNR and multicast DNS"):
                V.check(argparse.Namespace(name="vps-test", vm_test=False), self.repo, self.state, self.host)

        def wrong_keys(repo, name, attribute, raw=False):
            if attribute == "users.users.admin.openssh.authorizedKeys.keys":
                return ["ssh-ed25519 UNPLANNED"]
            return evaluate(repo, name, attribute, raw)
        for keys in (machine["adminKeys"], []):
            machine["adminKeys"] = keys
            V.write_json(self.host / "machine.json", machine)
            with self.subTest(keys=keys), patch.object(V, "run", side_effect=run), \
                 patch.object(V, "bootstrap_check"), patch.object(V, "evaluate", side_effect=wrong_keys), \
                 patch.object(V, "source_digest", return_value="new-digest"), \
                 self.assertRaisesRegex(V.Error, "Compiled administrator SSH keys"):
                V.check(argparse.Namespace(name="vps-test", vm_test=False), self.repo, self.state, self.host)

    def test_local_check_rejects_unsafe_bootstrap_before_building_or_writing_receipt(self):
        self.create_plan()
        previous = (self.state / "checked.json").read_bytes()
        with patch.object(V, "bootstrap_check", side_effect=V.Error("unsafe bootstrap")), \
             patch.object(V, "run") as run, self.assertRaisesRegex(V.Error, "unsafe bootstrap"):
            V.check(argparse.Namespace(name="vps-test", vm_test=False), self.repo, self.state, self.host)
        run.assert_not_called()
        self.assertEqual((self.state / "checked.json").read_bytes(), previous)

    def test_initial_encryption_does_not_require_existing_creation_rules(self):
        path = self.repo / "hosts/generated/new-host/secrets.yaml"
        recipients = [TEST_ADMIN, "age1testhost"]
        data = ("recipient: " + TEST_ADMIN + "\nrecipient: age1testhost\n").encode()
        with patch.object(V, "run", return_value=data) as run:
            V.encrypt({"material": "synthetic"}, path, recipients)
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["sops", "--config", "/dev/null", "encrypt"])
        self.assertEqual(command[command.index("--age") + 1], ",".join(recipients))
        self.assertEqual(path.read_bytes(), data)
        with patch.object(V, "run", return_value=data), self.assertRaises(FileExistsError):
            V.encrypt({"material": "synthetic"}, path, recipients)

    def test_secrets_interface_defaults_and_removed_terms_flag(self):
        args = V.parser().parse_args(["secrets", "vps-test"])
        self.assertIsNone(args.bootstrap_dir)
        self.assertIsNone(args.admin_recipient)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            V.parser().parse_args(["secrets", "vps-test", "--accept-warp-tos"])

    def test_default_bootstrap_and_generated_age_identity_are_separate(self):
        self.create_plan()
        args = V.parser().parse_args(["secrets", "vps-test"])
        home = Path(self.tmp.name) / "home"
        identity = home / ".config/sops/age/vps-test.txt"
        encrypted = []
        with self.provision_mocks(encrypted), patch.object(Path, "home", return_value=home), \
             patch.object(V, "prepare_admin_age", return_value=(TEST_ADMIN, str(identity))) as age, \
             patch.object(V.getpass, "getpass", return_value=""), contextlib.redirect_stdout(io.StringIO()):
            V.provision(args, self.repo, self.state, self.host)
        bootstrap = home / ".ssh/vps-test"
        age.assert_called_once_with(args, self.repo, bootstrap)
        credentials = V.read_json(self.state / "credentials.json")
        self.assertEqual(credentials["bootstrap_dir"], str(bootstrap))
        self.assertEqual(credentials["admin_identity"], str(identity))
        self.assertEqual(credentials["admin_recipient"], TEST_ADMIN)
        self.assertEqual(credentials["warp_registration_location"], "source-VPS")
        self.assertEqual(encrypted[0][2], [TEST_ADMIN, "age1testhost"])
        self.assertFalse(bootstrap in identity.parents)
        self.assertEqual(bootstrap.stat().st_mode & 0o777, 0o700)
        for relative in ("etc", "etc/ssh"):
            self.assertEqual((bootstrap / relative).stat().st_mode & 0o777, 0o755)

    def test_declined_age_creation_stops_before_registration(self):
        self.create_plan()
        args = V.parser().parse_args(["secrets", "vps-test", "--bootstrap-dir", str(Path(self.tmp.name) / "bootstrap")])
        with patch.object(V.getpass, "getpass", return_value=""), \
             patch.object(V, "prepare_admin_keys", return_value=[self.key.read_text()]), \
             patch.object(V.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value=""), \
             patch.dict(os.environ, {"XDG_CONFIG_HOME": str(Path(self.tmp.name) / "config")}), \
             patch.object(V, "run") as run, patch.object(V, "load") as load, self.assertRaises(V.Error):
            V.provision(args, self.repo, self.state, self.host)
        run.assert_not_called()
        load.assert_not_called()
        self.assertFalse(args.bootstrap_dir.exists())

    def test_registration_not_started_with_existing_credentials(self):
        self.create_plan()
        args = argparse.Namespace(name="vps-test", bootstrap_dir=Path(self.tmp.name) / "private-bootstrap",
                                  admin_recipient=TEST_ADMIN)
        resources = [("host_ciphertext", self.host / "secrets.yaml"),
                     ("monitoring_ciphertext", self.host / "monitoring.yaml"),
                     ("host_ciphertext", self.repo / "secrets/hosts/vps-test.yaml"),
                     ("monitoring_ciphertext", self.repo / "secrets/hosts/vps-test-monitoring.yaml"),
                     ("bootstrap_dir", args.bootstrap_dir)]
        before = (self.host / "machine.json").read_text()
        for kind, path in resources:
            path.parent.mkdir(parents=True, exist_ok=True)
            if kind == "bootstrap_dir":
                path.mkdir()
            else:
                path.write_text("SECRET-CONTENT")
            with self.subTest(kind=kind), patch.object(V, "run") as run, patch.object(V, "load") as load, \
                 patch.object(V.sys.stdin, "isatty", return_value=False), \
                 patch.object(V.getpass, "getpass") as prompt, self.assertRaises(V.Error) as error:
                V.provision(args, self.repo, self.state, self.host)
            run.assert_not_called()
            load.assert_not_called()
            prompt.assert_not_called()
            message = str(error.exception)
            self.assertIn(kind + ": " + str(path), message)
            self.assertIn("nothing generated or registered by this invocation", message)
            self.assertNotIn("SECRET-CONTENT", message)
            report = V.error_report(error.exception, "secrets", "vps-test")
            self.assertEqual(report["exceptions"][0]["existing_inputs"], [kind])
            self.assertNotIn(str(path), json.dumps(report))
            self.assertEqual((self.host / "machine.json").read_text(), before)
            if kind == "bootstrap_dir":
                path.rmdir()
            else:
                self.assertEqual(path.read_text(), "SECRET-CONTENT")
                path.unlink()

    def test_existing_bootstrap_backup_requires_consent_without_force(self):
        self.create_plan()
        bootstrap = Path(self.tmp.name) / "private-bootstrap"
        bootstrap.mkdir()
        old_key = bootstrap / "old-key"
        old_key.write_text("PRIVATE-OLD-KEY")
        before = (self.host / "machine.json").read_text()
        for interactive, answer in ((False, "y"), (True, ""), (True, "n"), (True, EOFError())):
            args = V.parser().parse_args(["secrets", "vps-test", "--bootstrap-dir", str(bootstrap)])
            with self.subTest(interactive=interactive, answer=answer), \
                 patch.object(V.sys.stdin, "isatty", return_value=interactive), \
                 patch("builtins.input", side_effect=answer if isinstance(answer, Exception) else None,
                       return_value=answer) as consent, patch.object(V, "run") as run, \
                 patch.object(V, "load") as load, patch.object(V.getpass, "getpass") as token, \
                 self.assertRaisesRegex(V.Error, "credential preflight"):
                V.provision(args, self.repo, self.state, self.host)
            self.assertEqual(consent.call_count, int(interactive))
            run.assert_not_called()
            load.assert_not_called()
            token.assert_not_called()
            self.assertEqual(old_key.read_text(), "PRIVATE-OLD-KEY")
            self.assertEqual(list(bootstrap.parent.glob(bootstrap.name + ".backup-*")), [])
            self.assertEqual((self.host / "machine.json").read_text(), before)

    def test_force_backs_up_existing_bootstrap_without_interactive_consent(self):
        self.create_plan()
        bootstrap = Path(self.tmp.name) / "private-bootstrap"
        bootstrap.mkdir()
        (bootstrap / "old-key").write_text("PRIVATE-OLD-KEY")
        args = V.parser().parse_args(["secrets", "vps-test", "--bootstrap-dir", str(bootstrap),
                                     "--admin-recipient", TEST_ADMIN, "--force"])
        output = io.StringIO()
        with self.provision_mocks([]), patch.object(V.sys.stdin, "isatty", return_value=False), \
             patch("builtins.input") as consent, patch.object(V.getpass, "getpass", return_value=""), \
             contextlib.redirect_stdout(output):
            V.provision(args, self.repo, self.state, self.host)
        consent.assert_not_called()
        self.assertIn("automatically accepted (--force)", output.getvalue())
        backups = list(bootstrap.parent.glob(bootstrap.name + ".backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / bootstrap.name / "old-key").read_text(), "PRIVATE-OLD-KEY")
        self.assertTrue((bootstrap / "etc/ssh/ssh_host_ed25519_key.pub").exists())
        self.assertFalse((bootstrap / "old-key").exists())
        self.assertEqual(V.read_json(self.state / "credentials.json")["bootstrap_dir"], str(bootstrap))

    def test_existing_bootstrap_backup_preserves_files_and_uses_original_path(self):
        self.create_plan()
        bootstrap = Path(self.tmp.name) / "private-bootstrap"
        bootstrap.mkdir()
        (bootstrap / "etc/ssh").mkdir(parents=True)
        (bootstrap / "etc/ssh/ssh_host_ed25519_key").write_text("PRIVATE-OLD-KEY")
        (bootstrap / "extra-file").write_text("OLD-DATA")
        args = V.parser().parse_args(["secrets", "vps-test", "--bootstrap-dir", str(bootstrap),
                                     "--admin-recipient", TEST_ADMIN])
        output = io.StringIO()
        with self.provision_mocks([]), patch.object(V.sys.stdin, "isatty", return_value=True), \
             patch("builtins.input", return_value="y") as consent, \
             patch.object(V.getpass, "getpass", return_value=""), contextlib.redirect_stdout(output):
            V.provision(args, self.repo, self.state, self.host)
        consent.assert_called_once()
        backups = list(bootstrap.parent.glob(bootstrap.name + ".backup-*"))
        self.assertEqual(len(backups), 1)
        backup = backups[0] / bootstrap.name
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o700)
        self.assertEqual((backup / "etc/ssh/ssh_host_ed25519_key").read_text(), "PRIVATE-OLD-KEY")
        self.assertEqual((backup / "extra-file").read_text(), "OLD-DATA")
        self.assertIn(str(backup), output.getvalue())
        self.assertNotIn("PRIVATE-OLD-KEY", output.getvalue())
        self.assertNotIn("OLD-DATA", output.getvalue())
        self.assertFalse((bootstrap / "extra-file").exists())
        self.assertTrue((bootstrap / "etc/ssh/ssh_host_ed25519_key.pub").is_file())
        self.assertEqual(V.read_json(self.state / "credentials.json")["bootstrap_dir"], str(bootstrap))
        self.assertEqual(self.key.read_text(), V.read_json(self.host / "machine.json")["adminKeys"][0])

    def test_existing_ciphertext_or_non_directory_bootstrap_never_offers_backup(self):
        self.create_plan()
        bootstrap = Path(self.tmp.name) / "private-bootstrap"
        args = V.parser().parse_args(["secrets", "vps-test", "--bootstrap-dir", str(bootstrap), "--force"])
        bootstrap.mkdir()
        ciphertexts = (self.host / "secrets.yaml", self.host / "monitoring.yaml",
                       self.repo / "secrets/hosts/vps-test.yaml",
                       self.repo / "secrets/hosts/vps-test-monitoring.yaml")
        for ciphertext in ciphertexts:
            ciphertext.parent.mkdir(parents=True, exist_ok=True)
            ciphertext.write_text("CIPHERTEXT")
            with self.subTest(ciphertext=ciphertext), patch.object(V.sys.stdin, "isatty", return_value=True), \
                 patch("builtins.input", return_value="y") as consent, \
                 self.assertRaisesRegex(V.Error, "credential preflight"):
                V.provision(args, self.repo, self.state, self.host)
            consent.assert_not_called()
            self.assertTrue(bootstrap.is_dir())
            self.assertEqual(ciphertext.read_text(), "CIPHERTEXT")
            ciphertext.unlink()
        bootstrap.rmdir()
        for kind in ("file", "symlink"):
            if kind == "file":
                bootstrap.write_text("OLD-DATA")
            else:
                bootstrap.symlink_to(self.tmp.name, target_is_directory=True)
            with self.subTest(kind=kind), patch.object(V.sys.stdin, "isatty", return_value=True), \
                 patch("builtins.input", return_value="y") as consent, \
                 self.assertRaisesRegex(V.Error, "credential preflight"):
                V.provision(args, self.repo, self.state, self.host)
            consent.assert_not_called()
            bootstrap.unlink()

    def test_bootstrap_backup_failure_stops_without_registration(self):
        self.create_plan()
        bootstrap = Path(self.tmp.name) / "private-bootstrap"
        bootstrap.mkdir()
        (bootstrap / "old-key").write_text("PRIVATE-OLD-KEY")
        args = V.parser().parse_args(["secrets", "vps-test", "--bootstrap-dir", str(bootstrap)])
        with patch.object(V.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="y"), \
             patch.object(Path, "rename", side_effect=OSError("PRIVATE-ERROR")), \
             patch.object(V, "run") as run, patch.object(V, "load") as load, \
             patch.object(V.getpass, "getpass") as token, self.assertRaises(V.Error) as error:
            V.provision(args, self.repo, self.state, self.host)
        self.assertIn("secrets / bootstrap backup", str(error.exception))
        self.assertNotIn("PRIVATE-ERROR", str(error.exception))
        self.assertEqual((bootstrap / "old-key").read_text(), "PRIVATE-OLD-KEY")
        self.assertEqual(list(bootstrap.parent.glob(bootstrap.name + ".backup-*")), [])
        run.assert_not_called()
        load.assert_not_called()
        token.assert_not_called()

    def test_bootstrap_backup_survives_later_provisioning_failure(self):
        self.create_plan()
        bootstrap = Path(self.tmp.name) / "private-bootstrap"
        bootstrap.mkdir()
        (bootstrap / "old-key").write_text("PRIVATE-OLD-KEY")
        args = V.parser().parse_args(["secrets", "vps-test", "--bootstrap-dir", str(bootstrap),
                                     "--admin-recipient", TEST_ADMIN])
        with self.provision_mocks([], warp_error="registration failed"), \
             patch.object(V.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="yes"), \
             patch.object(V.getpass, "getpass", return_value=""), contextlib.redirect_stdout(io.StringIO()), \
             self.assertRaisesRegex(V.Error, "WARP registration/profile"):
            V.provision(args, self.repo, self.state, self.host)
        backups = list(bootstrap.parent.glob(bootstrap.name + ".backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / bootstrap.name / "old-key").read_text(), "PRIVATE-OLD-KEY")
        self.assertTrue((bootstrap / "etc/ssh/ssh_host_ed25519_key.pub").exists())

    def test_dangling_credential_and_bootstrap_links_block_provisioning(self):
        self.create_plan()
        args = argparse.Namespace(name="vps-test", bootstrap_dir=Path(self.tmp.name) / "private-bootstrap",
                                  admin_recipient=TEST_ADMIN)
        for path in (self.host / "secrets.yaml", self.host / "monitoring.yaml",
                     self.repo / "secrets/hosts/vps-test.yaml", args.bootstrap_dir):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(Path(self.tmp.name) / "nonexistent")
            with self.subTest(path=path), patch.object(V, "run") as run, \
                 patch.object(V.getpass, "getpass") as prompt, self.assertRaises(V.Error) as error:
                V.provision(args, self.repo, self.state, self.host)
            self.assertIn(str(path), str(error.exception))
            prompt.assert_not_called()
            run.assert_not_called()
            self.assertTrue(path.is_symlink())
            path.unlink()

    def test_warp_failure_reports_stage_keeps_keys_and_never_retries(self):
        self.create_plan()
        (self.state / "credentials.json").unlink()
        args = argparse.Namespace(name="vps-test", bootstrap_dir=Path(self.tmp.name) / "bootstrap",
                                  admin_recipient=TEST_ADMIN)
        encrypted = []
        with self.provision_mocks(encrypted, warp_error="wgcf register failed (exit 1); output withheld"), \
             patch.object(V.getpass, "getpass", return_value=""), self.assertRaises(V.Error) as error:
            V.provision(args, self.repo, self.state, self.host)
        self.assertIn("secrets / WARP registration/profile: wgcf register failed (exit 1)", str(error.exception))
        self.assertIn("no implicit retry", str(error.exception))
        self.assertEqual(V.read_json(self.host / "machine.json")["adminKeys"], [self.key.read_text()])
        self.assertTrue(args.bootstrap_dir.exists())
        self.assertEqual(encrypted, [])
        self.assertFalse((self.state / "credentials.json").exists())
        # Repeating blindly must stop before touching tools or registering again.
        with patch.object(V, "load") as load, patch.object(V, "run") as run, \
             patch.object(V.sys.stdin, "isatty", return_value=False), self.assertRaises(V.Error):
            V.provision(args, self.repo, self.state, self.host)
        load.assert_not_called()
        run.assert_not_called()

    def provision_mocks(self, encrypted, warp_error=None):
        stack = contextlib.ExitStack()
        generator = Mock()
        generator.keypair.return_value = ("PRIVATE-REALITY", "PUBLIC-REALITY")
        generator.new_client.return_value = {"uuid": "test-uuid"}
        registration = Mock(return_value=({"test": "warp"}, "PRIVATE-WARP-ACCOUNT"))
        if warp_error is not None:
            registration.side_effect = V.Error(warp_error)
        # The source connection is used exactly once, without local fallback.
        stack.callback(lambda: registration.assert_called_once_with(self.c))
        stack.enter_context(patch.object(V, "register_warp_on_vps", registration))
        renderer = Mock()
        def load(path, name):
            return {"vps_generator": generator, "vps_renderer": renderer}[name]
        def run(argv, *_args, **_kwargs):
            if argv[0] == "ssh-keygen" and "-f" in argv:
                Path(argv[-1]).with_suffix(".pub").write_text("ssh-ed25519 AAAAHOST")
                return b""
            if argv[0] == "ssh-to-age":
                return b"age1testhost"
            return b"fingerprint"
        def encrypt(payload, path, recipients):
            encrypted.append((payload, path, recipients))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("test ciphertext")
        stack.enter_context(patch.object(V, "load", side_effect=load))
        stack.enter_context(patch.object(V, "run", side_effect=run))
        stack.enter_context(patch.object(V, "encrypt", side_effect=encrypt))
        return stack

    def test_secrets_generates_missing_admin_pair_and_saves_public_contents(self):
        self.create_plan()
        expected = self.key.read_text()
        self.key.unlink()
        args = argparse.Namespace(name="vps-test", bootstrap_dir=Path(self.tmp.name) / "bootstrap",
                                  admin_recipient=TEST_ADMIN)
        def generate(command):
            key = Path(command[-1])
            key.write_text("test private key")
            key.with_suffix(".pub").write_text(expected)
            return subprocess.CompletedProcess(command, 0)
        with self.provision_mocks([]), patch.object(V.sys.stdin, "isatty", return_value=True), \
             patch("builtins.input", return_value="y") as prompt, \
             patch.object(V.subprocess, "run", side_effect=generate) as generate_pair, \
             patch.object(V.getpass, "getpass", return_value=""), contextlib.redirect_stdout(io.StringIO()):
            V.provision(args, self.repo, self.state, self.host)
        prompt.assert_called_once()
        generate_pair.assert_called_once()
        machine = V.read_json(self.host / "machine.json")
        self.assertEqual(machine["adminKeyFiles"], [str(self.key)])
        self.assertEqual(machine["adminKeys"], [expected])
        self.assertNotIn("test private key", json.dumps(machine))
        self.assertTrue(self.key.with_suffix("").exists())

    def test_secrets_decline_or_noninteractive_missing_key_keeps_plan_and_stops_registration(self):
        self.create_plan()
        self.key.unlink()
        args = argparse.Namespace(name="vps-test", bootstrap_dir=Path(self.tmp.name) / "bootstrap",
                                  admin_recipient=TEST_ADMIN)
        before = (self.host / "machine.json").read_text()
        for interactive in (True, False):
            with self.subTest(interactive=interactive), patch.object(V.sys.stdin, "isatty", return_value=interactive), \
                 patch("builtins.input", return_value=""), patch.object(V.getpass, "getpass", return_value=""), \
                 patch.object(V, "load") as load, patch.object(V, "run") as run, self.assertRaises(V.Error):
                V.provision(args, self.repo, self.state, self.host)
            run.assert_not_called()
            load.assert_not_called()
            self.assertEqual((self.host / "machine.json").read_text(), before)
            self.assertFalse(args.bootstrap_dir.exists())

    def test_secrets_validates_public_key_contents_before_registration(self):
        self.create_plan()
        args = argparse.Namespace(name="vps-test", bootstrap_dir=Path(self.tmp.name) / "bootstrap",
                                  admin_recipient=TEST_ADMIN)
        for contents in ("not a public key", "ssh-ed25519 invalid-base64"):
            self.key.write_text(contents)
            with self.subTest(contents=contents), patch.object(V.getpass, "getpass", return_value=""), \
                 patch.object(V, "load") as load, patch.object(V, "run") as run, self.assertRaises(V.Error):
                V.provision(args, self.repo, self.state, self.host)
            load.assert_not_called()
            run.assert_not_called()
            self.assertFalse(args.bootstrap_dir.exists())

    def test_secrets_checks_ssh_key_format_before_registration(self):
        self.create_plan()
        self.key.write_text("ssh-ed25519 " + base64.b64encode(b"not an SSH key").decode())
        args = argparse.Namespace(name="vps-test", bootstrap_dir=Path(self.tmp.name) / "bootstrap",
                                  admin_recipient=TEST_ADMIN)
        with patch.object(V.getpass, "getpass", return_value=""), patch.object(V, "load") as load, \
             self.assertRaisesRegex(V.Error, "ssh-keygen"):
            V.provision(args, self.repo, self.state, self.host)
        load.assert_not_called()
        self.assertFalse(args.bootstrap_dir.exists())

    def test_secrets_reads_all_saved_paths_independent_of_current_directory(self):
        backup = Path(self.tmp.name) / "backup.pub"
        backup.write_text(self.key.read_text() + " backup")
        self.args.admin_key.append(backup)
        self.create_plan()
        machine = V.read_json(self.host / "machine.json")
        cwd = os.getcwd()
        try:
            os.chdir(self.state)
            with patch("builtins.input") as prompt:
                keys = V.prepare_admin_keys(machine, self.repo)
        finally:
            os.chdir(cwd)
        prompt.assert_not_called()
        self.assertEqual(keys, [self.key.read_text(), backup.read_text()])

    def test_secrets_rejects_invalid_saved_paths_before_key_generation(self):
        for filenames in ([], "admin.pub", [1], ["admin.pub"], ["/private-key"]):
            with self.subTest(filenames=filenames), patch.object(V, "ensure_admin_key") as ensure, \
                 self.assertRaises(V.Error):
                V.prepare_admin_keys({"adminKeyFiles": filenames}, self.repo)
            ensure.assert_not_called()

    def test_secrets_artifacts_share_host_directory_with_probe_and_plan(self):
        observed = V.read_json(self.state / "probe.json")
        self.state = self.host
        V.write_json(self.host / "probe.json", observed)
        self.create_plan()
        args = argparse.Namespace(name="vps-test", bootstrap_dir=Path(self.tmp.name) / "bootstrap",
                                  admin_recipient=TEST_ADMIN)
        encrypted = []
        with self.provision_mocks(encrypted), patch.object(V.getpass, "getpass", return_value="123:test-token"), \
             patch("builtins.input", return_value="-123"), contextlib.redirect_stdout(io.StringIO()):
            V.provision(args, self.repo, self.state, self.host)
        self.assertTrue((self.host / "probe.json").is_file())
        self.assertTrue((self.host / "plan.json").is_file())
        self.assertTrue((self.host / "credentials.json").is_file())
        self.assertTrue((self.host / "secrets.yaml").is_file())
        self.assertTrue((self.host / "monitoring.yaml").is_file())
        self.assertFalse((self.repo / "secrets/hosts/vps-test.yaml").exists())
        self.assertEqual(V.read_json(self.host / "credentials.json")["bootstrap_dir"], str(args.bootstrap_dir))

    def test_secrets_supports_legacy_plan_with_public_contents(self):
        self.create_plan()
        machine = V.read_json(self.host / "machine.json")
        del machine["adminKeyFiles"]
        machine["adminKeys"] = [self.key.read_text()]
        V.write_json(self.host / "machine.json", machine)
        self.key.unlink()
        args = argparse.Namespace(name="vps-test", bootstrap_dir=Path(self.tmp.name) / "bootstrap",
                                  admin_recipient=TEST_ADMIN)
        with self.provision_mocks([]), patch.object(V.getpass, "getpass", return_value=""), \
             patch.object(V, "ensure_admin_key") as ensure, contextlib.redirect_stdout(io.StringIO()):
            V.provision(args, self.repo, self.state, self.host)
        ensure.assert_not_called()
        self.assertEqual(V.read_json(self.host / "machine.json")["adminKeys"], machine["adminKeys"])

    def test_secrets_refuses_nonregular_machine_without_prompt(self):
        self.create_plan()
        machine = self.host / "machine.json"
        target = self.repo / "outside.json"
        machine.rename(target)
        before = target.read_bytes()
        args = V.parser().parse_args(["secrets", "vps-test"])
        for kind in ("symlink", "directory"):
            if kind == "symlink":
                machine.symlink_to(target)
            else:
                machine.mkdir()
            with self.subTest(kind=kind), patch("builtins.input") as prompt, \
                 patch.object(V.getpass, "getpass") as token, patch.object(V, "run") as run, \
                 self.assertRaisesRegex(V.Error, "non-regular generated file"):
                V.provision(args, self.repo, self.state, self.host)
            prompt.assert_not_called()
            token.assert_not_called()
            run.assert_not_called()
            self.assertEqual(target.read_bytes(), before)
            if kind == "symlink":
                machine.unlink()
            else:
                machine.rmdir()

    def test_optional_telegram_token_and_chat_id_after_token(self):
        self.create_plan()
        before = V.read_json(self.host / "machine.json")
        before["custom"] = {"preserve": [1, 2]}
        V.write_json(self.host / "machine.json", before)
        self.overwrite.reset_mock()
        args = argparse.Namespace(name="vps-test", bootstrap_dir=Path(self.tmp.name) / "bootstrap",
                                  admin_recipient=TEST_ADMIN)
        encrypted = []
        with self.provision_mocks(encrypted), patch.object(V.getpass, "getpass", return_value=""), \
             patch("builtins.input") as chat, contextlib.redirect_stdout(io.StringIO()):
            V.provision(args, self.repo, self.state, self.host)
        chat.assert_not_called()
        self.overwrite.assert_called_once_with(self.state / "credentials.json", force=False)
        self.assertEqual(V.read_json(self.host / "machine.json"),
                         before | {"adminKeys": [self.key.read_text()], "chatId": 0})
        self.assertEqual(len(encrypted), 1)
        self.assertFalse((self.host / "monitoring.yaml").exists())
        self.assertEqual(encrypted[0][1], self.host / "secrets.yaml")
        self.assertEqual(V.read_json(self.host / "machine.json")["chatId"], 0)
        self.assertEqual(V.read_json(self.host / "machine.json")["adminKeys"], [self.key.read_text()])
        self.assertNotIn("chat_id", V.read_json(self.state / "credentials.json"))

    def test_token_encrypted_and_chat_id_saved(self):
        self.create_plan()
        before = V.read_json(self.host / "machine.json")
        self.overwrite.reset_mock()
        args = argparse.Namespace(name="vps-test", bootstrap_dir=Path(self.tmp.name) / "bootstrap",
                                  admin_recipient=TEST_ADMIN)
        events, encrypted = [], []
        def token(*_args):
            events.append("token")
            return "123:test-private-token"
        def chat(*_args):
            events.append("chat")
            return "-1001234567890"
        with self.provision_mocks(encrypted), patch.object(V.getpass, "getpass", side_effect=token), \
             patch("builtins.input", side_effect=chat), contextlib.redirect_stdout(io.StringIO()):
            V.provision(args, self.repo, self.state, self.host)
        self.assertEqual(events, ["token", "chat"])
        self.overwrite.assert_called_once_with(self.state / "credentials.json", force=False)
        self.assertEqual(V.read_json(self.host / "machine.json"),
                         before | {"adminKeys": [self.key.read_text()], "chatId": -1001234567890})
        self.assertEqual(encrypted[1][0], {"bot_token": "123:test-private-token"})
        self.assertEqual(encrypted[0][1], self.host / "secrets.yaml")
        self.assertEqual(encrypted[1][1], self.host / "monitoring.yaml")
        self.assertEqual(V.read_json(self.host / "machine.json")["chatId"], -1001234567890)
        for path in (self.host / "machine.json", self.state / "credentials.json"):
            self.assertNotIn("test-private-token", path.read_text())

    def test_invalid_token_or_chat_stops_before_registration(self):
        self.create_plan()
        args = argparse.Namespace(name="vps-test", bootstrap_dir=Path(self.tmp.name) / "bootstrap",
                                  admin_recipient=TEST_ADMIN)
        for token, chat in (("bad", "123"), ("123:token", "0"), ("123:token", ""),
                            ("123:token", "oops"), ("123:token", str(2**63))):
            with self.subTest(token=token, chat=chat), patch.object(V.getpass, "getpass", return_value=token), \
                 patch("builtins.input", return_value=chat), patch.object(V, "run") as run, \
                 patch.object(V, "load") as load, self.assertRaises(V.Error):
                V.provision(args, self.repo, self.state, self.host)
            run.assert_not_called()
            load.assert_not_called()
            self.assertFalse(args.bootstrap_dir.exists())

    def test_optional_monitoring_secret_changes_source_digest(self):
        self.create_plan()
        for filename in ("flake.lock", "hosts/default.nix", "hosts/generated.nix",
                         "hosts/inventory.nix", "scripts/lt1-warp.py", "scripts/vps-credentials.py", "scripts/vps-warp.py",
                         "hosts/generated/vps-test/secrets.yaml"):
            path = self.repo / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("test input")
        PREPARE_INPUTS(self.repo, "vps-test")
        before = V.source_digest(self.repo, "vps-test")
        monitoring = self.host / "monitoring.yaml"
        monitoring.write_text("ciphertext")
        self.assertNotEqual(before, V.source_digest(self.repo, "vps-test"))
        monitoring.unlink()
        self.assertEqual(before, V.source_digest(self.repo, "vps-test"))
        monitoring.symlink_to("missing")
        with self.assertRaises(V.Error):
            V.source_digest(self.repo, "vps-test")

    def test_generated_loader_changes_source_digest(self):
        self.create_plan()
        for filename in ("flake.lock", "hosts/default.nix", "hosts/inventory.nix",
                         "scripts/lt1-warp.py", "scripts/vps-credentials.py", "scripts/vps-warp.py",
                         "hosts/generated/vps-test/secrets.yaml"):
            path = self.repo / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("test input")
        PREPARE_INPUTS(self.repo, "vps-test")
        before = V.source_digest(self.repo, "vps-test")
        (self.repo / "hosts/generated.nix").write_text("changed loader")
        self.assertNotEqual(before, V.source_digest(self.repo, "vps-test"))

    def test_workflow_artifacts_do_not_change_source_digest(self):
        self.create_plan()
        for filename in ("flake.lock", "hosts/default.nix", "hosts/inventory.nix",
                         "scripts/lt1-warp.py", "scripts/vps-credentials.py", "scripts/vps-warp.py",
                         "hosts/generated/vps-test/secrets.yaml"):
            path = self.repo / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("test input")
        PREPARE_INPUTS(self.repo, "vps-test")
        before = V.source_digest(self.repo, "vps-test")
        for filename in ("probe.json", "checked.json", "verified.json", "install-attempt.json",
                         "credentials.json", "plan.json", "errors/error.json", "installer-known-hosts-test"):
            V.write_json(self.host / filename, {"report": "not a configuration input"})
            self.assertEqual(V.source_digest(self.repo, "vps-test"), before)
        # A mismatched core fails closed instead of validating with another tool pin.
        lock = V.read_json(self.repo / "flake.lock")
        changed = copy.deepcopy(lock)
        changed["nodes"]["core"]["locked"]["rev"] = "c" * 40
        (self.repo / "flake.lock").write_text(json.dumps(changed))
        with self.assertRaises(V.Error):
            V.source_digest(self.repo, "vps-test")
        (self.repo / "flake.lock").write_text(json.dumps(lock))
        self.assertEqual(V.source_digest(self.repo, "vps-test"), before)
        (self.host / "configuration.nix").write_text("changed configuration")
        self.assertNotEqual(V.source_digest(self.repo, "vps-test"), before)

    def test_payloads_only_require_token_when_telegram_enabled(self):
        self.create_plan()
        directory = self.host
        main = directory / "secrets.yaml"
        credentials = {"host_recipient": "age1host", "admin_recipient": "age1admin"}
        main.write_text("recipient: age1host\nrecipient: age1admin\n")
        with patch.object(V, "evaluate", return_value=False), patch.object(V, "run", return_value=b'{"material": "{}", "warp": "{}"}') as run:
            self.assertEqual(len(V.payloads(self.repo, "vps-test", credentials)), 1)
        run.assert_called_once()
        monitoring = directory / "monitoring.yaml"
        monitoring.write_text(main.read_text())
        for token, passes in (("123:valid-token", True), ("", False), ("invalid", False)):
            with self.subTest(token=token), patch.object(V, "evaluate", return_value=True), \
                 patch.object(V, "run", side_effect=[b'{"material": "{}", "warp": "{}"}', json.dumps({"bot_token": token}).encode()]):
                if passes:
                    self.assertEqual(len(V.payloads(self.repo, "vps-test", credentials)), 2)
                else:
                    with self.assertRaises(V.Error):
                        V.payloads(self.repo, "vps-test", credentials)
        monitoring.write_text("recipient: age1wrong\n")
        with patch.object(V, "evaluate", return_value=True), patch.object(V, "run") as run, self.assertRaises(V.Error):
            V.payloads(self.repo, "vps-test", credentials)
        run.assert_not_called()

    def test_payloads_automatically_use_host_specific_administrator_identity(self):
        directory = self.host
        directory.mkdir(parents=True)
        (directory / "secrets.yaml").write_text("recipient: age1host\nrecipient: age1admin\n")
        identity = Path(self.tmp.name) / "admin-age.txt"
        identity.write_text("private test identity")
        identity.chmod(0o600)
        credentials = {"host_recipient": "age1host", "admin_recipient": "age1admin", "admin_identity": str(identity)}
        with patch.object(V, "evaluate", return_value=False), patch.object(V, "run", return_value=b'{}') as run:
            self.assertEqual(V.payloads(self.repo, "vps-test", credentials), [{}])
        self.assertEqual(run.call_args.kwargs["env"]["SOPS_AGE_KEY_FILE"], str(identity))
        identity.chmod(0o644)
        with patch.object(V, "evaluate", return_value=False), patch.object(V, "run") as run, self.assertRaises(V.Error):
            V.payloads(self.repo, "vps-test", credentials)
        run.assert_not_called()

    def test_final_ssh_key_is_pinned_in_separate_known_hosts(self):
        self.create_plan()
        plan = V.read_json(self.state / "plan.json")
        m = V.read_json(self.host / "machine.json")
        key = self.create_admin_private_key()
        c = V.production_connection(plan, m, {"host_public_key": "ssh-ed25519 AAAAFINAL comment"}, self.state)
        self.assertEqual(c["key"], str(key))
        self.assertNotEqual(c["key"], plan["connection"]["key"])
        self.assertIn("StrictHostKeyChecking=yes", V.ssh_args(c))
        self.assertEqual(c["user"], "admin")
        self.assertEqual(c["port"], 3333)
        self.assertEqual(Path(c["known_hosts"]).read_text(), "[203.0.113.5]:3333 ssh-ed25519 AAAAFINAL\n")

    def test_legacy_machine_without_admin_user_keeps_operator(self):
        self.create_plan()
        plan = V.read_json(self.state / "plan.json")
        machine = V.read_json(self.host / "machine.json")
        del machine["adminUser"]
        self.create_admin_private_key()
        final = V.production_connection(plan, machine, {"host_public_key": "ssh-ed25519 AAAAFINAL"}, self.state)
        self.assertEqual(final["user"], "operator")

    def test_invalid_saved_admin_user_is_rejected_before_ssh(self):
        self.create_plan()
        plan = V.read_json(self.state / "plan.json")
        machine = V.read_json(self.host / "machine.json") | {"adminUser": "root"}
        with self.assertRaises(ValueError):
            V.production_connection(plan, machine, {"host_public_key": "ssh-ed25519 AAAAFINAL"}, self.state)

    def test_default_port_ssh_pin_uses_plain_address(self):
        self.create_plan()
        plan = V.read_json(self.state / "plan.json")
        m = V.read_json(self.host / "machine.json") | {"sshPort": 22}
        self.create_admin_private_key()
        c = V.production_connection(plan, m, {"host_public_key": "ssh-ed25519 AAAAFINAL"}, self.state)
        self.assertEqual(Path(c["known_hosts"]).read_text(), "203.0.113.5 ssh-ed25519 AAAAFINAL\n")

    def test_production_key_override_and_legacy_plan_never_use_probe_identity(self):
        self.create_plan()
        plan = V.read_json(self.state / "plan.json")
        machine = V.read_json(self.host / "machine.json")
        credentials = {"host_public_key": "ssh-ed25519 AAAAFINAL"}
        alternate = Path(self.tmp.name) / "alternate-admin"
        alternate.write_text("synthetic alternate private key")
        for legacy in (False, True):
            current = machine.copy()
            if legacy:
                del current["adminKeyFiles"]
            with self.subTest(legacy=legacy):
                c = V.production_connection(plan, current, credentials, self.state, key=alternate)
                self.assertEqual(c["key"], str(alternate))
                self.assertNotEqual(c["key"], plan["connection"]["key"])
        del machine["adminKeyFiles"]
        with self.assertRaisesRegex(V.Error, "use verify HOST --key"):
            V.production_connection(plan, machine, credentials, self.state)

    def test_missing_admin_private_key_never_falls_back_to_probe(self):
        self.create_plan()
        plan = V.read_json(self.state / "plan.json")
        machine = V.read_json(self.host / "machine.json")
        credentials = {"host_public_key": "ssh-ed25519 AAAAFINAL"}
        source_key = Path(self.tmp.name) / "source-key"
        source_key.write_text("synthetic source private key")
        plan["connection"]["key"] = str(source_key)
        with self.assertRaisesRegex(V.Error, "Administrator private SSH key not found"):
            V.production_connection(plan, machine, credentials, self.state)
        self.assertFalse((self.state / "installed-known-hosts").exists())

    def test_multiple_admin_keys_default_to_first_and_allow_explicit_selection(self):
        self.create_plan()
        plan = V.read_json(self.state / "plan.json")
        machine = V.read_json(self.host / "machine.json")
        first = self.create_admin_private_key()
        second = Path(self.tmp.name) / "second-admin"
        second.write_text("synthetic second private key")
        machine["adminKeyFiles"].append(str(second.with_suffix(".pub")))
        credentials = {"host_public_key": "ssh-ed25519 AAAAFINAL"}
        c = V.production_connection(plan, machine, credentials, self.state)
        self.assertEqual(c["key"], str(first))
        c = V.production_connection(plan, machine, credentials, self.state, key=second)
        self.assertEqual(c["key"], str(second))

    def test_invalid_production_key_paths_are_rejected(self):
        self.create_plan()
        plan = V.read_json(self.state / "plan.json")
        machine = V.read_json(self.host / "machine.json")
        credentials = {"host_public_key": "ssh-ed25519 AAAAFINAL"}
        for files in ([], "admin.pub", [1], ["relative.pub"], ["/private-key"]):
            with self.subTest(files=files), self.assertRaises(V.Error):
                V.production_connection(plan, machine | {"adminKeyFiles": files}, credentials, self.state)
        with self.assertRaisesRegex(V.Error, "PRIVATE SSH key"):
            V.production_connection(plan, machine, credentials, self.state, key=self.key)

    def test_verify_parser_has_separate_private_key_option(self):
        args = V.parser().parse_args(["verify", "vps-test"])
        self.assertIsNone(args.key)
        args = V.parser().parse_args(["verify", "vps-test", "--key", "/admin-key"])
        self.assertEqual(args.key, Path("/admin-key"))

    def test_telegram_verify_disabled_succeeds_enabled_requires_delivery(self):
        self.create_plan()
        admin_key = self.create_admin_private_key()
        alternate = Path(self.tmp.name) / "alternate-admin"
        alternate.write_text("synthetic alternate private key")
        metrics = {"status": "success", "data": {"result": [{"value": [0, "1"]}]}}
        good = "\n".join(f"LISTEN 0 128 127.0.0.1:{p} 0.0.0.0:*" for p in V.PRIVATE_PORTS)
        def run(cmd, *_args, **_kwargs):
            if cmd[-1] == "ss -H -lnt": return good.encode()
            if "api/v1/query" in cmd[-1]: return json.dumps(metrics).encode()
            if "sshd -T" in cmd[-1]:
                self.assertIn("-f /etc/ssh/sshd_config", cmd[-1])
                grep = shlex.split(cmd[-1].rsplit("|", 1)[1])
                # OpenSSH 10.5 emits canonical names, older versions lowercase.
                for text, expected in ((b"permitrootlogin no\n", 0), (b"PermitRootLogin no\n", 0),
                                       (b"PermitRootLogin yes\n", 1)):
                    self.assertEqual(subprocess.run(grep, input=text, capture_output=True).returncode, expected)
            return b"ok"
        for enabled, code, status, key in ((False, 0, "disabled", None),
                                          (True, 2, "not_tested", alternate),
                                          (True, 0, "operator_confirmed", None)):
            with self.subTest(enabled=enabled), patch.object(V, "evaluate", return_value=enabled), \
                 patch.object(V, "run", side_effect=run) as remote, patch.object(V, "tunnel_test"), \
                 patch.object(V.socket, "create_connection", side_effect=ConnectionRefusedError), \
                 patch.object(V, "cleanup_installation_key") as cleanup, \
                 patch.object(V, "confirm"), patch.object(V, "send_test"), \
                 contextlib.redirect_stdout(io.StringIO()):
                args = argparse.Namespace(name="vps-test", send_test_alert=status == "operator_confirmed", key=key)
                self.assertEqual(V.verify(args, self.repo, self.state, self.host), code)
            if code == 0:
                cleanup.assert_called_once_with(self.state, self.repo,
                    protected_keys=[str(admin_key), admin_key])
            else:
                cleanup.assert_not_called()
            for call in remote.call_args_list:
                command = call.args[0]
                self.assertEqual(command[command.index("-i") + 1], str(key or admin_key))
                self.assertNotIn(self.c["key"], command)
            self.assertEqual(V.read_json(self.state / "verified.json")["telegram"], status)
            self.assertEqual(V.read_json(self.state / "verified.json")["ipv6"], "disabled")
            self.assertTrue(any(call.args[0][-1] == V.IPV6_DISABLED_CHECK for call in remote.call_args_list))

    def test_verify_requires_disabled_ipv6_before_tunnel_or_success_report(self):
        self.create_plan()
        self.create_admin_private_key()
        good = "\n".join(f"LISTEN 0 128 127.0.0.1:{p} 0.0.0.0:*" for p in V.PRIVATE_PORTS)
        def run(cmd, *_args, **_kwargs):
            if cmd[-1] == "ss -H -lnt":
                return good.encode()
            if cmd[-1] == V.IPV6_DISABLED_CHECK:
                raise V.Error("IPv6 check failed")
            return b"ok"
        with patch.object(V, "evaluate", return_value=False), patch.object(V, "run", side_effect=run), \
             patch.object(V, "tunnel_test") as tunnel, patch.object(V, "cleanup_installation_key") as cleanup, \
             self.assertRaisesRegex(V.Error, "IPv6 check failed"):
            V.verify(argparse.Namespace(name="vps-test", send_test_alert=False), self.repo, self.state, self.host)
        tunnel.assert_not_called()
        cleanup.assert_not_called()
        self.assertFalse((self.state / "verified.json").exists())

    def test_verify_failure_retains_temporary_key(self):
        self.create_plan()
        self.create_admin_private_key()
        with patch.object(V, "evaluate", return_value=False), \
             patch.object(V, "run", side_effect=V.Error("SSH unavailable")), \
             patch.object(V, "cleanup_installation_key") as cleanup, self.assertRaisesRegex(V.Error, "SSH unavailable"):
            V.verify(argparse.Namespace(name="vps-test", send_test_alert=False), self.repo, self.state, self.host)
        cleanup.assert_not_called()

    def test_disabled_telegram_rejects_alert_without_remote_actions(self):
        self.create_plan()
        with patch.object(V, "evaluate", return_value=False), patch.object(V, "run") as run, \
             self.assertRaisesRegex(V.Error, "Telegram is disabled"):
            V.verify(argparse.Namespace(name="vps-test", send_test_alert=True), self.repo, self.state, self.host)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
