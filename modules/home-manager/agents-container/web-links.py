#!/usr/bin/env python3
"""HTTP(S)-only host browser bridge; no files, guest commands or display sockets."""
from collections import deque
import json
import os
import socket
import socketserver
import stat
import struct
import subprocess
import sys
import time
from urllib.parse import urlsplit

SOCKET = "/run/agents-web-links/socket"
MAX_URL = 8192
MAX_REQUEST = 16384
OPEN_TIMEOUT = 5
MAX_PER_MINUTE = 20


def validate_url(url):
    if not isinstance(url, str) or not url or len(url.encode("utf-8")) > MAX_URL:
        raise ValueError("expected an HTTP(S) URL of at most 8192 bytes")
    # urlsplit otherwise strips some controls. Reject them before parsing.
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url):
        raise ValueError("URL contains whitespace or control characters")
    try:
        parts = urlsplit(url)
        valid = (parts.scheme in ("http", "https") and parts.hostname
                 and parts.username is None and parts.password is None)
        parts.port  # Validate an explicit port, including IPv6 forms.
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("only absolute HTTP(S) URLs without embedded credentials are allowed")
    return url


class WebLinkServer(socketserver.UnixStreamServer):
    def __init__(self, path, runner, opener):
        self.runner = runner
        self.opener = opener
        self.attempts = deque()
        super().__init__(path, Handler)
        os.chmod(path, 0o600)

    def open_url(self, request):
        if not isinstance(request, dict) or set(request) != {"url"}:
            raise ValueError("expected only a URL")
        url = validate_url(request["url"])
        now = time.monotonic()
        while self.attempts and now - self.attempts[0] >= 60:
            self.attempts.popleft()
        if (self.attempts and now - self.attempts[-1] < 1) or len(self.attempts) >= MAX_PER_MINUTE:
            raise ValueError("host browser request rate limit reached")
        self.attempts.append(now)
        try:
            # A transient user service keeps the browser outside this broker's
            # cgroup, so restarting the broker cannot close a newly opened browser.
            # The executable and every option are host-selected, never guest input.
            subprocess.run(
                [self.runner, "--user", "--collect", "--quiet", "--property=Type=exec",
                 "--property=StandardOutput=null", "--property=StandardError=null",
                 "--description=Agents web link", "--", self.opener, url],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=OPEN_TIMEOUT, check=True,
            )
        except (OSError, subprocess.SubprocessError):
            raise ValueError("host browser launcher failed") from None
        # Accepted by the host launcher, not proof that a browser window appeared.
        return {"ok": True}


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(0.5)
        try:
            credentials = self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            _, uid, _ = struct.unpack("3i", credentials)
            if uid != os.geteuid():
                raise ValueError("browser requests require the host user's UID")
            data = self.rfile.readline(MAX_REQUEST + 1)
            if len(data) > MAX_REQUEST or not data.endswith(b"\n"):
                raise ValueError("invalid browser request size or framing")
            try:
                request = json.loads(data)
            except (ValueError, RecursionError):
                raise ValueError("invalid browser request JSON") from None
            response = self.server.open_url(request)
        except (OSError, ValueError):
            # Never include an address or an exception containing request data in logs.
            response = {"ok": False, "error": "host browser request rejected or launcher unavailable"}
        try:
            self.wfile.write(json.dumps(response).encode() + b"\n")
        except OSError:
            pass


def open_on_host(url, path=SOCKET):
    validate_url(url)
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(OPEN_TIMEOUT + 2)
        client.connect(path)
        client.sendall(json.dumps({"url": url}, ensure_ascii=False).encode() + b"\n")
        with client.makefile("rb") as stream:
            data = stream.readline(4097)
        if len(data) > 4096 or not data.endswith(b"\n"):
            raise ValueError("invalid host browser response")
        response = json.loads(data)
    if not isinstance(response, dict) or response.get("ok") is not True:
        raise ValueError("host browser request rejected or launcher unavailable")


def serve(path, runner, opener):
    os.umask(0o077)
    if os.path.lexists(path):
        existing = os.lstat(path)
        if not stat.S_ISSOCK(existing.st_mode) or existing.st_uid != os.geteuid():
            raise ValueError("refusing to replace a non-socket or foreign socket")
        os.unlink(path)
    try:
        with WebLinkServer(path, runner, opener) as server:
            server.serve_forever(poll_interval=0.1)
    finally:
        if os.path.lexists(path):
            os.unlink(path)


def main(args):
    try:
        if len(args) == 4 and args[0] == "serve":
            serve(*args[1:])
        elif len(args) == 2 and args[0] == "open":
            open_on_host(args[1])
        else:
            raise ValueError("usage: xdg-open <http(s)-url>")
    except (OSError, ValueError):
        print("agents web links: request failed; check agents-web-links.service on the host", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
