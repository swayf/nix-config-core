#!/usr/bin/env python3
"""Synthetic X25519 credentials and TLS target shared by isolated Xray tests."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import subprocess

UUID = "12345678-1234-4234-8234-123456789abc"
SECOND_UUID = "abcdefab-cdef-4abc-8def-abcdefabcdef"


def keypair():
    private = subprocess.check_output(["openssl", "genpkey", "-algorithm", "X25519", "-outform", "DER"])
    public = subprocess.check_output(["openssl", "pkey", "-inform", "DER", "-pubout", "-outform", "DER"], input=private)
    def encode(value):
        return base64.urlsafe_b64encode(value[-32:]).decode().rstrip("=")
    return encode(private), encode(public)


def client(material, server):
    return {
        "log": {"access": "none", "loglevel": "warning"},
        "inbounds": [{"listen": "127.0.0.1", "port": 1080, "protocol": "socks", "settings": {"auth": "noauth"}}],
        "outbounds": [{"protocol": "vless", "settings": {"vnext": [{"address": server, "port": 443,
            "users": [{"id": UUID, "encryption": "none", "flow": "xtls-rprx-vision"}]}]},
            "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {
                "serverName": "target.test", "fingerprint": "chrome", "password": material["public_key"],
                "shortId": material["short_id"], "spiderX": "/",
            }}}],
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        body = b"real-vless-tunnel-fixture\n" * 4096
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class TLSHTTPServer(ThreadingHTTPServer):
    def __init__(self, address, handler, context):
        self.context = context
        super().__init__(address, handler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        try:
            return self.context.wrap_socket(connection, server_side=True), address
        except (OSError, ValueError):
            connection.close()
            raise
