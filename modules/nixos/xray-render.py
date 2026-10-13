#!/usr/bin/env python3
"""Render Xray JSON from public settings and existing material, without rotation."""
import argparse
import base64
import copy
import json
import ipaddress
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid


def validate_endpoint(value):
    if not isinstance(value, str):
        raise ValueError("invalid WireGuard endpoint")
    match = re.fullmatch(r"(?:\[([0-9a-fA-F:]+)\]|([A-Za-z0-9][A-Za-z0-9.-]{0,252})):(\d{1,5})", value)
    if not match or not 1 <= int(match[3]) <= 65535:
        raise ValueError("invalid WireGuard endpoint")
    if match[1]:
        ipaddress.IPv6Address(match[1])


def validate_warp(settings):
    def key(value):
        if not isinstance(value, str) or len(base64.b64decode(value, validate=True)) != 32:
            raise ValueError("invalid WireGuard key")
    if not isinstance(settings, dict) or settings.get("noKernelTun") is not True:
        raise ValueError("userspace WireGuard required")
    key(settings["secretKey"])
    addresses = settings["address"]
    if not isinstance(addresses, list) or not addresses:
        raise ValueError("WireGuard addresses required")
    for address in addresses:
        ipaddress.ip_interface(address)
    peers = settings["peers"]
    if not isinstance(peers, list) or not peers:
        raise ValueError("WireGuard peer required")
    for peer in peers:
        validate_endpoint(peer["endpoint"])
        key(peer["publicKey"])
        if "preSharedKey" in peer:
            key(peer["preSharedKey"])
        if "allowedIPs" in peer:
            if not isinstance(peer["allowedIPs"], list) or not peer["allowedIPs"]:
                raise ValueError("invalid allowedIPs")
            for network in peer["allowedIPs"]:
                ipaddress.ip_network(network, strict=False)
        if "keepAlive" in peer and (type(peer["keepAlive"]) is not int or not 0 <= peer["keepAlive"] <= 65535):
            raise ValueError("invalid keepAlive")
    if "mtu" in settings and (type(settings["mtu"]) is not int or not 576 <= settings["mtu"] <= 65535):
        raise ValueError("invalid MTU")
    if "reserved" in settings:
        reserved = settings["reserved"]
        if (not isinstance(reserved, list) or len(reserved) != 3
                or any(type(v) is not int or not 0 <= v <= 255 for v in reserved)):
            raise ValueError("invalid reserved bytes")


def render(settings, material, warp=None):
    names = settings["clients"]
    if (not isinstance(names, list) or len(names) != len(set(names))
            or any(not isinstance(n, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", n) for n in names)):
        raise ValueError("invalid clients")
    if not re.fullmatch(r"[a-zA-Z0-9.-]+", settings["serverName"]):
        raise ValueError("invalid SNI")
    port = settings["port"]
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("invalid port")
    for key in ("private_key", "public_key"):
        value = material[key]
        if (not re.fullmatch(r"[A-Za-z0-9_-]{43}", value)
                or len(base64.urlsafe_b64decode(value + "=")) != 32):
            raise ValueError("invalid key")
    private = base64.urlsafe_b64decode(material["private_key"] + "=")
    derived = subprocess.run(
        ["openssl", "pkey", "-inform", "DER", "-pubout", "-outform", "DER"],
        input=bytes.fromhex("302e020100300506032b656e04220420") + private,
        capture_output=True,
        check=False,
    )
    if (derived.returncode or len(derived.stdout) != 44
            or derived.stdout[-32:] != base64.urlsafe_b64decode(material["public_key"] + "=")):
        raise ValueError("inconsistent REALITY keypair")
    if not re.fullmatch(r"(?:[0-9a-f]{2}){1,8}", material["short_id"]):
        raise ValueError("invalid short ID")
    clients, seen = [], set()
    for name in names:
        value = material["clients"][name]["uuid"]
        if str(uuid.UUID(value)) != value or value in seen:
            raise ValueError("invalid or duplicate UUID")
        seen.add(value)
        clients.append({"id": value, "email": name, "level": 0, "flow": "xtls-rprx-vision"})
    # Public outbounds/routing belong to the caller; no panel template is read.
    routing = copy.deepcopy(settings.get("routing", {"domainStrategy": "AsIs", "rules": []}))
    routing.setdefault("rules", []).insert(0, {
        "type": "field", "inboundTag": ["stats-api"], "outboundTag": "stats-api",
    })
    outbounds = copy.deepcopy(settings.get("outbounds", [{"tag": "direct", "protocol": "freedom"}]))
    placeholders = [o for o in outbounds if o.get("tag") == "warp"]
    if placeholders:
        if (len(placeholders) != 1 or placeholders[0].get("protocol") != "wireguard"
                or placeholders[0].get("settings") not in (None, {})):
            raise ValueError("WARP must be a public placeholder without credentials")
        validate_warp(warp)
        placeholders[0]["settings"] = copy.deepcopy(warp)
    elif warp is not None:
        raise ValueError("WARP credentials without outbound")
    return {
        "log": {"access": "none", "error": "", "loglevel": "warning"},
        "stats": {},
        "api": {"tag": "stats-api", "services": ["StatsService"]},
        "policy": {
            "levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}},
            "system": {"statsInboundUplink": True, "statsInboundDownlink": True,
                       "statsOutboundUplink": True, "statsOutboundDownlink": True},
        },
        "inbounds": [
            {"tag": "stats-api", "listen": "127.0.0.1", "port": settings["statsPort"],
             "protocol": "dokodemo-door", "settings": {"address": "127.0.0.1"}},
            {"tag": settings["tag"], "listen": settings.get("listen", "0.0.0.0"), "port": port,
             "protocol": "vless", "settings": {"clients": clients, "decryption": "none"},
             "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {
                 "show": False, "target": settings.get("target", settings["serverName"] + ":443"),
                 "xver": 0, "serverNames": [settings["serverName"]],
                 "privateKey": material["private_key"], "shortIds": [material["short_id"]],
             }}, "sniffing": copy.deepcopy(settings.get("sniffing", {"enabled": False}))},
        ],
        "outbounds": outbounds,
        "routing": routing,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--material", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--warp", type=Path, help="runtime-only WireGuard settings, never a store path")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        result = render(json.loads(args.settings.read_text()), json.loads(args.material.read_text()),
                        json.loads(args.warp.read_text()) if args.warp else None)
        with tempfile.NamedTemporaryFile(dir=args.output.parent, delete=False) as output:
            temp = Path(output.name)
            try:
                output.write((json.dumps(result) + "\n").encode())
                output.flush()
                os.fsync(output.fileno())
                temp.replace(args.output)
            finally:
                temp.unlink(missing_ok=True)
    except (ValueError, KeyError, TypeError, OSError, AttributeError):
        # Never echo malformed input or exception details containing credentials.
        print("Xray configuration/material rejected", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
