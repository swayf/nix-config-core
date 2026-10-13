#!/usr/bin/env python3
"""Real private D-Bus + session/exec scripts; mock only the Wayland handshake."""
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time
import unittest


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir(mode=0o700)
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.bind(str(self.root / "wayland"))
        self.addCleanup(self.sock.close)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        mock = self.bin / "wayland-info"
        mock.write_text('#!/bin/sh\ntest -z "${DISPLAY:-}" || exit 41\n'
                        'test "$WAYLAND_DISPLAY" = "$TEST_WAYLAND" || exit 42\n'
                        'exit "${TEST_HANDSHAKE_STATUS:-0}"\n')
        mock.chmod(0o755)
        self.env = dict(os.environ, XDG_RUNTIME_DIR=str(self.runtime),
                        WAYLAND_DISPLAY=str(self.root / "wayland"),
                        TEST_WAYLAND=str(self.root / "wayland"),
                        DISPLAY=":host", DBUS_SESSION_BUS_ADDRESS="unix:path=/missing-host-bus",
                        PATH=f"{self.bin}:{os.environ['PATH']}")

    def start(self, env=None):
        self.process = subprocess.Popen(
            [os.environ["TEST_DBUS_RUN"], f"--config-file={os.environ['TEST_DBUS_CONFIG']}",
             "--", os.environ["TEST_SESSION"]],
            env=env or self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True)
        self.addCleanup(self.stop)
        return self.process

    def stop(self):
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
        self.process.communicate(timeout=10)

    def ready(self):
        for _ in range(100):
            if (self.runtime / "session-ready").exists():
                return
            if self.process.poll() is not None:
                self.fail(str(self.process.communicate()))
            time.sleep(0.05)
        self.fail("Session readiness timed out")

    def execute(self, *args):
        return subprocess.run([os.environ["TEST_EXEC"], *args], env=self.env,
                              capture_output=True, text=True, timeout=10)

    def test_private_bus_direct_socket_and_keepalive(self):
        self.start()
        self.ready()
        bus = (self.runtime / "dbus-address").read_text().strip()
        self.assertNotEqual(bus, self.env["DBUS_SESSION_BUS_ADDRESS"])
        self.assertEqual((self.runtime / "dbus-address").stat().st_mode & 0o777, 0o600)
        result = self.execute("bash", "-c", 'printf "%s\\n" "$WAYLAND_DISPLAY" '
                              '"${DISPLAY-unset}" "$DBUS_SESSION_BUS_ADDRESS"', "arg with spaces")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [self.env["WAYLAND_DISPLAY"], "unset", bus])
        result = self.execute(os.environ["TEST_DBUS_SEND"], "--session", "--print-reply",
                              "--dest=org.freedesktop.DBus", "/org/freedesktop/DBus",
                              "org.freedesktop.DBus.ListNames")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("org.freedesktop.DBus", result.stdout)
        result = self.execute("printf", "%s\\n", "space separated", "$(touch /tmp/never-evaluate)", "")
        self.assertEqual(result.stdout.splitlines(), ["space separated", "$(touch /tmp/never-evaluate)", ""])
        self.assertIsNone(self.process.poll(), "Closing an application must not end the session")
        self.assertFalse((self.runtime / "wayland-gui").exists())
        self.assertFalse((self.runtime / "x-display").exists())

    def test_exec_requires_ready_session(self):
        result = self.execute("printf", "should not execute")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_dead_wayland_does_not_publish_readiness(self):
        process = self.start(dict(self.env, TEST_HANDSHAKE_STATUS="19"))
        process.communicate(timeout=10)
        self.assertEqual(process.returncode, 19)
        self.assertFalse((self.runtime / "session-ready").exists())
        self.assertFalse((self.runtime / "dbus-address").exists())

    def test_missing_socket(self):
        process = self.start(dict(self.env, WAYLAND_DISPLAY=str(self.root / "missing")))
        _, stderr = process.communicate(timeout=10)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("socket is unavailable", stderr)
        self.assertFalse((self.runtime / "session-ready").exists())


if __name__ == "__main__":
    unittest.main()
