#!/usr/bin/env python3
"""Isolated wgcf registration helper; no fleet defaults or SSH interface."""
import configparser
from pathlib import Path
import json
import re
import subprocess
import tempfile
import time


class RegistrationError(ValueError):
    """Static registration diagnostics safe to display without exposing tool output."""
    def __init__(self, message, *, details=None):
        super().__init__(message)
        self.details = details or {}


def parse_profile(text):
    if text.lstrip().startswith("{"):
        value = json.loads(text)
        if "outbounds" in value:
            values = [o["settings"] for o in value["outbounds"] if o.get("tag") == "warp" and o.get("protocol") == "wireguard"]
            if len(values) != 1:
                raise ValueError("expected one warp outbound")
            return values[0]
        return value
    profile = configparser.ConfigParser(interpolation=None)
    profile.read_string(text)
    interface, peer = profile["Interface"], profile["Peer"]
    split = lambda value: [v.strip() for v in value.split(",") if v.strip()]
    result = {"secretKey": interface["PrivateKey"], "address": split(interface["Address"]),
              "mtu": int(interface.get("MTU", "1280")), "noKernelTun": True,
              "peers": [{"publicKey": peer["PublicKey"], "endpoint": peer["Endpoint"],
                         "allowedIPs": split(peer.get("AllowedIPs", "0.0.0.0/0, ::/0")),
                         "keepAlive": int(peer.get("PersistentKeepalive", "0"))}]}
    if peer.get("PresharedKey"):
        result["peers"][0]["preSharedKey"] = peer["PresharedKey"]
    # wgcf does not export Cloudflare reserved bytes. Imported Xray JSON preserves them.
    if interface.get("Reserved"):
        result["reserved"] = [int(v) for v in split(interface["Reserved"])]
    return result


def wgcf_diagnostic(stdout, stderr):
    """Classify captured output; return only fixed text/codes, never matched lines."""
    def text(value):
        if isinstance(value, bytes):
            return value[-128 * 1024:].decode(errors="replace")
        return value[-128 * 1024:] if isinstance(value, str) else ""
    output = (text(stderr) + "\n" + text(stdout)).lower()
    status = re.search(r"\b(?:http(?:/\d(?:\.\d)?)?\s+|(?:http\s+)?status(?:\s+code)?\s*[:=]?\s+)([45]\d{2})\b", output)
    if status is None:
        status = re.search(r"\b(400) bad request\b|\b(401) unauthorized\b|\b(403) forbidden\b|"
                           r"\b(404) not found\b|\b(407) proxy authentication required\b|"
                           r"\b(408) request timeout\b|\b(429) too many requests\b|"
                           r"\b(500) internal server error\b|\b(502) bad gateway\b|"
                           r"\b(503) service unavailable\b|\b(504) gateway timeout\b", output)
    if status is not None:
        code = int(next(value for value in status.groups() if value is not None))
        hints = {400: "API request rejected; check wgcf/API compatibility",
                 401: "API authentication rejected; registration may have failed after account creation",
                 403: "API request forbidden; check network/proxy access and wgcf/API compatibility",
                 404: "API endpoint not found; check wgcf/API compatibility",
                 407: "proxy authentication required; check proxy configuration",
                 408: "API request timed out; check connectivity",
                 429: "API rate limit reached; wait before any explicit retry"}
        hint = hints.get(code, "API/server error; check service availability" if code >= 500 else "API request rejected")
        return f"HTTP {code}: {hint}", {"reason_code": "http_error", "http_status": code}
    patterns = [
        ("dns", ("no such host", "server misbehaving", "temporary failure in name resolution", "name or service not known"),
         "DNS resolution failed; check DNS, including the proxy hostname if configured"),
        ("tls_certificate", ("x509:", "certificate verify failed", "certificate verification failed"),
         "TLS certificate validation failed; check system clock and CA certificates; do not disable verification"),
        ("tls_handshake", ("tls handshake", "tls: handshake", "remote error: tls", "handshake failure"),
         "TLS handshake failed; check network/proxy interception and wgcf/API compatibility"),
        ("proxy", ("proxyconnect tcp", "socks connect", "proxy authentication"),
         "proxy connection failed; check proxy configuration and connectivity"),
        ("connection_refused", ("connection refused",), "connection refused; check network/firewall/proxy"),
        ("network_unreachable", ("network is unreachable", "no route to host"), "network unreachable; check routing/firewall"),
        ("request_timeout", ("i/o timeout", "context deadline exceeded", "client.timeout exceeded", "timed out"),
         "network request timed out; check DNS/connectivity/proxy"),
        ("connection_closed", ("connection reset by peer", "unexpected eof"),
         "connection closed or response truncated; check network/proxy and service availability"),
        ("response_format", ("invalid character", "cannot unmarshal", "unexpected end of json input", "<!doctype html", "<html"),
         "unexpected API response format; check wgcf/API compatibility or an HTML response from a proxy/access-control page"),
        ("account_exists", ("existing account", "account already exists"), "wgcf found an existing account; do not overwrite it"),
        ("account_invalid", ("invalid account", "no account", "missing account"), "wgcf account configuration is missing/invalid"),
        ("filesystem_permissions", ("permission denied", "read-only file system"), "local file permissions prevented wgcf from saving data"),
        ("filesystem_full", ("no space left on device", "disk quota exceeded"), "local filesystem is full or quota exceeded"),
        ("invalid_arguments", ("unknown flag", "unknown command"), "wgcf does not support the requested command/flag; check its version"),
        ("tool_panic", ("panic:",), "wgcf crashed; check its version and API compatibility"),
    ]
    for category, signatures, reason in patterns:
        if any(signature in output for signature in signatures):
            return reason, {"reason_code": category}
    return "cause not recognized from captured output; check wgcf/API connectivity and configuration", {"reason_code": "unclassified"}


