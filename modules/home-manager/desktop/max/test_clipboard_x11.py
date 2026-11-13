"""Optional integration tests using isolated Xvfb displays and real xclip.

The fake gdbus represents trusted host focus, not information from the guest.
"""

import base64
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
import unittest

import clipboard


@unittest.skipUnless(shutil.which("Xvfb") and shutil.which("xclip"), "requires Xvfb and xclip")
class X11BridgeTests(unittest.TestCase):
    def setUp(self):
        self.servers = []
        self.peers = []
        self.writers = []
        self.environments = []
        self.epoch = 0
        self.addCleanup(self.cleanup)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.focus = root / "focus"
        self.focus.write_text("(false, uint32 1)\n")
        gdbus = root / "gdbus"
        gdbus.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nprint(Path({str(self.focus)!r}).read_text(), end='')\n")
        gdbus.chmod(0o755)
        for index in range(2):
            read_fd, write_fd = os.pipe()
            server = subprocess.Popen(
                ["Xvfb", "-displayfd", str(write_fd), "-screen", "0", "640x480x24", "-nolisten", "tcp"],
                pass_fds=(write_fd,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self.servers.append(server)
            os.close(write_fd)
            with os.fdopen(read_fd) as stream:
                self.assertTrue(select.select([stream], [], [], 5)[0], "Xvfb did not start")
                number = stream.readline().strip()
            self.assertTrue(number, "Xvfb startup failed")
            env = dict(os.environ, DISPLAY=":" + number, MAX_FOCUS_PID="123",
                       PATH=str(root) + ":" + os.environ["PATH"])
            env.pop("XAUTHORITY", None)
            self.environments.append(env)
            if index == 0:
                command = [sys.executable, str(Path(clipboard.__file__).resolve()), "host"]
            else:
                display_file = root / "guest-display"
                display_file.write_text(env["DISPLAY"])
                command = [sys.executable, "-c",
                           f"import sys; sys.path.insert(0, {str(Path(clipboard.__file__).resolve().parent)!r}); "
                           f"import clipboard; clipboard.endpoint(True, {str(display_file)!r})"]
            peer = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
            self.peers.append(peer)
            self.assertEqual(self.frame(peer), {"ready": True})

    def cleanup(self):
        for peer in self.peers:
            peer.stdin.close()
            try:
                peer.wait(timeout=5)
            except subprocess.TimeoutExpired:
                clipboard.stop(peer)
            peer.stdout.close()
        for process in self.writers + self.servers:
            clipboard.stop(process)

    def send(self, index, frame):
        self.peers[index].stdin.write(json.dumps(frame).encode() + b"\n")
        self.peers[index].stdin.flush()

    def frame(self, peer):
        self.assertTrue(select.select([peer.stdout], [], [], 5)[0], "missing clipboard event")
        return json.loads(peer.stdout.readline())

    def silence(self, index):
        self.assertFalse(select.select([self.peers[index].stdout], [], [], 0.5)[0], "unexpected clipboard event")

    def activate(self, expected=b""):
        self.epoch += 1
        self.focus.write_text(f"(true, uint32 {self.epoch})\n")
        control = {"active": True, "epoch": self.epoch, "focus_serial": self.epoch}
        # Guest waits for the host baseline, and must not export its stale data.
        self.send(1, control)
        self.send(0, control)
        frame = self.frame(self.peers[0])
        self.assertEqual(base64.b64decode(frame["data"]), expected)
        self.send(1, frame)
        self.silence(1)

    def deactivate(self):
        self.epoch += 1
        self.focus.write_text(f"(false, uint32 {self.epoch})\n")
        control = {"active": False, "epoch": self.epoch, "focus_serial": None}
        self.send(1, control)
        self.send(0, control)
        self.silence(1)

    def publish(self, source, target, data):
        fd = os.memfd_create("test-clipboard")
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(data)
            os.lseek(fd, 0, 0)
            self.writers.append(subprocess.Popen(
                ["xclip", "-selection", "clipboard", "-target", target, "-quiet"],
                stdin=fd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=self.environments[source],
            ))
        finally:
            os.close(fd)

    def read(self, index, target="UTF8_STRING"):
        return subprocess.run(
            ["xclip", "-selection", "clipboard", "-out", "-target", target],
            env=self.environments[index], capture_output=True, timeout=5, check=True,
        ).stdout

    def transfer(self, source, destination, target, data):
        self.publish(source, target, data)
        frame = self.frame(self.peers[source])
        self.assertEqual(frame["target"], target)
        self.assertEqual(frame["epoch"], self.epoch)
        self.assertEqual(base64.b64decode(frame["data"]), data)
        self.send(destination, frame)
        self.silence(destination)
        self.assertEqual(self.read(destination, target), data)

    def test_bidirectional_text_images_and_no_echo(self):
        self.activate()
        self.transfer(0, 1, "UTF8_STRING", "Привет из GNOME\n\n".encode())
        self.transfer(1, 0, "UTF8_STRING", "Ответ из MAX\n".encode())
        # Larger than an X11 property: exercises xclip's INCR transfer handling.
        self.transfer(0, 1, "image/png", b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4096)
        self.transfer(1, 0, "UTF8_STRING", b"")

    def test_guest_stale_clipboard_does_not_override_host_on_focus_gain(self):
        self.publish(1, "UTF8_STRING", b"stale guest clipboard")
        self.silence(1)
        self.activate()
        self.assertEqual(self.read(1), b"")

    def test_inactive_at_startup_and_refresh_on_focus_gain(self):
        self.publish(0, "UTF8_STRING", b"host secret")
        self.silence(0)
        self.activate(b"host secret")
        self.assertEqual(self.read(1), b"host secret")

    def test_focus_loss_clears_guest_but_preserves_host_and_rejects_old_frames(self):
        self.activate()
        self.transfer(0, 1, "UTF8_STRING", b"old secret")
        old_epoch = self.epoch
        self.deactivate()
        self.assertEqual(self.read(1), b"")
        self.assertEqual(self.read(0), b"old secret")
        self.publish(0, "UTF8_STRING", b"new secret")
        self.silence(0)
        self.activate(b"new secret")
        self.send(1, {"target": "UTF8_STRING", "data": "c3RhbGU=", "epoch": old_epoch})
        self.silence(1)
        self.assertEqual(self.read(1), b"new secret")

    def test_host_rechecks_focus_before_control_arrives(self):
        self.activate()
        self.focus.write_text("(false, uint32 99)\n")
        self.publish(0, "UTF8_STRING", b"background secret")
        self.silence(0)
        self.send(0, {"target": "UTF8_STRING", "data": "bWFsaWNpb3Vz", "epoch": self.epoch})
        self.silence(0)
        self.assertEqual(self.read(0), b"background secret")


if __name__ == "__main__":
    unittest.main()
