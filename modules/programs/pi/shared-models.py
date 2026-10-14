"""Share Pi's model configuration and credentials, never profile settings.

Run with Pi stopped. Existing profile files are backed up before migration;
providers already in the common file take precedence as a complete block.
"""

from pathlib import Path
import fcntl
import json
import os
import sys
import tempfile


def load(path):
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return data


def save(path, data):
    # Follow a user's common-file symlink rather than replacing it.
    path = path.resolve()
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def share(home):
    root = home / ".pi"
    common = root / "agent"
    common.mkdir(parents=True, exist_ok=True)
    # homeInit may run concurrently in several terminals.
    with open(common / ".shared-models.lock", "a", opener=lambda p, f: os.open(p, f, 0o600)) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        profiles = sorted(path for path in (root / "profiles").glob("*") if path.is_dir())
        for name in ("models.json", "auth.json"):
            target = common / name
            data = load(target) if target.exists() else {}
            sources = []
            for profile in profiles:
                source = profile / name
                if source.is_symlink() and source.resolve() == target.resolve():
                    continue
                if source.exists() or source.is_symlink():
                    local = load(source)  # Fail safely on invalid JSON/dangling user links.
                    sources.append((source, local))
            # Validate all sources before changing this shared file.
            if name == "models.json":
                providers = data.setdefault("providers", {})
                if not isinstance(providers, dict):
                    raise ValueError(f"Expected a providers object: {target}")
                for source, local in sources:
                    if not isinstance(local.get("providers", {}), dict):
                        raise ValueError(f"Expected a providers object: {source}")
                    for provider, config in local.get("providers", {}).items():
                        providers.setdefault(provider, config)
                    for key, value in local.items():
                        data.setdefault(key, value)
            else:
                for _, local in sources:
                    for provider, credential in local.items():
                        data.setdefault(provider, credential)
            if not target.exists() or sources:
                save(target, data)
            target.chmod(0o600)
            for source, _ in sources:
                backup = source.with_name(name + ".before-shared")
                index = 1
                while backup.exists() or backup.is_symlink():
                    backup = source.with_name(f"{name}.before-shared.{index}")
                    index += 1
                # Save even a user symlink's content without modifying its target.
                with open(backup, "x", opener=lambda p, f: os.open(p, f, 0o600)) as stream:
                    stream.write(source.read_text())
                source.unlink()
            for profile in profiles:
                source = profile / name
                if source.is_symlink() and source.readlink() != target:
                    source.unlink()
                if not source.is_symlink():
                    source.symlink_to(target)


if __name__ == "__main__":
    share(Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home())
