#!/usr/bin/env python3
"""Explicit stdin-only userspace WARP probe; no registration, storage or deployment."""
import argparse
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time


def probe(settings, renderer):
    renderer.validate_warp(settings)
    with tempfile.TemporaryDirectory(prefix="xray-warp-probe.") as directory:
        root = Path(directory)
        root.chmod(0o700)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        config = {"log": {"loglevel": "none"},
                  "inbounds": [{"listen": "127.0.0.1", "port": port, "protocol": "socks",
                                "settings": {"auth": "noauth", "udp": False}}],
                  "outbounds": [{"tag": "warp", "protocol": "wireguard", "settings": settings}]}
        path = root / "config.json"
        path.write_text(json.dumps(config))
        path.chmod(0o600)
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "XRAY_LOCATION_ASSET", "SSL_CERT_FILE")}
        checked = subprocess.run(["xray", "run", "-test", "-config", str(path)], env=env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        if checked.returncode:
            raise ValueError("Xray rejected WARP settings")
        process = subprocess.Popen(["xray", "run", "-config", str(path)], env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 5
            while True:
                if process.poll() is not None:
                    raise ValueError("probe exited before listening")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise ValueError("probe startup timeout")
                    time.sleep(0.05)
            curl = subprocess.run(["curl", "--disable", "--silent", "--show-error", "--fail",
                                   "--noproxy", "", "--proxy", f"socks5h://127.0.0.1:{port}",
                                   "--connect-timeout", "10", "--max-time", "25",
                                   "https://www.cloudflare.com/cdn-cgi/trace"],
                                  env=env, capture_output=True, timeout=30)
            if curl.returncode or process.poll() is not None:
                raise ValueError("WARP probe failed")
            trace = dict(line.split("=", 1) for line in curl.stdout.decode().splitlines() if "=" in line)
            ipaddress.ip_address(trace["ip"])
            if trace.get("warp") not in ("on", "plus"):
                raise ValueError("Cloudflare did not confirm WARP")
            return {k: trace.get(k) for k in ("ip", "warp", "loc", "colo")}
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--renderer", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    def interrupted(signum, _frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        spec = importlib.util.spec_from_file_location("renderer", args.renderer)
        renderer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(renderer)
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("input too large")
        print(json.dumps(probe(json.loads(raw), renderer)))
        return 0
    except (ValueError, KeyError, TypeError, OSError, AssertionError, subprocess.TimeoutExpired):
        print("WARP probe failed; check private input and tools. No deploy performed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