def register(args):
    if not args.accept_tos:
        raise ValueError("registration requires explicit --accept-tos")
    # wgcf is unofficial. Its account, tokens and generated profile must never reach terminal logs.
    with tempfile.TemporaryDirectory(prefix="vps-wgcf.") as directory:
        root = Path(directory)
        root.chmod(0o700)
        for command in (["wgcf", "register", "--accept-tos"], ["wgcf", "generate"]):
            stage = "wgcf " + command[1]
            details = {"tool": "wgcf", "stage": stage, "timeout_seconds": 90}
            started = time.monotonic()
            try:
                # Keep raw logs only in memory: wgcf can print account credentials.
                result = subprocess.run(command, cwd=root, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
            except FileNotFoundError:
                details["reason_code"] = "tool_missing"
                raise RegistrationError(stage + ": required wgcf tool not found; check runtime/PATH; no retry",
                                        details=details) from None
            except subprocess.TimeoutExpired as e:
                reason, diagnosis = wgcf_diagnostic(e.output, e.stderr)
                details.update(diagnosis)
                details["elapsed_seconds"] = round(time.monotonic() - started, 3)
                details["account_file_created"] = (root / "wgcf-account.toml").is_file()
                details["timed_out"] = True
                raise RegistrationError(stage + ": timed out after 90s; " + reason + "; "
                                        "registration may already have created an account; raw output withheld; no retry",
                                        details=details) from None
            except OSError:
                details["reason_code"] = "tool_start"
                raise RegistrationError(stage + ": unable to start tool; check local permissions/runtime; no retry",
                                        details=details) from None
            details["elapsed_seconds"] = round(time.monotonic() - started, 3)
            details["exit_code"] = result.returncode
            if result.returncode:
                reason, diagnosis = wgcf_diagnostic(result.stdout, result.stderr)
                details.update(diagnosis)
                details["account_file_created"] = (root / "wgcf-account.toml").is_file()
                account = "wgcf account file was created before failure" if details["account_file_created"] else "remote account creation cannot be ruled out"
                raise RegistrationError(stage + f" failed (exit {result.returncode}); " + reason + "; " + account
                                        + "; raw output withheld to protect secrets; no retry", details=details)
        try:
            return parse_profile((root / "wgcf-profile.conf").read_text()), (root / "wgcf-account.toml").read_text()
        except (ValueError, KeyError, OSError, configparser.Error):
            raise RegistrationError("wgcf generated invalid/missing profile or account; "
                                    "details withheld to protect secrets; account may already exist; no retry",
                                    details={"tool": "wgcf", "stage": "wgcf generate", "reason_code": "profile_invalid"}) from None
