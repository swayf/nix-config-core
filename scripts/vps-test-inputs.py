"""Synthetic selected-input fixture shared by offline CLI suites (not shipped)."""
import json
import os
from pathlib import Path


def prepare(repo, name):
    if os.environ.get("VPS_CORE_REV"):
        core = {"rev": os.environ["VPS_CORE_REV"], "narHash": os.environ["VPS_CORE_NAR_HASH"]}
    else:
        raise ValueError("Tests require explicit tool/core pin")
    nodes = {"root": {"inputs": {n: n for n in ("core", "nixpkgs2605", "disko", "sops-nix")}}}
    for node in nodes["root"]["inputs"]:
        nodes[node] = {"locked": {"type": "github", "owner": "fixture", "repo": node,
                                   "rev": "b" * 40, "narHash": "sha256-fixture"}, "inputs": {}}
    nodes["core"]["locked"].update(core)
    (repo / "flake.lock").write_text(json.dumps({"version": 7, "root": "root", "nodes": nodes}))
    policy = json.loads(Path(os.environ["VPS_CLI_POLICY"]).read_text())
    for name_ in (*policy["factoryFiles"],
                  f"hosts/generated/{name}/configuration.nix", f"hosts/generated/{name}/disk-config.nix",
                  f"hosts/generated/{name}/hardware-configuration.nix"):
        path = repo / name_
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n")
    registry = repo / "hosts/generated/inventory.json"
    registry.write_text(json.dumps({name: {"system": "x86_64-linux"}}))
    secret = repo / "hosts/generated" / name / "secrets.yaml"
    if not secret.exists():
        secret.write_text("fixture ciphertext")
