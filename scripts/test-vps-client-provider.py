#!/usr/bin/env python3
"""Client projection unit/filesystem fixtures; no real credentials or services."""
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location("provider", Path(__file__).with_name("vps-client-provider.py"))
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.binding = {"host": "fixture", "client": "default", "nodeName": "lt1-vps",
                        "stateFile": "mihomo/vps/agents-lt.json"}
        self.public = {"clients": ["default", "other"], "serverName": "example.org"}
        self.material = {"private_key": "PRIVATE-MUST-NOT-ESCAPE", "public_key": "A" * 43, "short_id": "aabb",
                         "clients": {"default": {"uuid": "12345678-1234-4234-8234-123456789012"},
                                     "other": {"uuid": "OTHER-MUST-NOT-ESCAPE"}}}
        self.path = self.root / "state/mihomo/vps/agents-lt.json"

    def projection(self):
        return P.project(self.binding, self.public, self.material, "203.0.113.8")

    def test_projection_is_an_allowlist_of_only_selected_client(self):
        value = self.projection()
        self.assertEqual(set(value), {"proxies"})
        node, = value["proxies"]
        self.assertEqual(node, {"name": "lt1-vps", "type": "vless", "server": "203.0.113.8", "port": 443,
            "uuid": self.material["clients"]["default"]["uuid"], "network": "tcp", "udp": True,
            "tls": True, "flow": "xtls-rprx-vision", "servername": "example.org", "client-fingerprint": "chrome",
            "reality-opts": {"public-key": "A" * 43, "short-id": "aabb"}})
        self.assertNotIn("PRIVATE", json.dumps(value))
        self.assertNotIn("OTHER", json.dumps(value))

    def test_projection_tracks_changed_connection_without_changing_binding(self):
        first = self.projection()
        self.public.update(port=8443, serverName="new.example.org")
        self.material.update(public_key="B" * 43, short_id="ccdd")
        self.material["clients"]["default"]["uuid"] = "87654321-1234-4234-8234-123456789012"
        second = P.project(self.binding, self.public, self.material, "203.0.113.9")
        self.assertNotEqual(first, second)
        self.assertEqual(second["proxies"][0]["name"], "lt1-vps")
        self.assertEqual(second["proxies"][0]["server"], "203.0.113.9")
        self.assertEqual(second["proxies"][0]["port"], 8443)
        self.assertEqual(second["proxies"][0]["servername"], "new.example.org")

    def test_disabled_client_projects_nonmatching_reject_not_empty_invalid_provider(self):
        self.public["clients"] = ["other"]
        value = self.projection()
        self.assertEqual(value, P.UNAVAILABLE)
        self.assertNotEqual(value["proxies"][0]["name"], self.binding["nodeName"])
        self.assertEqual(value["proxies"][0]["type"], "reject")

    def test_invalid_selected_material_is_rejected_without_secret_details(self):
        for field, value in (("public_key", "SECRET-KEY"), ("short_id", "SECRET-ID")):
            with self.subTest(field=field), mock.patch.dict(self.material, {field: value}):
                with self.assertRaisesRegex(ValueError, "details withheld") as caught:
                    self.projection()
                self.assertNotIn("SECRET", str(caught.exception))
        del self.material["clients"]["default"]
        with self.assertRaisesRegex(ValueError, "details withheld"):
            self.projection()

    def test_bad_endpoints_and_uuid_are_rejected(self):
        for port in (True, 0, 65536, "443"):
            with self.subTest(port=port), mock.patch.dict(self.public, {"port": port}):
                with self.assertRaises(ValueError):
                    self.projection()
        for address in ("example.org", "127.1", "203.0.113.8/32"):
            with self.assertRaises(ValueError):
                P.project(self.binding, self.public, self.material, address)
        self.material["clients"]["default"]["uuid"] = "SECRET-BAD-UUID"
        with self.assertRaisesRegex(ValueError, "details withheld"):
            self.projection()

    def test_atomic_publication_is_private_and_leaves_no_temporary_files(self):
        P.write(self.path, self.projection())
        old_inode = self.path.stat().st_ino
        P.write(self.path, P.UNAVAILABLE)
        self.assertNotEqual(old_inode, self.path.stat().st_ino)
        self.assertEqual(json.loads(self.path.read_text()), P.UNAVAILABLE)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_seed_is_fail_closed_and_never_overwrites_published_credentials(self):
        P.write(self.path, P.UNAVAILABLE, seed=True)
        self.assertEqual(json.loads(self.path.read_text()), P.UNAVAILABLE)
        P.write(self.path, self.projection())
        previous = self.path.read_bytes(), self.path.stat().st_ino
        P.write(self.path, P.UNAVAILABLE, seed=True)
        self.assertEqual(previous, (self.path.read_bytes(), self.path.stat().st_ino))

    def test_symlinks_and_shared_file_or_directory_are_refused(self):
        P.write(self.path, P.UNAVAILABLE)
        self.path.unlink()
        other = self.root / "other"
        other.write_text("not touched")
        self.path.symlink_to(other)
        with self.assertRaises(ValueError):
            P.write(self.path, self.projection())
        self.assertEqual(other.read_text(), "not touched")
        self.path.unlink()
        self.path.parent.rmdir()
        self.path.parent.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            P.write(self.path, self.projection())
        self.path.parent.unlink()
        self.path.parent.mkdir(mode=0o755)
        with self.assertRaises(ValueError):
            P.write(self.path, self.projection())
        self.path.parent.chmod(0o700)
        self.path.write_text("public")
        self.path.chmod(0o644)
        with self.assertRaises(ValueError):
            P.write(self.path, self.projection())

    def test_failed_replace_preserves_last_deployed_projection_and_cleans_temporary(self):
        P.write(self.path, self.projection())
        before = self.path.read_bytes()
        with mock.patch.object(P.os, "replace", side_effect=OSError("failure")):
            with self.assertRaises(OSError):
                P.write(self.path, P.UNAVAILABLE)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_bindings_are_explicit_and_validated(self):
        source = self.root / "bindings.json"
        source.write_text(json.dumps({"agents-lt": self.binding}))
        with mock.patch.dict(os.environ, {"VPS_CLIENT_PROVIDERS": str(source)}):
            self.assertEqual(P.bindings(), {"agents-lt": self.binding})
            for key, bad in (("stateFile", "../outside"), ("stateFile", "/nix/store/secret"),
                             ("host", "../fixture"), ("client", "bad client"), ("nodeName", "__unavailable__")):
                source.write_text(json.dumps({"agents-lt": self.binding | {key: bad}}))
                with self.subTest(key=key, bad=bad), self.assertRaises(ValueError):
                    P.bindings()
            source.write_text(json.dumps({"agents-lt": self.binding, "other": self.binding}))
            with self.assertRaises(ValueError):
                P.bindings()
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(P.bindings(), {})

    @contextlib.contextmanager
    def probe_fixture(self):
        process = mock.Mock()
        process.poll.return_value = None
        paths, configs = [], []
        def start(command, **kwargs):
            path = Path(command[-1])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
            paths.append(path)
            configs.append(json.loads(path.read_text()))
            return process
        with mock.patch.dict(os.environ, {"VPS_MIHOMO_BINARY": "/fixture/mihomo",
                                         "HTTP_PROXY": "http://untrusted", "HOME": "/untrusted"}), \
                mock.patch.object(P.socket, "socket") as listener, \
                mock.patch.object(P.socket, "create_connection") as connect, \
                mock.patch.object(P.time, "sleep"), \
                mock.patch.object(P.subprocess, "Popen", side_effect=start) as popen, \
                mock.patch.object(P.subprocess, "run") as curl:
            listener.return_value.__enter__.return_value.getsockname.return_value = ("127.0.0.1", 31001)
            curl.return_value = mock.Mock(returncode=0, stdout=b"204")
            yield process, paths, configs, popen, curl, connect
        self.assertTrue(all(not path.parent.exists() for path in paths))

    def test_probe_is_private_isolated_and_has_no_direct_or_ambient_proxy_route(self):
        with self.probe_fixture() as (process, paths, configs, popen, curl, connect):
            P.probe(self.projection())
            config, = configs
            self.assertEqual(config["rules"], ["MATCH,managed-probe"])
            self.assertEqual(len(config["proxies"]), 1)
            node, = config["proxies"]
            self.assertEqual(node["type"], "vless")
            self.assertEqual(node["uuid"], self.material["clients"]["default"]["uuid"])
            self.assertEqual(node["server"], "203.0.113.8")
            self.assertEqual(config["bind-address"], "127.0.0.1")
            self.assertFalse(config["allow-lan"])
            self.assertNotIn("external-controller", config)
            self.assertNotIn("DIRECT", json.dumps(config))
            self.assertNotIn("PRIVATE", json.dumps(config))
            self.assertNotIn("OTHER", json.dumps(config))
            self.assertEqual(popen.call_args.args[0][0], "/fixture/mihomo")
            self.assertEqual(popen.call_args.kwargs["stderr"], P.subprocess.DEVNULL)
            env = popen.call_args.kwargs["env"]
            self.assertEqual(env["HOME"], str(paths[0].parent))
            self.assertNotIn("HTTP_PROXY", env)
            command = curl.call_args.args[0]
            self.assertEqual(command[1], "--disable")
            self.assertIn("socks5h://127.0.0.1:31001", command)
            self.assertIn("=https", command)
            self.assertIn("https://cp.cloudflare.com/generate_204", command)
            self.assertNotIn("--location", command)
            self.assertNotIn(node["uuid"], str(command))
            self.assertEqual(curl.call_args.kwargs["stderr"], P.subprocess.DEVNULL)
            self.assertEqual(curl.call_args.kwargs["timeout"], 20)
            self.assertEqual(curl.call_args.kwargs["env"], env)
            connect.assert_called_once_with(("127.0.0.1", 31001), timeout=0.1)
            process.terminate.assert_called_once()
            self.assertFalse(self.path.exists(), "The probe must not publish to the live provider")

    def test_probe_failure_timeout_and_interrupt_clean_up_client_and_plaintext(self):
        for failure in ("status", "curl", "timeout", "interrupt", "startup", "exit"):
            with self.subTest(failure=failure), self.probe_fixture() as (process, paths, configs, popen, curl, connect):
                expected = ValueError
                if failure == "status":
                    curl.return_value.stdout = b"200"
                elif failure == "curl":
                    curl.return_value.returncode = 7
                elif failure == "timeout":
                    curl.side_effect = P.subprocess.TimeoutExpired("curl", 20)
                    expected = P.subprocess.TimeoutExpired
                elif failure == "interrupt":
                    curl.side_effect = KeyboardInterrupt()
                    expected = KeyboardInterrupt
                elif failure == "startup":
                    connect.side_effect = OSError("not ready")
                else:
                    process.poll.return_value = 1
                with self.assertRaises(expected):
                    P.probe(self.projection())
                if failure == "exit":
                    curl.assert_not_called()
                else:
                    process.terminate.assert_called_once()
                if failure == "startup":
                    self.assertEqual(connect.call_count, 50)
                    curl.assert_not_called()
                self.assertFalse(self.path.exists())

    def test_probe_launch_failure_or_exit_after_https_cleans_plaintext_and_refuses_success(self):
        for failure in ("launch", "late-exit"):
            with self.subTest(failure=failure), self.probe_fixture() as (process, paths, configs, popen, curl, connect):
                expected = ValueError
                if failure == "launch":
                    start = popen.side_effect
                    def fail(*args, **kwargs):
                        start(*args, **kwargs)
                        raise OSError("cannot start")
                    popen.side_effect = fail
                    expected = OSError
                else:
                    process.poll.side_effect = [None, 1, 1]
                with self.assertRaises(expected):
                    P.probe(self.projection())
                process.terminate.assert_not_called()
                self.assertFalse(self.path.exists())

    def test_probe_kills_client_that_does_not_terminate(self):
        with self.probe_fixture() as (process, *_):
            process.wait.side_effect = [P.subprocess.TimeoutExpired("mihomo", 5), 0]
            P.probe(self.projection())
            process.kill.assert_called_once()
            self.assertEqual(process.wait.call_count, 2)

    def test_probe_refuses_disabled_client_and_missing_pinned_binary(self):
        with self.probe_fixture() as (_, paths, configs, popen, curl, connect):
            with self.assertRaises(ValueError):
                P.probe(P.UNAVAILABLE)
            for binary in ("", "relative/mihomo"):
                with mock.patch.dict(os.environ, {"VPS_MIHOMO_BINARY": binary}), self.assertRaises(ValueError):
                    P.probe(self.projection())
            popen.assert_not_called()
            curl.assert_not_called()
            self.assertEqual(paths, [])

    def test_destination_is_local_xdg_state_not_repository_or_store(self):
        repo = self.root / "repo"
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root / "state")}):
            self.assertEqual(P.destination(self.binding, repo), self.path)
        for root in (str(repo), "/nix/store", "relative", str(repo / ".." / "state")):
            with self.subTest(root=root), mock.patch.dict(os.environ, {"XDG_STATE_HOME": root}):
                with self.assertRaises(ValueError):
                    P.destination(self.binding, repo)


if __name__ == "__main__":
    unittest.main()
