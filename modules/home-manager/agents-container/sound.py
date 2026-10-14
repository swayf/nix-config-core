#!/usr/bin/env python3
"""Playback-only host broker: guests send event names, never paths or commands."""
import json
import os
from pathlib import Path
import re
import socketserver
import subprocess
import sys
import time

EVENT = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
MAX_REQUEST = 256
MAX_PLAY_SECONDS = 15


class SoundServer(socketserver.UnixStreamServer):
    def __init__(self, path, config, player):
        self.config = Path(config)
        self.player = player
        self.playback = None
        self.started = 0.0
        super().__init__(path, Handler)

    def service_actions(self):
        if self.playback is None:
            return
        code = self.playback.poll()
        if code is None and time.monotonic() - self.started >= MAX_PLAY_SECONDS:
            self.playback.kill()
            code = self.playback.wait()
        if code is not None:
            if code:
                print(f"agents sound: player exited with code {code}", file=sys.stderr, flush=True)
            self.playback = None

    def play(self, request):
        if not isinstance(request, dict) or set(request) != {"event"}:
            raise ValueError("expected only an event name")
        event = request["event"]
        if not isinstance(event, str) or not EVENT.fullmatch(event):
            raise ValueError("invalid sound event")
        # Reload the host-owned map on each request; no rebuild for new sounds.
        try:
            with self.config.open() as stream:
                mapping = json.loads(stream.read(65537))
        except (OSError, ValueError):
            raise ValueError("host sound configuration is missing or invalid") from None
        if not isinstance(mapping, dict):
            raise ValueError("host sound configuration must be an object")
        filename = mapping.get(event)
        if not isinstance(filename, str) or not filename:
            raise ValueError(f"sound '{event}' is not configured on the host")
        path = Path(filename).expanduser()
        if not path.is_absolute() or not path.is_file():
            raise ValueError(f"sound '{event}' needs an existing absolute host file path")
        self.service_actions()
        # Bound process count and suppress overlapping/rate-spammed alerts.
        if self.playback is not None or time.monotonic() - self.started < 0.25:
            return {"ok": True, "skipped": True}
        self.playback = subprocess.Popen(
            [self.player, "--client-name=Pi sounds", "--", str(path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.started = time.monotonic()
        return {"ok": True}

    def server_close(self):
        if self.playback is not None:
            if self.playback.poll() is None:
                self.playback.kill()
            self.playback.wait()
            self.playback = None
        super().server_close()


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(0.5)
        try:
            data = self.rfile.readline(MAX_REQUEST + 1)
            if len(data) > MAX_REQUEST or not data.endswith(b"\n"):
                raise ValueError("invalid sound request size or framing")
            response = self.server.play(json.loads(data))
        except (OSError, ValueError) as error:
            response = {"ok": False, "error": str(error)}
        try:
            self.wfile.write(json.dumps(response).encode() + b"\n")
        except OSError:
            pass  # A disconnected client must not stop the broker.


def main():
    path, config, player = sys.argv[1:]
    os.umask(0o077)
    if os.path.lexists(path):
        os.unlink(path)
    try:
        with SoundServer(path, config, player) as server:
            server.serve_forever(poll_interval=0.1)
    finally:
        if os.path.lexists(path):
            os.unlink(path)


if __name__ == "__main__":
    main()
