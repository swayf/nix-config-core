#!/usr/bin/env python3
"""Integration test: gateway built with port = 12081; directPort = 12083.

Usage: python3 test-network.py /path/to/gateway-image.tar.gz
Uses a local SOCKS fixture (no external connections and no real credentials),
plus the already-local docker.io/library/busybox:latest image or a local archive.
For the GUI gateway (port = 12081; directPort = null), pass --strict. Tests the same
firewall, TUN and patched SOCKS implementation as the production gateway.
"""
import argparse
import contextlib
import json
import os
import socket
import socketserver
import struct
import subprocess
import threading
import time


def run(*args, check=True):
    result = subprocess.run(args, text=True, capture_output=True, timeout=180)
    if check and result.returncode:
        raise RuntimeError(f"{args}: {result.stdout}\n{result.stderr}")
    return result


def podman(*args, **kw):
    return run("podman", *args, **kw)


def recv(conn, count):
    data = b""
    while len(data) < count:
        chunk = conn.recv(count - len(data))
        if not chunk:
            raise EOFError
        data += chunk
    return data


reject = threading.Event()


class TCP(socketserver.BaseRequestHandler):
    def handle(self):
        with contextlib.suppress(OSError, EOFError):
            conn = self.request
            conn.settimeout(10)
            version, methods = recv(conn, 2)
            assert version == 5
            recv(conn, methods)
            conn.sendall(b"\x05\x00")
            _, cmd, _, atyp = recv(conn, 4)
            recv(conn, {1: 4, 4: 16}.get(atyp, 0) or recv(conn, 1)[0])
            recv(conn, 2)
            # Match Mihomo's actual loopback BND.ADDR behind pasta NAT.
            port = self.server.server_address[1]
            rejected = port == 12081 and reject.is_set()
            reply = b"\x05" + (b"\x05" if rejected else b"\x00")
            conn.sendall(reply + b"\x00\x01\x7f\x00\x00\x01" + struct.pack("!H", port))
            if rejected:
                return
            if cmd == 1:
                conn.recv(4096)
                body = b"via-direct" if port == 12083 else b"via-socks"
                conn.sendall(b"HTTP/1.0 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
            elif cmd == 3:
                while conn.recv(1024):
                    pass


class UDP(socketserver.BaseRequestHandler):
    def handle(self):
        if reject.is_set():
            return
        packet, sock = self.request
        # SOCKS UDP header for the literal IPv4 DNS resolver used by the test.
        assert packet[:4] == b"\x00\x00\x00\x01"
        query = packet[10:]
        question = query[12:]
        # Strip optional EDNS records after the first question.
        end = question.index(b"\x00") + 5
        answer = query[:2] + b"\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00"
        answer += question[:end] + b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04\xcb\x00\x71\x2a"
        sock.sendto(packet[:10] + answer, self.client_address)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("gateway_archive")
parser.add_argument("workload_archive", nargs="?", help="Optional local BusyBox image archive")
parser.add_argument("--strict", action="store_true", help="Gateway without directPort; test GUI workload permissions and blocked DIRECT")
args = parser.parse_args()

gateway = f"agents-firewall-test-gateway-{os.getpid()}"
work = f"agents-firewall-test-work-{os.getpid()}"
servers = []


def start_proxy():
    for cls in (Server, socketserver.ThreadingUDPServer):
        server = cls(("127.0.0.1", 12081), TCP if cls is Server else UDP)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)


def inside(*args, success=True):
    result = podman("exec", work, *args, check=False)
    assert (result.returncode == 0) == success, (args, result.stdout, result.stderr)
    return result.stdout


def explicit_direct():
    # BusyBox has nc but no SOCKS-aware curl. Send a SOCKS5 domain CONNECT
    # followed by HTTP, proving the request reaches the distinct direct fixture.
    if args.strict:
        inside("timeout", "3", "nc", "-z", "10.0.2.2", "12083", success=False)
    else:
        output = inside("sh", "-c", r"printf '\005\001\000\005\001\000\003\013example.com\000\120GET / HTTP/1.0\r\n\r\n' | nc -w 3 10.0.2.2 12083")
        assert "via-direct" in output, repr(output)


def blocked():
    inside("timeout", "3", "wget", "-qO-", "http://198.51.100.80", success=False)
    inside("timeout", "3", "nslookup", "example.com", "1.1.1.1", success=False)


