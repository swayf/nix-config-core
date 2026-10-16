#!/usr/bin/env python3
"""Private SSH protocol for WARP registration on the source VPS; never run directly."""
import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import runpy
import signal
import struct
import sys
import tempfile

MAX_BINARY = 32 * 1024 * 1024
MAX_REQUEST = 64 * 1024 * 1024
MAX_RESPONSE = 1024 * 1024
REASONS = {
    "dns": "DNS resolution failed on the VPS",
    "tls_certificate": "TLS certificate validation failed; check VPS clock/CA certificates",
    "tls_handshake": "TLS handshake failed on the VPS",
    "proxy": "proxy connection failed on the VPS",
    "connection_refused": "connection refused from the VPS",
    "network_unreachable": "network unreachable from the VPS",
    "request_timeout": "network request timed out on the VPS",
    "connection_closed": "connection closed or response truncated",
    "response_format": "unexpected API response format; check wgcf/API compatibility or access controls",
    "account_exists": "wgcf found an existing account; no overwrite",
    "account_invalid": "wgcf account configuration is missing/invalid",
    "filesystem_permissions": "VPS filesystem permissions prevented saving account/profile",
    "filesystem_full": "VPS filesystem full or quota exceeded",
    "invalid_arguments": "wgcf command/flag unsupported",
    "tool_panic": "wgcf crashed; check API compatibility",
    "tool_missing": "wgcf tool could not be started on the VPS",
    "tool_start": "could not execute wgcf; check temporary filesystem permissions/noexec policy",
    "profile_invalid": "generated profile/account missing or invalid",
    "artifact_integrity": "uploaded wgcf checksum mismatch",
    "remote_platform": "worker requires Linux x86_64",
    "worker_runtime": "VPS worker failed; check Python 3 and temporary filesystem prerequisites",
    "protocol": "invalid WARP worker protocol/response; contents withheld",
    "unclassified": "cause not recognized; raw output withheld",
}


class WorkerError(ValueError):
    def __init__(self, reason, *, details=None):
        super().__init__(reason)
        self.details = details or {}


def portable_binary(data):
    """Require a Linux x86_64 ELF without a Nix/glibc interpreter or dynamic deps."""
    if not 64 <= len(data) <= MAX_BINARY or data[:7] != b"\x7fELF\x02\x01\x01":
        raise WorkerError("Use the packaged vps command: a portable Linux x86_64 wgcf binary is required")
    kind, machine = struct.unpack_from("<HH", data, 16)
    offset = struct.unpack_from("<Q", data, 32)[0]
    size, count = struct.unpack_from("<HH", data, 54)
    if kind != 2 or machine != 62 or size < 56 or not 1 <= count <= 128 or offset + size * count > len(data):
        raise WorkerError("Invalid portable wgcf ELF layout; use the packaged vps command")
    types = [struct.unpack_from("<I", data, offset + size * i)[0] for i in range(count)]
    if 1 not in types or any(kind in (2, 3) for kind in types):
        raise WorkerError("wgcf must be statically linked, without a Nix/glibc loader; use the packaged vps command")


def request(binary, helper):
    if binary.stat().st_size > MAX_BINARY:
        raise WorkerError("Portable wgcf binary exceeds 32 MiB")
    data = binary.read_bytes()
    portable_binary(data)
    if not isinstance(helper, str) or len(helper.encode()) > 256 * 1024:
        raise WorkerError("Invalid WARP helper source")
    return {"schema": 1, "accept_tos": True, "wgcf": base64.b64encode(data).decode(),
            "sha256": hashlib.sha256(data).hexdigest(), "helper": helper}


def diagnostic(details):
    """Accept only known enums and bounded numbers, never remote messages or paths."""
    result = {"tool": "wgcf", "registration_location": "source-VPS"}
    if not isinstance(details, dict):
        details = {}
    stage = details.get("stage")
    result["stage"] = stage if stage in ("wgcf register", "wgcf generate", "worker preflight") else "worker preflight"
    category = details.get("reason_code")
    result["reason_code"] = category if isinstance(category, str) and (category in REASONS or category == "http_error") else "unclassified"
    for key, low, high in (("exit_code", -128, 255), ("http_status", 400, 599), ("timeout_seconds", 0, 90)):
        value = details.get(key)
        if type(value) is int and low <= value <= high:
            result[key] = value
    value = details.get("elapsed_seconds")
    if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 300:
        result["elapsed_seconds"] = value
    for key in ("account_file_created", "timed_out"):
        if type(details.get(key)) is bool:
            result[key] = details[key]
    return result


