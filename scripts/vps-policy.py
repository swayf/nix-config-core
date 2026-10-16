"""Explicit caller-owned source layout and legacy compatibility, not core defaults."""
import json
import os
from pathlib import Path, PurePosixPath
import re


def policy():
    path = os.environ.get("VPS_CLI_POLICY")
    if not path or not Path(path).is_absolute():
        raise ValueError("VPS_CLI_POLICY must name an absolute caller-owned policy file")
    value = json.loads(Path(path).read_text())
    if set(value) != {"factoryFiles", "nativeFiles", "nativeAddressFile", "legacyAdminUser"}:
        raise ValueError("Unsupported VPS source policy fields")
    for key in ("factoryFiles", "nativeFiles"):
        if not isinstance(value[key], list) or not value[key]:
            raise ValueError("Explicit selected source files required")
        for name in value[key]:
            relative_path(name)
    relative_path(value["nativeAddressFile"])
    if value["nativeAddressFile"] not in value["nativeFiles"]:
        raise ValueError("Native address source must be included in selected native files")
    if (not isinstance(value["legacyAdminUser"], str)
            or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", value["legacyAdminUser"])
            or value["legacyAdminUser"] in {"root", "nobody"}):
        raise ValueError("Invalid legacy administrator policy")
    return value


def relative_path(value):
    if (not isinstance(value, str) or not value or PurePosixPath(value).is_absolute()
            or any(p in {"", ".", ".."} for p in value.split("/"))):
        raise ValueError("Source policy paths must be relative without traversal")
    return value
