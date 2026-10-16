#!/usr/bin/env python3
"""Non-destructive VPS state copying and transitional lookup; no SSH or registration."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

STATE_FILES = {"probe.json", "plan.json", "credentials.json", "checked.json",
               "install-attempt.json", "verified.json", "installed-known-hosts"}
MARKER = ".legacy-state-copy.json"


def validate_name(name):
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", name):
        raise ValueError("Invalid host name")
    return name


def safe_path(path):
    path = Path(os.path.abspath(path.expanduser()))
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Symlinked state path refused")
    return path


def entries(source):
    safe_path(source)
    result = {}
    if not source.exists():
        return result
    for path in source.iterdir():
        selected = path.name in STATE_FILES or path.name.startswith("installer-known-hosts-") or path.name == "errors"
        if not selected:
            continue
        if path.is_symlink():
            raise ValueError("Symlinked state artifact refused")
        if (path.name == "errors" and not path.is_dir()) or (path.name != "errors" and not path.is_file()):
            raise ValueError("Unexpected state artifact type")
        files = list(path.rglob("*")) if path.name == "errors" else [path]
        for file in files:
            if file.is_symlink() or not (file.is_file() or file.is_dir()):
                raise ValueError("Non-regular state artifact refused")
            if file.is_file():
                if file.stat().st_size > 16 * 1024 * 1024:
                    raise ValueError("State artifact exceeds 16 MiB")
                result[file.relative_to(source).as_posix()] = hashlib.sha256(file.read_bytes()).hexdigest()
    return result


def xdg_root():
    root = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    if not root.is_absolute():
        raise ValueError("XDG_STATE_HOME must be absolute")
    return safe_path(root / "vps")


def select(repo, name, override=None):
    validate_name(name)
    legacy = safe_path(repo / "hosts/generated" / name)
    if override is not None:
        return safe_path(override / name)
    target = safe_path(xdg_root() / name)
    old = entries(legacy)
    if old:
        if target.exists():
            marker = target / MARKER
            if not marker.is_file() or marker.is_symlink():
                raise ValueError("Both legacy and XDG state exist; explicit fixture migration/review required")
            value = json.loads(marker.read_text())
            if value != {"schema": 1, "source": str(legacy), "files": old}:
                raise ValueError("Legacy state changed after copy; inspect recovery, no automatic merge")
            # Never interpret a missing attempt marker as a fresh installation.
            if "install-attempt.json" in old and not (target / "install-attempt.json").is_file():
                raise ValueError("Copied installation attempt marker missing; inspect recovery")
            copied = entries(target)
            if not old.keys() <= copied.keys():
                raise ValueError("Copied state artifact missing; inspect recovery, no automatic merge")
            return target
        return legacy
    if target.exists():
        owner = target / ".state-owner.json"
        if not owner.is_file() or owner.is_symlink() or json.loads(owner.read_text()) != {"repo": str(repo)}:
            raise ValueError("XDG state belongs to an unknown checkout; use explicit --state-dir")
    return target


def create(path, repo):
    safe_path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    owner = path / ".state-owner.json"
    if path.parent == xdg_root() and not owner.exists():
        with owner.open("x") as output:
            json.dump({"repo": str(repo)}, output)
        owner.chmod(0o600)


def copy_state(repo, name, destination, copy=False):
    validate_name(name)
    source = safe_path(repo / "hosts/generated" / name)
    target = safe_path(destination / name)
    if target == source or source in target.parents or target in source.parents:
        raise ValueError("Source and target must be separate")
    files = entries(source)
    if not files:
        raise ValueError("No legacy state artifacts")
    if target.exists():
        raise ValueError("Destination already exists; no overwrite/merge")
    result = {"schema": 1, "source": str(source), "files": files}
    if not copy:
        return result
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    stage = Path(tempfile.mkdtemp(prefix=".vps-state-", dir=target.parent))
    try:
        for name in files:
            dest = stage / name
            dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            dest.write_bytes((source / name).read_bytes())
            dest.chmod(0o600)
        if entries(source) != files or entries(stage) != files:
            raise ValueError("State changed during copy; nothing published")
        (stage / MARKER).write_text(json.dumps(result, indent=2) + "\n")
        (stage / MARKER).chmod(0o600)
        if target.exists() or target.is_symlink():
            raise ValueError("Destination appeared during copy")
        stage.rename(target)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    # Old files and every embedded path/receipt hash are deliberately unchanged.
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--copy", action="store_true", help="explicit copy; default is read-only preview")
    parser.add_argument("name")
    args = parser.parse_args()
    repo = safe_path(args.repo).resolve()
    with (repo / ".vps.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(json.dumps(copy_state(repo, args.name, args.destination, args.copy), indent=2))


if __name__ == "__main__":
    main()
