#!/usr/bin/env python3
"""Disable the discovered bell extension in every profile, preserving other settings."""
import json
import os
from pathlib import Path
import sys
import tempfile


def disable(profile):
    path = profile / "settings.json"
    # Never overwrite a user-owned symlink (including dangling or store links).
    if path.is_symlink():
        print(f"Pi bell: leaving settings symlink unchanged: {path}", file=sys.stderr)
        return False
    original = path.read_bytes() if path.exists() else None
    settings = json.loads(original) if original is not None else {}
    if not isinstance(settings, dict):
        raise ValueError(f"{path}: expected a JSON object")
    extensions = settings.get("extensions", [])
    if not isinstance(extensions, list) or not all(isinstance(item, str) for item in extensions):
        raise ValueError(f"{path}: extensions must be a string array")
    bell = profile / "extensions/bell.ts"
    # Remove explicit enables for this same discovered file, not other resources.
    enables = {"extensions/bell.ts", "+extensions/bell.ts", str(bell), "+" + str(bell)}
    exclusion = "-extensions/bell.ts"
    updated = [item for item in extensions if item not in enables and item != exclusion]
    updated.append(exclusion)
    if updated == extensions:
        return False
    settings["extensions"] = updated
    profile.mkdir(parents=True, exist_ok=True)
    if original is not None:
        backup = path.with_name(path.name + ".before-disable-bell")
        if not backup.exists() and not backup.is_symlink():
            with backup.open("xb") as stream:
                os.chmod(backup, 0o600)
                stream.write(original)
    with tempfile.NamedTemporaryFile(mode="w", dir=profile, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            os.chmod(temporary, 0o600)
            json.dump(settings, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return True


def install(home=None):
    home = Path.home() if home is None else Path(home)
    profiles = [home / ".pi/agent"]
    profiles.extend(sorted(p for p in (home / ".pi/profiles").glob("*") if p.is_dir()))
    for profile in profiles:
        disable(profile)


if __name__ == "__main__":
    install()