def load_archive(path):
    podman("load", "-i", path)
    # Read the archive's manifest to avoid accidentally testing an older tag.
    import tarfile
    with tarfile.open(path) as archive:
        return json.load(archive.extractfile("manifest.json"))[0]["RepoTags"][0]


try:
    ref = load_archive(args.gateway_archive)
    workload_ref = load_archive(args.workload_archive) if args.workload_archive else "docker.io/library/busybox:latest"
    start_proxy()
    direct = Server(("127.0.0.1", 12083), TCP)
    threading.Thread(target=direct.serve_forever, daemon=True).start()
    servers.append(direct)
    podman("run", "-d", "--name", gateway, "--pull=never", "--http-proxy=false",
           "--userns=keep-id:uid=1000,gid=1000", "--user=0:0",
           "--network=pasta:--map-host-loopback,10.0.2.2,--ipv4-only",
           "--cap-drop=ALL", "--cap-add=NET_ADMIN", "--device=/dev/net/tun", ref)
    for _ in range(50):
        if podman("exec", gateway, "/bin/test", "-f", "/tmp/ready", check=False).returncode == 0:
            break
        time.sleep(0.1)
    else:
        logs = podman("logs", gateway)
        raise AssertionError(logs.stdout + logs.stderr)
    permissions = ["--cap-add=SYS_CHROOT", "--security-opt=no-new-privileges"] if args.strict else [
        "--cap-add=SETUID", "--cap-add=SETGID",
        "--security-opt=seccomp=unconfined", "--security-opt=apparmor=unconfined",
        "--security-opt=unmask=ALL", "--device=/dev/net/tun",
    ]
    podman("run", "-d", "--name", work, "--pull=never", "--http-proxy=false",
           "--userns=keep-id:uid=1000,gid=1000", "--user=1000:1000",
           f"--network=container:{gateway}", "--cap-drop=ALL", *permissions,
           workload_ref, "sleep", "infinity")
    assert inside("timeout", "5", "wget", "-qO-", "http://198.51.100.80") == "via-socks"
    assert "203.0.113.42" in inside("timeout", "5", "nslookup", "-type=A", "example.com", "1.1.1.1")
    print("PASS: default TCP and UDP DNS traverse US SOCKS, including loopback UDP relay NAT")
    explicit_direct()
    print("PASS: explicit DIRECT is blocked" if args.strict else "PASS: explicit SOCKS client reaches the separate host-direct port")
    # A physical-interface canary would be reachable without the nft policy.
    canary = Server(("127.0.0.1", 12082), TCP)
    servers.append(canary)
    inside("timeout", "3", "nc", "-z", "10.0.2.2", "12082", success=False)
    inside("timeout", "3", "nslookup", "example.com", "10.0.2.2", success=False)
    inside("timeout", "3", "wget", "-qO-", "http://[2606:4700:4700::1111]", success=False)
    inside("ip", "route", "del", "default", success=False)
    print("PASS: host ports, host DNS, IPv6 and route modification blocked")
    reject.set()
    blocked()
    print("PASS: unavailable remote upstream gives no fallback")
    # Shutdown + close makes fresh connections fail; existing UDP is suppressed
    # by reject. The workload remains running through every failure case.
    for server in servers[:2]:
        server.shutdown()
        server.server_close()
    blocked()
    explicit_direct()
    print("PASS: stopped US SOCKS gives no fallback; DIRECT " + ("blocked" if args.strict else "still works"))
    reject.clear()
    start_proxy()
    assert inside("timeout", "5", "wget", "-qO-", "http://198.51.100.80") == "via-socks"
    assert "203.0.113.42" in inside("timeout", "5", "nslookup", "-type=A", "example.com", "1.1.1.1")
    print("PASS: TCP and UDP recover when the upstream returns")
    # Test gateway failure independently, with a healthy upstream still alive.
    podman("kill", "--signal=KILL", gateway)
    blocked()
    explicit_direct()
    print("PASS: killed gateway blocks default egress; DIRECT " + ("blocked" if args.strict else "remains available"))
finally:
    # Podman removes a list in parallel; the namespace owner must go last.
    podman("rm", "-f", "--time=1", work, check=False)
    podman("rm", "-f", "--time=1", gateway, check=False)
    for server in servers:
        server.server_close()
