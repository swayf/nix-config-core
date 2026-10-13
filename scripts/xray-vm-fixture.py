#!/usr/bin/env python3
"""VM-only synthetic persistent credentials and local TLS/Telegram services."""
import argparse
import importlib.util
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import ssl
import subprocess
import urllib.parse

spec = importlib.util.spec_from_file_location("fixture", Path(__file__).with_name("xray-test-fixture.py"))
F = importlib.util.module_from_spec(spec)
spec.loader.exec_module(F)
PRIVATE = "/run/test-xray-material"


def seed():
    state = Path("/var/lib/xray-test/material")
    state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not state.exists():
        private, public = F.keypair()
        state.write_text(json.dumps({"private_key": private, "public_key": public,
                                    "short_id": "0123456789abcdef", "clients": {
                                        "alice": {"uuid": F.UUID},
                                        "bob": {"uuid": F.SECOND_UUID}}}))
    Path(PRIVATE).write_bytes(state.read_bytes())
    Path("/run/test-telegram-token").write_text("000000:test-only-token")


class Handler(F.Handler):
    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"])).decode()
        values = json.loads(raw) if raw.startswith("{") else urllib.parse.parse_qs(raw)
        with Path("/run/test-telegram-events").open("a") as output:
            output.write(json.dumps(values) + "\n")
        body = json.dumps({"ok": True, "result": {"message_id": 1, "date": 0,
                          "chat": {"id": -1, "type": "group"}}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["seed", "tls", "telegram", "client"])
    parser.add_argument("--server", default="server")
    args = parser.parse_args()
    os.umask(0o077)
    if args.mode == "seed":
        seed()
    elif args.mode == "client":
        print(json.dumps(F.client(json.loads(Path(PRIVATE).read_text()), args.server)))
    else:
        if args.mode == "tls":
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
                            "-subj", "/CN=target.test", "-keyout", "/run/test-tls.key", "-out", "/run/test-tls.crt"],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_3
            context.set_alpn_protocols(["h2", "http/1.1"])
            context.load_cert_chain("/run/test-tls.crt", "/run/test-tls.key")
            server = F.TLSHTTPServer(("0.0.0.0", 443), Handler, context)
        else:
            server = ThreadingHTTPServer(("127.0.0.1", 18080), Handler)
        server.serve_forever()


if __name__ == "__main__":
    main()
