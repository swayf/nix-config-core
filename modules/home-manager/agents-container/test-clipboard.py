#!/usr/bin/env python3
import base64
import importlib.util
import os
from pathlib import Path
import socket
import socketserver
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("clipboard", Path(__file__).with_name("clipboard.py"))
clipboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clipboard)


class ClipboardTests(unittest.TestCase):
    def test_cli_translation(self):
        self.assertEqual(clipboard.command("xclip", ["-selection", "clipboard", "-o", "-t", "image/png"], True),
                         (["wl-paste", "--type", "image/png", "--no-newline"], True, False))
        self.assertEqual(clipboard.command("xclip", ["-selection", "clipboard", "-in"], True)[0],
                         ["wl-copy", "--type", "text/plain;charset=utf-8"])
        self.assertEqual(clipboard.command("xclip", ["-selection", "clipboard", "-out"], True)[0],
                         ["wl-paste", "--type", "text/plain;charset=utf-8", "--no-newline"])
        self.assertEqual(clipboard.command("wl-copy", [], False)[0],
                         ["xclip", "-selection", "clipboard", "-in"])
        self.assertEqual(clipboard.command("wl-paste", ["-n"], False),
                         (["xclip", "-selection", "clipboard", "-out"], True, True))

    def test_format_listing_translation(self):
        # Exact probe used by Pi's X11 clipboard image reader.
        for args in (["-selection", "clipboard", "-t", "TARGETS", "-o"],
                     ["-sel", "clipboard", "-out", "-target", "TARGETS"]):
            self.assertEqual(clipboard.command("xclip", args, True),
                             (["wl-paste", "--no-newline", "--list-types"], True, False))
            self.assertEqual(clipboard.command("xclip", args, False),
                             (["xclip", "-selection", "clipboard", "-out", "-target", "TARGETS"], True, False))
        self.assertEqual(clipboard.command("wl-paste", ["--list-types"], False)[0],
                         ["xclip", "-selection", "clipboard", "-out", "-target", "TARGETS"])

    def test_pi_image_probe_and_read(self):
        payload = b"\x89PNG\r\n\x1a\n\x00\xff\n"
        for wayland in (True, False):
            with self.subTest(wayland=wayland):
                calls = []

                def run(cmd, **kwargs):
                    calls.append(cmd)
                    listing = "--list-types" in cmd or "TARGETS" in cmd
                    return subprocess.CompletedProcess(cmd, 0, stdout=(
                        b"text/plain\nimage/png\nimage/jpeg\n" if listing else payload
                    ))

                with tempfile.TemporaryDirectory() as directory, \
                     patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"} if wayland else {}, clear=True), \
                     patch.object(clipboard.subprocess, "run", side_effect=run):
                    with socketserver.ThreadingUnixStreamServer(directory + "/socket", clipboard.Handler) as server:
                        thread = threading.Thread(target=server.serve_forever)
                        thread.start()
                        try:
                            def request(target):
                                with socket.socket(socket.AF_UNIX) as sock:
                                    sock.connect(directory + "/socket")
                                    clipboard.send(sock, {"tool": "xclip", "args": [
                                        "-selection", "clipboard", "-t", target, "-o"
                                    ]})
                                    result = clipboard.receive(sock)
                                self.assertEqual(result["code"], 0)
                                return base64.b64decode(result["data"])

                            types = request("TARGETS").decode().splitlines()
                            self.assertIn("image/png", types)
                            self.assertEqual(request("image/png"), payload)
                            if wayland:
                                self.assertEqual(calls, [
                                    ["wl-paste", "--no-newline", "--list-types"],
                                    ["wl-paste", "--type", "image/png", "--no-newline"],
                                ])
                            else:
                                self.assertEqual(calls, [
                                    ["xclip", "-selection", "clipboard", "-out", "-target", "TARGETS"],
                                    ["xclip", "-selection", "clipboard", "-out", "-target", "image/png"],
                                ])
                        finally:
                            server.shutdown()
                            thread.join()

    def test_reject_watch_execution_and_files(self):
        for args in (["--watch", "sh"], ["/etc/passwd"], ["-selection", "secondary"]):
            with self.assertRaises(ValueError):
                clipboard.command("xclip", args, True)

    def test_backend_error_is_returned(self):
        def run(cmd, stdin, stderr, **kwargs):
            stderr.write(b"xdg-mime: no method available\n")
            return subprocess.CompletedProcess(cmd, 1)

        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}), \
             patch.object(clipboard.subprocess, "run", side_effect=run):
            with socketserver.ThreadingUnixStreamServer(directory + "/socket", clipboard.Handler) as server:
                thread = threading.Thread(target=server.serve_forever)
                thread.start()
                try:
                    with socket.socket(socket.AF_UNIX) as sock:
                        sock.connect(directory + "/socket")
                        clipboard.send(sock, {"tool": "wl-copy", "args": [], "data": ""})
                        response = clipboard.receive(sock)
                    self.assertEqual(response["code"], 1)
                    self.assertIn("xdg-mime: no method available", response["error"])
                    self.assertIn("wl-copy exited with code 1", response["error"])
                finally:
                    server.shutdown()
                    thread.join()

    def test_binary_bidirectional_bridge(self):
        payload = b"\x89PNG\x00\xff\n"
        contents = b""

        def run(cmd, stdin, **kwargs):
            nonlocal contents
            if cmd[0] == "wl-copy":
                contents = stdin.read()
                return subprocess.CompletedProcess(cmd, 0)
            return subprocess.CompletedProcess(cmd, 0, stdout=contents)

        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}), \
             patch.object(clipboard.subprocess, "run", side_effect=run):
            with socketserver.ThreadingUnixStreamServer(directory + "/socket", clipboard.Handler) as server:
                thread = threading.Thread(target=server.serve_forever)
                thread.start()
                try:
                    def request(tool, data=b""):
                        with socket.socket(socket.AF_UNIX) as sock:
                            sock.connect(directory + "/socket")
                            clipboard.send(sock, {"tool": tool, "args": ["--type", "image/png"],
                                                  "data": base64.b64encode(data).decode()})
                            return clipboard.receive(sock)
                    self.assertEqual(request("wl-copy", payload)["code"], 0)
                    self.assertEqual(contents, payload)
                    self.assertEqual(base64.b64decode(request("wl-paste")["data"]), payload)
                    # A change originating on the host is returned to the guest.
                    contents = b"host text\n"
                    self.assertEqual(base64.b64decode(request("wl-paste")["data"]), contents)
                finally:
                    server.shutdown()
                    thread.join()


if __name__ == "__main__":
    unittest.main()
