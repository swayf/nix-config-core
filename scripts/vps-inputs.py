#!/usr/bin/env python3
"""Explicit VPS input set, not an arbitrary Nix path scanner."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
TOOL_FILES = ("vps.py", "vps-vless-ops.py", "vps-probe.py", "vps-warp.py", "vps-wgcf.py",
              "vps-credentials.py", "vps-inputs.py", "vps-state.py", "deployment-lib.py", "vps-policy.py")


def library():
    spec = importlib.util.spec_from_file_location("deployment_lib", SOURCE / "deployment-lib.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tool_pin():
    rev, nar = os.environ.get("VPS_CORE_REV"), os.environ.get("VPS_CORE_NAR_HASH")
    if rev or nar:
        if not rev or not nar:
            raise ValueError("Incomplete tool/core pin")
        return {"rev": rev, "narHash": nar}
    raise ValueError("Explicit tool/core revision and NAR hash required")


def digest(repo, name, directory, secret, monitoring):
    lock = library().selected_lock(json.loads((repo / "flake.lock").read_text()),
                                  ("core", "nixpkgs2605", "disko", "sops-nix"))
    core = lock["nodes"][lock["nodes"]["root"]["inputs"]["core"]]["locked"]
    if any(core.get(k) != value for k, value in tool_pin().items()):
        raise ValueError("Tool/core pin mismatch; rebuild CLI from this configuration before check/apply")
    spec = importlib.util.spec_from_file_location("vps_policy", SOURCE / "vps-policy.py")
    policy_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy_module)
    policy = policy_module.policy()
    generated = directory == repo / "hosts/generated" / name
    files = [repo / filename for filename in policy["factoryFiles"]]
    files += [directory / filename for filename in ("configuration.nix", "disk-config.nix", "vless.json")]
    files += [directory / filename for filename in (("machine.json", "hardware-configuration.nix") if generated else ("operations.json",))]
    files.append(secret)
    if monitoring.exists() or monitoring.is_symlink():
        files.append(monitoring)
    # Native declarations still consume private shared data; generated hosts do not.
    if not generated:
        files += [repo / filename for filename in policy["nativeFiles"]]
    digest = hashlib.sha256(b"vps-selected-inputs-v3\0")
    digest.update(json.dumps(policy, sort_keys=True).encode())
    digest.update(json.dumps(lock, sort_keys=True).encode())
    if generated:
        registry = json.loads((repo / "hosts/generated/inventory.json").read_text())
        digest.update(json.dumps(registry[name], sort_keys=True).encode())
    for path in sorted(set(files)):
        if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
            raise ValueError("Missing/unsafe selected source input: " + str(path))
        digest.update(str(path.relative_to(repo)).encode() + b"\0" + path.read_bytes())
    for filename in TOOL_FILES:
        digest.update(filename.encode() + b"\0" + (SOURCE / filename).read_bytes())
    # Include the actual delivered renderer even when the caller's checkout differs.
    renderer = Path(os.environ.get("VPS_XRAY_RENDERER", str(SOURCE.parent / "modules/nixos/xray-render.py")))
    if not renderer.is_absolute() or not renderer.is_file() or renderer.is_symlink():
        raise ValueError("Pinned tool renderer unavailable")
    digest.update(renderer.read_bytes())
    return digest.hexdigest()
