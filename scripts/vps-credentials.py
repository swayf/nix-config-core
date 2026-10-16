#!/usr/bin/env python3
"""Tool-owned credential primitives; callers supply paths and recipients explicitly."""
import base64
import subprocess
import uuid


def run(command, data=None):
    result = subprocess.run(command, input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if result.returncode:
        raise ValueError("command failed: " + command[0] + "; check installed tools")
    return result.stdout


def keypair():
    private = run(["openssl", "genpkey", "-algorithm", "X25519", "-outform", "DER"])
    public = run(["openssl", "pkey", "-inform", "DER", "-pubout", "-outform", "DER"], private)
    if (len(private) != 48 or private[:16].hex() != "302e020100300506032b656e04220420"
            or len(public) != 44 or public[:12].hex() != "302a300506032b656e032100"):
        raise ValueError("unexpected OpenSSL X25519 encoding")
    encode = lambda value: base64.urlsafe_b64encode(value).decode().rstrip("=")
    return encode(private[-32:]), encode(public[-32:])


def new_client():
    return {"uuid": str(uuid.uuid4())}
