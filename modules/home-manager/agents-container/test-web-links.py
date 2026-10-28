#!/usr/bin/env python3
"""Browser bridge contracts; no host browser or user service is started."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("web_links", Path(__file__).with_name("web-links.py"))
web_links = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web_links)


class WebLinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.path = str(self.base / "socket")
        self.server = web_links.WebLinkServer(self.path, "/host/bin/systemd-run", "/host/bin/xdg-open")
        self.addCleanup(self.server.server_close)
        self.run = patch.object(web_links.subprocess, "run").start()
        self.addCleanup(patch.stopall)

    def start_server(self):
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.shutdown)

    def request(self, data):
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(2)
            client.connect(self.path)
            client.sendall(data)
            return json.loads(client.makefile("rb").readline())

    def test_exact_host_command_without_shell_and_private_socket(self):
        url = "https://example.com/a?q=$(touch%20/tmp/never)&x=one#part"
        self.assertEqual(self.server.open_url({"url": url}), {"ok": True})
        self.run.assert_called_once_with(
            ["/host/bin/systemd-run", "--user", "--collect", "--quiet", "--property=Type=exec",
             "--property=StandardOutput=null", "--property=StandardError=null",
             "--description=Agents web link", "--", "/host/bin/xdg-open", url],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=5, check=True,
        )
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_rejects_files_commands_credentials_controls_and_extra_fields(self):
        invalid = [None, [], "", "/etc/passwd", "file:///etc/passwd", "javascript:alert(1)",
                   "mailto:test@example.com", "--help", "https:", "https:///path",
                   "https://user:pass@example.com", "https://user@example.com",
                   "https://example.com:bad", "https://[broken/", "https://example.com\n",
                   "https://exa\tmple.com", "https://example.com/white space",
                   "https://example.com/\x00", "https://example.com/\x7f", "\nhttps://example.com",
                   "https://example.com/" + "x" * web_links.MAX_URL, "https://example.com/\ud800"]
        for url in invalid:
            with self.subTest(url=repr(url)), self.assertRaises(ValueError):
                self.server.open_url({"url": url})
        for request in [None, [], "https://example.com", {},
                        {"url": "https://example.com", "command": "sh"},
                        {"url": "https://example.com", "args": []}]:
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.server.open_url(request)
        self.run.assert_not_called()

    def test_accepts_http_https_ipv6_and_unicode(self):
        for url in ["http://localhost:8070/a", "https://example.com/path?x=1&y=2#part",
                    "https://[::1]:8443/a", "HTTPS://example.com", "https://example.com/тест"]:
            with self.subTest(url=url):
                self.server.attempts.clear()
                self.server.open_url({"url": url})
                self.assertEqual(self.run.call_args.args[0][-1], url)

    def test_rate_limits_immediate_clicks_and_total_requests(self):
        with patch.object(web_links.time, "monotonic", return_value=100) as clock:
            self.server.open_url({"url": "https://example.com"})
            with self.assertRaisesRegex(ValueError, "rate limit"):
                self.server.open_url({"url": "https://example.com"})
            for i in range(1, web_links.MAX_PER_MINUTE):
                clock.return_value = 100 + i
                self.server.open_url({"url": "https://example.com"})
            clock.return_value = 140
            with self.assertRaisesRegex(ValueError, "rate limit"):
                self.server.open_url({"url": "https://example.com"})
            clock.return_value = 161
            self.server.open_url({"url": "https://example.com"})
        self.assertEqual(self.run.call_count, web_links.MAX_PER_MINUTE + 1)

    def test_launcher_failure_is_bounded_and_does_not_expose_url(self):
        url = "https://example.com/?private=test-secret"
        for error in [FileNotFoundError(url), subprocess.CalledProcessError(1, [url]),
                      subprocess.TimeoutExpired([url], 5)]:
            with self.subTest(error=type(error).__name__):
                self.server.attempts.clear()
                self.run.side_effect = error
                with self.assertRaisesRegex(ValueError, "^host browser launcher failed$"):
                    self.server.open_url({"url": url})

    def test_socket_rejects_bad_protocol_then_accepts_valid_url(self):
        self.start_server()
        for data in [b"garbage\n", b"[]\n", b'x' * (web_links.MAX_REQUEST + 1) + b'\n',
                     b'{"url":"file:///etc/passwd"}\n',
                     b'[' * 2000 + b']' * 2000 + b'\n']:
            with self.subTest(data=data[:30]):
                self.assertFalse(self.request(data)["ok"])
        web_links.open_on_host("https://example.com/a", self.path)
        self.run.assert_called_once()

    def test_incomplete_request_times_out_and_server_recovers(self):
        self.start_server()
        self.assertFalse(self.request(b'{"url":')["ok"])
        web_links.open_on_host("https://example.com", self.path)
        self.run.assert_called_once()

    def test_foreign_uid_is_rejected_without_spawning(self):
        self.start_server()
        with patch.object(web_links.os, "geteuid", return_value=os.geteuid() + 1):
            self.assertFalse(self.request(b'{"url":"https://example.com"}\n')["ok"])
        self.run.assert_not_called()

    def test_client_failure_and_disabled_bridge_do_not_open_locally(self):
        with self.assertRaises(OSError):
            web_links.open_on_host("https://example.com", str(self.base / "absent"))
        self.start_server()
        self.run.side_effect = FileNotFoundError()
        with self.assertRaises(ValueError):
            web_links.open_on_host("https://example.com", self.path)
        self.run.assert_called_once()

    def test_real_broker_process_and_fixed_launcher_argv(self):
        # The child interpreter loads the real helper with a fake host launcher,
        # independently of this process's subprocess.run mock.
        runner = self.base / "runner"
        record = self.base / "argv.json"
        runner.write_text(
            f"#!{os.sys.executable}\nimport json, sys\n"
            f"open({str(record)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        )
        runner.chmod(0o755)
        # Popen is independent of the mock used by open_url's unit tests.
        path = str(self.base / "real-socket")
        proc = subprocess.Popen([os.sys.executable, "-B", str(Path(web_links.__file__)),
                                 "serve", path, str(runner), "/host/bin/xdg-open"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            import time
            deadline = time.monotonic() + 3
            while not Path(path).exists() and proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            web_links.open_on_host("https://example.com/?a=1&b=2", path)
            args = json.loads(record.read_text())
            self.assertEqual(args[-3:], ["--", "/host/bin/xdg-open", "https://example.com/?a=1&b=2"])
            self.assertIn("--property=Type=exec", args)
        finally:
            proc.terminate()
            proc.communicate(timeout=3)


if __name__ == "__main__":
    unittest.main()
