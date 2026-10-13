#!/usr/bin/env python3
"""Real loopback REALITY + exporter test. Synthetic keys, no external traffic."""
import contextlib
import base64
import importlib.util
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def retry(action):
    last = None
    for _ in range(100):
        try:
            return action()
        except (OSError, ValueError, AssertionError, subprocess.CalledProcessError) as error:
            last = error
            time.sleep(0.2)
    raise AssertionError("loopback fixture did not become ready") from last


R = load("render", ROOT / "modules/nixos/xray-render.py")
F = load("fixture", ROOT / "scripts/xray-test-fixture.py")
os.umask(0o077)
with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
    base = Path(directory)
    private, public = F.keypair()
    material = {"private_key": private, "public_key": public, "short_id": "0123456789abcdef",
                "clients": {"alice": {"uuid": F.UUID}}}
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
                    "-subj", "/CN=target.test", "-keyout", str(base / "tls.key"), "-out", str(base / "tls.crt")],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_3
    tls.set_alpn_protocols(["h2", "http/1.1"])
    tls.load_cert_chain(base / "tls.crt", base / "tls.key")
    target = F.TLSHTTPServer(("127.0.0.1", 0), F.Handler, tls)
    stack.callback(target.server_close)
    threading.Thread(target=target.serve_forever, daemon=True).start()
    stack.callback(target.shutdown)
    ports = set()
    while len(ports) < 4:
        candidate = port()
        if candidate != target.server_port:
            ports.add(candidate)
    vless, stats, socks, metrics = sorted(ports)
    settings = {"serverName": "target.test", "target": f"127.0.0.1:{target.server_port}",
                "port": vless, "listen": "127.0.0.1", "statsPort": stats,
                "clients": ["alice"], "tag": "test-inbound"}
    # Syntax-check the new WARP/routing/sniffing candidate with the pinned core.
    # Do not start it or contact Cloudflare: the peer below is synthetic loopback.
    wireguard_key = lambda key: base64.b64encode(base64.urlsafe_b64decode(key + "=")).decode()
    warp = {"secretKey": wireguard_key(private), "address": ["172.16.0.2/32"],
            "noKernelTun": True, "mtu": 1420, "reserved": [1, 2, 3],
            "peers": [{"endpoint": "127.0.0.1:2408", "publicKey": wireguard_key(public)}]}
    warp_settings = settings | {
        "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic", "fakedns"]},
        "outbounds": [{"tag": "direct", "protocol": "freedom"},
                      {"tag": "blocked", "protocol": "blackhole"},
                      {"tag": "warp", "protocol": "wireguard"}],
        "routing": {"domainStrategy": "AsIs", "rules": [
            {"type": "field", "ip": ["geoip:private"], "outboundTag": "blocked"},
            {"type": "field", "protocol": ["bittorrent"], "outboundTag": "blocked"},
            {"type": "field", "domain": ["domain:ipinfo.io"], "outboundTag": "warp"}]},
    }
    warp_config = base / "warp-syntax.json"
    warp_config.write_text(json.dumps(R.render(warp_settings, material, warp)))
    warp_config.chmod(0o600)
    subprocess.run(["xray", "run", "-test", "-config", str(warp_config)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    warp_config.unlink()
    server_config = base / "server.json"
    server_config.write_text(json.dumps(R.render(settings, material)))
    client_config = F.client(material, "127.0.0.1")
    client_config["inbounds"][0]["port"] = socks
    client_config["outbounds"][0]["settings"]["vnext"][0]["port"] = vless
    (base / "client.json").write_text(json.dumps(client_config))
    processes = []

    def start(command):
        # These are isolated synthetic fixtures; keep startup diagnostics in the test log.
        process = subprocess.Popen(command)
        processes.append(process)
        return process

    def stop_all():
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)

    stack.callback(stop_all)
    subprocess.run(["xray", "run", "-test", "-config", str(server_config)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    server = start(["xray", "run", "-config", str(server_config)])
    start(["xray", "run", "-config", str(base / "client.json")])
    start(["xray-exporter", f"--listen=127.0.0.1:{metrics}", f"--xray-endpoint=127.0.0.1:{stats}",
           "--with-user-metrics", "--log-path=disabled", "--scrape-timeout=1"])
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def scrape():
        with opener.open(f"http://127.0.0.1:{metrics}/scrape", timeout=4) as response:
            return response.read().decode()

    def healthy():
        result = scrape()
        assert "xray_up 1\n" in result
        return result

    retry(healthy)

    def tunnel():
        result = subprocess.check_output(["curl", "--disable", "-kfsS", "--http1.1", "--max-time", "3",
            "--noproxy", "", "--socks5-hostname", f"127.0.0.1:{socks}", f"https://127.0.0.1:{target.server_port}/"],
            stderr=subprocess.DEVNULL)
        assert b"real-vless-tunnel-fixture" in result

    retry(tunnel)
    values = healthy()
    for dimension, name in [("user", "alice"), ("inbound", "test-inbound"), ("outbound", "direct")]:
        assert f'dimension="{dimension}",target="{name}"' in values

    def counter(value):
        return float(next(line.split()[-1] for line in value.splitlines()
            if line.startswith('xray_traffic_downlink_bytes_total{dimension="user",target="alice"}')))

    first, second = counter(values), counter(healthy())
    assert first > 0 and second >= first, "scraping reset counters"
    server.terminate()
    server.wait(timeout=10)
    assert "xray_up 0\n" in scrape(), "exporter HTTP must work with Xray stopped"
    server = start(["xray", "run", "-config", str(server_config)])
    retry(healthy)
    # An incomplete camouflage TLS handshake must not stall the fixture forever.
    stalled = socket.create_connection(("127.0.0.1", target.server_port), timeout=2)
    stack.callback(stalled.close)
    retry(tunnel)
    assert counter(healthy()) > 0, "exporter must recover without a restart"
    print("PASS: WARP/routing/sniffing syntax, REALITY HTTPS tunnel, metrics, reset=false, independent exporter M2 and recovery")