def failure(details):
    details = diagnostic(details)
    code = details.get("http_status")
    if details["reason_code"] == "http_error" and code is not None:
        hints = {401: "API authentication rejected", 403: "API access forbidden; check VPS network/proxy/API compatibility",
                 404: "API endpoint not found", 407: "proxy authentication required", 429: "API rate limit reached"}
        reason = f"HTTP {code}: " + hints.get(code, "API/server request failed")
    else:
        reason = REASONS.get(details["reason_code"], REASONS["unclassified"])
    status = " timed out" if details.get("timed_out") else " failed"
    if "exit_code" in details:
        status += f" (exit {details['exit_code']})"
    message = "WARP on source VPS: " + details["stage"] + status + "; " + reason
    if details.get("account_file_created"):
        message += "; account file was created before failure (temporary files cleaned up)"
    return WorkerError(message + "; remote account may already exist; no retry", details=details)


def response(raw):
    try:
        if len(raw) > MAX_RESPONSE:
            raise ValueError()
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get("schema") != 1 or type(value.get("ok")) is not bool:
            raise ValueError()
        if value["ok"]:
            if set(value) != {"schema", "ok", "settings", "account"} or not isinstance(value["settings"], dict):
                raise ValueError()
            if not isinstance(value["account"], str) or not 0 < len(value["account"].encode()) <= 256 * 1024:
                raise ValueError()
            return value["settings"], value["account"]
        if set(value) != {"schema", "ok", "error"} or not isinstance(value["error"], dict):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise failure({"reason_code": "protocol"}) from None
    raise failure(value["error"])


def execute(value):
    if not isinstance(value, dict) or value.get("schema") != 1 or value.get("accept_tos") is not True:
        raise failure({"reason_code": "protocol"})
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise failure({"reason_code": "remote_platform"})
    try:
        binary = base64.b64decode(value["wgcf"], validate=True)
        portable_binary(binary)
        if hashlib.sha256(binary).hexdigest() != value["sha256"]:
            raise failure({"reason_code": "artifact_integrity"})
        helper = value["helper"]
        if not isinstance(helper, str) or len(helper.encode()) > 256 * 1024:
            raise ValueError()
    except (ValueError, TypeError, KeyError) as e:
        if isinstance(e, WorkerError):
            raise e
        raise failure({"reason_code": "protocol"}) from None
    with tempfile.TemporaryDirectory(prefix="vps-warp.") as directory:
        root = Path(directory)
        root.chmod(0o700)
        (root / "wgcf").write_bytes(binary)
        (root / "wgcf").chmod(0o700)
        source = root / "lt1-warp.py"
        source.write_text(helper)
        source.chmod(0o600)
        original_env, original_tempdir = dict(os.environ), tempfile.tempdir
        try:
            # Only the VPS's own proxy/CA settings apply, not the local PC's.
            allowed = {"SSL_CERT_FILE", "SSL_CERT_DIR", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY",
                       "https_proxy", "http_proxy", "all_proxy", "no_proxy"}
            os.environ.clear()
            os.environ.update({key: val for key, val in original_env.items() if key in allowed})
            os.environ.update({"PATH": str(root) + ":/usr/bin:/bin", "TMPDIR": str(root)})
            tempfile.tempdir = str(root)
            tool = runpy.run_path(str(source))
            try:
                settings, account = tool["register"](argparse.Namespace(accept_tos=True, vps=False))
            except tool["RegistrationError"] as e:
                raise failure(e.details) from None
            return {"schema": 1, "ok": True, "settings": settings, "account": account}
        finally:
            os.environ.clear()
            os.environ.update(original_env)
            tempfile.tempdir = original_tempdir


def main():
    os.umask(0o077)
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, lambda received, _frame: sys.exit(128 + received))
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST:
            raise failure({"reason_code": "protocol"})
        value = execute(json.loads(raw))
        encoded = json.dumps(value)
        if len(encoded.encode()) > MAX_RESPONSE:
            raise failure({"reason_code": "protocol"})
    except WorkerError as e:
        encoded = json.dumps({"schema": 1, "ok": False, "error": diagnostic(e.details)})
    except Exception:
        encoded = json.dumps({"schema": 1, "ok": False, "error": diagnostic({"reason_code": "worker_runtime"})})
    # stdout is captured by the local SSH parent, never inherited by a terminal.
    print(encoded)
    return 0  # Transport succeeded; the parent handles ok:false without retrying.


if __name__ == "__main__":
    sys.exit(main())
