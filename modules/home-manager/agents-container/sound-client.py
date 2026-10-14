#!/usr/bin/env python3
"""Test a named event via the existing playback-only bridge, on host or guest."""
import json
import os
from pathlib import Path
import socket
import sys


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("done", "request"):
        raise SystemExit("usage: sound-client.py done|request")
    guest = Path("/run/agents-sound/socket")
    path = guest if guest.exists() else Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "agents-sound/socket"
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(2)
        client.connect(str(path))
        client.sendall(json.dumps({"event": sys.argv[1]}).encode() + b"\n")
        response = json.loads(client.makefile("rb").readline(4097))
    print(json.dumps(response, ensure_ascii=False))
    if response.get("ok") is not True or response.get("skipped"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
