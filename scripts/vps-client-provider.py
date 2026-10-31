#!/usr/bin/env python3
"""Client-only Mihomo file projection. No SSH, API calls, key discovery or stdout.

The caller pins bindings. Successful managed apply publishes credentials; an
explicit local import instead requires an isolated client connectivity probe.
Files live outside Git/store, owned by the invoking desktop user. Mihomo watches
file providers. A nonmatching reject node is intentional: an empty provider is
invalid in Mihomo and would retain the previous successfully parsed contents.
"""
import base64
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
import socket
import sys
import tempfile
import time
import uuid

UNAVAILABLE = {"proxies": [{"name": "__unavailable__", "type": "reject"}]}


def bindings():
    source = os.environ.get("VPS_CLIENT_PROVIDERS")
    if not source:
        return {}
    path = Path(source)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("Invalid pinned client bindings")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("Invalid client bindings")
    destinations = set()
    for name, binding in value.items():
        if (not re.fullmatch(r"[a-z][a-z0-9-]*", name)
                or not isinstance(binding, dict)
                or set(binding) != {"host", "client", "nodeName", "stateFile"}):
            raise ValueError("Invalid client binding fields")
        for key in ("host", "client"):
            if not isinstance(binding[key], str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", binding[key]):
                raise ValueError("Invalid managed host/client")
        if (not isinstance(binding["nodeName"], str)
                or not re.fullmatch(r"[A-Za-z0-9_-]+", binding["nodeName"])
                or binding["nodeName"] == "__unavailable__"):
            raise ValueError("Invalid managed node label")
        file = binding["stateFile"]
        if (not isinstance(file, str) or PurePosixPath(file).is_absolute()
                or any(p in {"", ".", ".."} for p in file.split("/"))
                or not file.startswith("mihomo/vps/")):
            raise ValueError("Managed provider must be beneath mihomo/vps in XDG state")
        if file in destinations:
            raise ValueError("Duplicate managed provider destination")
        destinations.add(file)
    return value


def project(binding, public, material, address):
    """Allowlist, never copy material: private keys/other clients stay in memory."""
    import ipaddress
    try:
        if binding["client"] not in public["clients"]:
            return UNAVAILABLE
        endpoint = str(ipaddress.IPv4Address(address))
        port = public.get("port", 443)
        sni = public["serverName"]
        client = material["clients"][binding["client"]]["uuid"]
        key, sid = material["public_key"], material["short_id"]
        if (type(port) is not int or not 1 <= port <= 65535
                or not isinstance(sni, str) or not re.fullmatch(r"[A-Za-z0-9.-]+", sni)
                or str(uuid.UUID(client)) != client
                or not re.fullmatch(r"[A-Za-z0-9_-]{43}", key)
                or len(base64.urlsafe_b64decode(key + "=")) != 32
                or not re.fullmatch(r"(?:[0-9a-f]{2}){1,8}", sid)):
            raise ValueError()
        return {"proxies": [{
            "name": binding["nodeName"], "type": "vless", "server": endpoint,
            "port": port, "uuid": client, "network": "tcp", "udp": True,
            "tls": True, "flow": "xtls-rprx-vision", "servername": sni,
            "client-fingerprint": "chrome",
            "reality-opts": {"public-key": key, "short-id": sid},
        }]}
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError("Managed client projection rejected; details withheld") from None


def destination(binding, repo):
    root = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    path = root / binding["stateFile"]
    if (not root.is_absolute() or ".." in path.parts
            or path.is_relative_to(repo) or path.is_relative_to("/nix/store")):
        raise ValueError("Unsafe managed provider destination")
    return path


def probe(value):
    """Probe only the selected client, without touching live Mihomo or the VPS.

    The private, short-lived Mihomo has no DIRECT/fallback route or controller.
    Its config contains only the client projection; raw process output is never
    exposed. This tests TCP/HTTPS, not live watcher consumption, UDP or revocation.
    """
    nodes = value["proxies"]
    if len(nodes) != 1 or nodes[0]["type"] != "vless":
        raise ValueError("An enabled VLESS client is required for local import")
    binary = os.environ.get("VPS_MIHOMO_BINARY")
    if not binary or not Path(binary).is_absolute():
        raise ValueError("Pinned Mihomo probe binary required")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    # Ignore TMPDIR: plaintext must not accidentally land in a checkout/store.
    with tempfile.TemporaryDirectory(prefix="vps-client-probe-", dir="/tmp") as directory:
        path = Path(directory) / "config.json"
        write(path, {
            "mixed-port": port, "bind-address": "127.0.0.1", "allow-lan": False,
            "mode": "rule", "log-level": "silent", "ipv6": False,
            "geo-auto-update": False, "find-process-mode": "off",
            "profile": {"store-selected": False, "store-fake-ip": False},
            "dns": {"enable": False},
            "proxies": [nodes[0] | {"name": "managed-probe"}],
            "rules": ["MATCH,managed-probe"],
        })
        env = {key: os.environ[key] for key in ("PATH", "SSL_CERT_FILE", "SSL_CERT_DIR") if key in os.environ}
        env["HOME"] = directory
        process = subprocess.Popen([binary, "-d", directory, "-f", str(path)], env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                if process.poll() is not None:
                    raise ValueError("Isolated client exited")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                raise ValueError("Isolated client startup timed out")
            result = subprocess.run([
                "curl", "--disable", "--silent", "--fail", "--noproxy", "",
                "--proxy", f"socks5h://127.0.0.1:{port}", "--proto", "=https",
                "--connect-timeout", "5", "--max-time", "15", "--output", "/dev/null",
                "--write-out", "%{http_code}", "https://cp.cloudflare.com/generate_204",
            ], env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20)
            if result.returncode or result.stdout != b"204" or process.poll() is not None:
                raise ValueError("Selected client HTTPS probe failed")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def write(path, value, *, seed=False):
    """Atomic 0600 write in a private 0700 directory; never follow symlinks.

    Walk using directory FDs so a concurrent symlink replacement cannot redirect
    credentials. Seeding never overwrites an existing projection or its inode.
    """
    path = Path(path)
    if not path.is_absolute() or any(p in {".", ".."} for p in path.parts):
        raise ValueError("Absolute normalized provider path required")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    temp = None
    try:
        for part in path.parent.parts[1:]:
            try:
                os.mkdir(part, 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        directory = os.fstat(fd)
        if directory.st_uid != os.getuid() or stat.S_IMODE(directory.st_mode) != 0o700:
            raise ValueError("Provider directory must be owned by this user and mode 0700")
        try:
            existing = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if (not stat.S_ISREG(existing.st_mode) or existing.st_uid != os.getuid()
                    or stat.S_IMODE(existing.st_mode) & 0o077):
                raise ValueError("Unsafe existing provider file")
            if seed:
                return
        temp = ".provider-" + secrets.token_hex(16)
        output = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(output, "wb") as stream:
            stream.write((json.dumps(value, sort_keys=True) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
        if seed:
            # No-clobber publication, including a race with a successful apply.
            try:
                os.link(temp, path.name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
            except FileExistsError:
                raise ValueError("Provider changed while seeding; retry startup") from None
        else:
            os.replace(temp, path.name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        if temp is not None:
            try:
                os.unlink(temp, dir_fd=fd)
            except FileNotFoundError:
                pass
        os.close(fd)


def main():
    try:
        for path in sys.argv[1:]:
            write(path, UNAVAILABLE, seed=True)
    except (OSError, ValueError):
        print("Managed Mihomo provider seeding failed; details withheld", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
