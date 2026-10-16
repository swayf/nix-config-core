#!/usr/bin/env python3
"""Headless regression for fullscreen X11 windows; needs Weston 13 and python-xlib."""
import importlib.util
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

CONFIG = Path(__file__).with_name("weston.ini")


def photo_window_client():
    from Xlib import X, Xatom, display

    connection = display.Display()
    screen = connection.screen()
    main = screen.root.create_window(
        20, 20, 640, 480, 0, X.CopyFromParent, X.InputOutput,
        X.CopyFromParent, background_pixel=screen.white_pixel,
    )
    main.set_wm_name("MAX test main window")
    main.map()
    connection.sync()
    time.sleep(0.2)
    photo = screen.root.create_window(
        0, 0, 1100, 800, 0, X.CopyFromParent, X.InputOutput,
        X.CopyFromParent, background_pixel=screen.white_pixel,
        override_redirect=int(os.environ["TEST_OVERRIDE_REDIRECT"]),
    )
    photo.set_wm_name("MAX test fullscreen photo")
    photo.change_property(
        connection.intern_atom("_NET_WM_STATE"), Xatom.ATOM, 32,
        [connection.intern_atom("_NET_WM_STATE_FULLSCREEN")],
    )
    photo.map()
    connection.sync()
    time.sleep(0.5)
    connection.sync()
    photo.destroy()
    connection.sync()
    time.sleep(0.5)
    connection.sync()
    Path(os.environ["XDG_RUNTIME_DIR"], "client-done").touch()
    connection.close()


@unittest.skipUnless(shutil.which("weston") and shutil.which("Xwayland")
                     and importlib.util.find_spec("Xlib"),
                     "requires Weston, Xwayland and python-xlib")
class WestonWindowTests(unittest.TestCase):
    def test_fullscreen_photo_windows(self):
        for override_redirect in (False, True):
            with self.subTest(override_redirect=override_redirect), tempfile.TemporaryDirectory() as tmp:
                runtime = Path(tmp)
                helper = runtime / "client.sh"
                helper.write_text("#!/bin/sh\nexec " + shlex.join([
                    sys.executable, str(Path(__file__).resolve()), "--client",
                ]) + "\n")
                helper.chmod(0o700)
                config = runtime / "weston.ini"
                text = re.sub(r"(?m)^path=.*$", "path=" + str(helper), CONFIG.read_text())
                config.write_text(text.replace("watch=true", "watch=false"))
                env = dict(os.environ, XDG_RUNTIME_DIR=tmp,
                           TEST_OVERRIDE_REDIRECT=str(int(override_redirect)))
                # Never connect to the host's graphical session.
                env.pop("DISPLAY", None)
                env.pop("WAYLAND_DISPLAY", None)
                with (runtime / "weston.log").open("w+") as log:
                    process = subprocess.Popen([
                        "weston", "--backend=headless-backend.so", "--use-pixman",
                        "--width=1100", "--height=800", "--idle-time=0",
                        "--config=" + str(config),
                    ], env=env, stdout=log, stderr=log)
                    try:
                        deadline = time.monotonic() + 15
                        while (process.poll() is None
                               and not (runtime / "client-done").exists()
                               and time.monotonic() < deadline):
                            time.sleep(0.05)
                        log.seek(0)
                        output = log.read()
                        self.assertIsNone(process.poll(), output)
                        self.assertTrue((runtime / "client-done").exists(), output)
                    finally:
                        if process.poll() is None:
                            process.terminate()
                        process.wait(timeout=5)


if __name__ == "__main__":
    if sys.argv[1:] == ["--client"]:
        photo_window_client()
    else:
        unittest.main()
