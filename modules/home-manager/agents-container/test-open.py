#!/usr/bin/env python3
"""Real loopback/stdio tests with fake Podman and desktop launcher, not host acceptance."""
from contextlib import contextmanager
import http.server
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).with_name('open.py')
spec = importlib.util.spec_from_file_location('preview', SOURCE)
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


def free_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


class Echo(socketserver.BaseRequestHandler):
    def handle(self):
        while data := self.request.recv(65536):
            self.request.sendall(data)
        self.request.sendall(b'after-half-close')


class HTTP(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'preview-ready')

    def log_message(self, *_):
        pass


class URLTest(unittest.TestCase):
    def test_bare_port_uses_http_and_ipv4_loopback(self):
        for value, port in (('1', 1), ('8071', 8071), ('65535', 65535), ('08071', 8071)):
            with self.subTest(value=value):
                self.assertEqual(preview.preview_url(value),
                                 (f'http://127.0.0.1:{port}', port))

    def test_refuses_invalid_bare_ports(self):
        for value in ('0', '65536', '-1', '+8071', ' 8071', '8071 ', '80\n71',
                      '\uff18\uff10\uff17\uff11', '\u0668\u0660\u0667\u0661', '8_071', '8e3', '0x1f87',
                      '8071/', '8071?db=synthetic', '9' * 8193):
            with self.subTest(value=value[:70]), self.assertRaises(ValueError):
                preview.preview_url(value)

    def test_preserves_path_query_fragment_and_normalizes_localhost(self):
        self.assertEqual(preview.preview_url('http://localhost:8071/web/login?db=synthetic#view'),
                         ('http://127.0.0.1:8071/web/login?db=synthetic#view', 8071))
        self.assertEqual(preview.preview_url('https://127.0.0.1:8443/a%20b')[1], 8443)

    def test_refuses_external_names_credentials_invalid_ports_and_controls(self):
        for value in ('', 'file:///tmp/test', 'javascript:alert(1)',
                      'http://example.com:8071/', 'http://127.0.0.2:8071/',
                      'http://127.0.0.1/', 'http://127.0.0.1:0/',
                      'http://127.0.0.1:65536/', 'http://127.0.0.1:bad/',
                      'http://[::1]:8071/', 'http://user@localhost:8071/',
                      'http://user:password@127.0.0.1:8071/',
                      'http://localhost:8071/\nsecret', ' http://localhost:8071/',
                      'http://localhost:8071/has space', 'http://localhost:8071/\x7f',
                      'http://localhost:8071/\\evil', 'http://localhost:8071/' + 'x' * 8192):
            with self.subTest(value=value[:70]), self.assertRaises(ValueError):
                preview.preview_url(value)

    def test_dispatch_is_host_only_and_does_not_change_lifecycle_source(self):
        module = SOURCE.parent.parent.joinpath('agents-container.nix').read_text()
        dispatch = module.split('if [[', 1)[1].split('export AGENTS_SECRET_SOURCES', 1)[0]
        self.assertIn('== open', dispatch)
        self.assertIn('${./agents-container/open.py}', dispatch)
        self.assertIn('${lib.escapeShellArg cfg.workspaceName}', dispatch)
        self.assertIn('-I -B', dispatch)
        self.assertNotIn('launcherText', dispatch)
        lifecycle = SOURCE.with_name('launcher.sh').read_text()
        self.assertNotIn('open.py', lifecycle)
        self.assertNotIn('agents open', lifecycle)


class PreviewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.events = self.root / 'events.jsonl'
        self.podman = self.root / 'podman'
        self.opener = self.root / 'host-opener'
        self.podman.write_text(f'''#!{sys.executable}
import json, os, sys
args = sys.argv[1:]
assert args[:9] == ['exec', '-i', '--user', '1000:1000', 'test-workspace',
                    '/bin/python3', '-I', '-u', '-c'], args[:9]
with open(os.environ['TEST_EVENTS'], 'a') as output:
    output.write(json.dumps({{'event': args[-1], 'pid': os.getpid()}}) + '\\n')
if os.environ.get('TEST_EXEC_FAIL'):
    sys.exit(17)
if os.environ.get('TEST_PROBE_HANG') and args[-1] == 'probe':
    import time
    time.sleep(60)
args[-2] = os.environ['TEST_TARGET_PORT']
os.execv(sys.executable, [sys.executable, *args[6:]])
''')
        self.opener.write_text(f'''#!{sys.executable}
import json, os, sys, time
assert len(sys.argv) == 2
url = sys.argv[1]
if os.environ.get('TEST_REQUIRE_DESKTOP'):
    assert os.environ.get('DISPLAY') == ':terminal-fixture'
    assert os.environ.get('WAYLAND_DISPLAY') == 'wayland-terminal-fixture'
    assert os.environ.get('DBUS_SESSION_BUS_ADDRESS') == 'unix:path=/fixture/desktop-bus'
if os.environ.get('TEST_CHECK_HTTP'):
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({{}}))
    assert opener.open(url, timeout=3).read() == b'preview-ready'
with open(os.environ['TEST_EVENTS'], 'a') as output:
    output.write(json.dumps({{'event': 'browser', 'url': url, 'pid': os.getpid()}}) + '\\n')
time.sleep(float(os.environ.get('TEST_OPEN_DELAY', '0')))
sys.exit(int(os.environ.get('TEST_OPEN_STATUS', '0')))
''')
        self.podman.chmod(0o755)
        self.opener.chmod(0o755)
        self.env = dict(os.environ, TEST_EVENTS=str(self.events))
        self.env.pop('PYTHONPATH', None)
        self.port = free_port()
        self.url = f'http://localhost:{self.port}/web/login?db=synthetic#view'

    @contextmanager
    def target(self, web=False):
        cls = http.server.ThreadingHTTPServer if web else socketserver.ThreadingTCPServer
        with cls(('127.0.0.1', 0), HTTP if web else Echo) as server:
            server.daemon_threads = True
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            self.env['TEST_TARGET_PORT'] = str(server.server_address[1])
            try:
                yield server
            finally:
                server.shutdown()
                worker.join(timeout=2)

    def command(self, url=None):
        return [sys.executable, '-I', '-B', str(SOURCE), str(self.podman),
                'test-workspace', str(self.opener), url or self.url]

    def calls(self):
        if not self.events.exists():
            return []
        return [json.loads(line) for line in self.events.read_text().splitlines()]

    def run_failure(self, url=None):
        result = subprocess.run(self.command(url), env=self.env, capture_output=True,
                                text=True, timeout=12)
        self.assertNotEqual(result.returncode, 0)
        return result

    @contextmanager
    def forwarder(self):
        child = subprocess.Popen(self.command(), env=self.env, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if any(call['event'] == 'browser' for call in self.calls()):
                    break
                if child.poll() is not None:
                    out, err = child.communicate(timeout=2)
                    self.fail(f'forwarder exited early: {out} {err}')
                time.sleep(0.02)
            else:
                self.fail('forwarder did not request browser launch')
            yield child
        finally:
            if child.poll() is None:
                child.send_signal(signal.SIGINT)
            out, err = child.communicate(timeout=6)
            self.assertEqual(child.returncode, 0, err)
            self.assertNotIn('db=synthetic', out + err)
            self.forwarder_output = out, err

    def assert_port_released(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(('127.0.0.1', self.port))

    def test_help_does_not_touch_podman_or_desktop(self):
        result = subprocess.run(self.command('--help'), env=self.env,
                                capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0)
        self.assertIn('Ctrl+C', result.stdout)
        self.assertIn('bare PORT opens http://127.0.0.1:PORT', result.stdout)
        self.assertFalse(self.events.exists())

    def test_invalid_bare_ports_are_refused_before_any_side_effect(self):
        for value in ('0', '65536', '-1', '+8071', '\uff18\uff10\uff17\uff11', '8071/private-marker'):
            with self.subTest(value=value):
                result = self.run_failure(value)
                self.assertNotIn('private-marker', result.stdout + result.stderr)
                self.assertFalse(self.events.exists())
        self.assert_port_released()

    def test_url_refusal_is_before_any_side_effect_and_does_not_echo_input(self):
        result = self.run_failure('http://example.com:8071/?token=private-marker')
        self.assertNotIn('private-marker', result.stdout + result.stderr)
        self.assertFalse(self.events.exists())

    def test_port_conflict_does_not_open_browser_or_probe_workspace(self):
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', self.port))
            occupied.listen()
            self.run_failure()
        self.assertFalse(self.events.exists())

    def test_missing_workspace_does_not_open_browser_and_releases_port(self):
        self.env['TEST_EXEC_FAIL'] = '1'
        self.env['TEST_TARGET_PORT'] = str(free_port())
        self.run_failure()
        self.assertEqual([call['event'] for call in self.calls()], ['probe'])
        self.assert_port_released()

    def test_missing_service_does_not_open_browser_and_releases_port(self):
        self.env['TEST_TARGET_PORT'] = str(free_port())
        self.run_failure()
        self.assertEqual([call['event'] for call in self.calls()], ['probe'])
        self.assert_port_released()

    def test_probe_has_a_deadline(self):
        self.env['TEST_PROBE_HANG'] = '1'
        self.env['TEST_TARGET_PORT'] = str(free_port())
        started = time.monotonic()
        self.run_failure()
        self.assertLess(time.monotonic() - started, 11)
        self.assertEqual([call['event'] for call in self.calls()], ['probe'])
        self.assert_port_released()

    def test_browser_can_load_the_page_before_launcher_returns(self):
        self.env['TEST_CHECK_HTTP'] = '1'
        with self.target(web=True), self.forwarder():
            events = self.calls()
            self.assertEqual(events[0]['event'], 'probe')
            self.assertEqual(events[-1]['event'], 'browser')
            self.assertEqual(events[-1]['url'], self.url.replace('localhost', '127.0.0.1'))
        self.assert_port_released()

    def test_bare_port_opens_and_forwards_the_http_root(self):
        self.url = str(self.port)
        self.env['TEST_CHECK_HTTP'] = '1'
        with self.target(web=True), self.forwarder():
            events = self.calls()
            self.assertEqual(events[0]['event'], 'probe')
            self.assertTrue(any(call['event'] == 'relay' for call in events))
            self.assertEqual(events[-1]['event'], 'browser')
            self.assertEqual(events[-1]['url'], f'http://127.0.0.1:{self.port}')
        self.assert_port_released()

    def test_duplex_binary_half_close_and_concurrent_clients(self):
        with self.target(), self.forwarder():
            clients = [socket.create_connection(('127.0.0.1', self.port), timeout=3)
                       for _ in range(4)]
            try:
                for index, client in enumerate(clients):
                    payload = bytes(range(256)) * 1024 + bytes([index])
                    client.sendall(payload)
                    received = b''
                    while len(received) < len(payload):
                        received += client.recv(65536)
                    self.assertEqual(received, payload)
                    client.shutdown(socket.SHUT_WR)
                    self.assertEqual(client.recv(65536), b'after-half-close')
                    self.assertEqual(client.recv(1), b'')
            finally:
                for client in clients:
                    client.close()
        self.assert_port_released()

    def test_terminal_desktop_environment_reaches_the_actual_opener(self):
        self.env.update(TEST_REQUIRE_DESKTOP='1', DISPLAY=':terminal-fixture',
                        WAYLAND_DISPLAY='wayland-terminal-fixture',
                        DBUS_SESSION_BUS_ADDRESS='unix:path=/fixture/desktop-bus')
        with self.target(), self.forwarder():
            self.assertTrue(any(call['event'] == 'browser' for call in self.calls()))

    def test_late_opener_failure_is_reported_while_forwarding_continues(self):
        self.env.update(TEST_OPEN_DELAY='1.4', TEST_OPEN_STATUS='17')
        with self.target(), self.forwarder() as child:
            time.sleep(1.7)
            self.assertIsNone(child.poll())
        self.assertIn('later exited with status 17', self.forwarder_output[1])
        self.assert_port_released()

    def test_slow_opener_is_detached_and_not_killed_by_the_startup_deadline(self):
        self.env['TEST_OPEN_DELAY'] = '1.6'
        with patch.dict(os.environ, self.env):
            status = preview.open_browser(str(self.opener), self.url)
            self.assertEqual(status, 'browser opener still running')
            pid = next(call['pid'] for call in self.calls() if call['event'] == 'browser')
            self.assertEqual(os.getpgid(pid), pid)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.02)
            else:
                self.fail('owned fixture opener was not reaped')

    def test_browser_failure_leaves_no_listener(self):
        self.env['TEST_OPEN_STATUS'] = '17'
        with self.target():
            self.run_failure()
        self.assert_port_released()

    def test_ctrl_c_reaps_active_relays_without_lifecycle_calls(self):
        with self.target(), socket.socket() as client:
            with self.forwarder():
                client.connect(('127.0.0.1', self.port))
                client.sendall(b'held connection')
                self.assertEqual(client.recv(1024), b'held connection')
                relays = [call['pid'] for call in self.calls() if call['event'] == 'relay']
                self.assertTrue(relays)
            for pid in relays:
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)
            client.settimeout(2)
            self.assertEqual(client.recv(1), b'')
        self.assert_port_released()


if __name__ == '__main__':
    unittest.main()
