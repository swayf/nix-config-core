#!/usr/bin/env python3
"""Install the pinned Pi package in all profiles, preserving writable settings."""
import json
import os
from pathlib import Path
import sys
import tempfile


def is_subagents(entry):
    source = entry.get("source") if isinstance(entry, dict) else entry
    if not isinstance(source, str):
        return False
    return (source.removeprefix("npm:").split("@", 1)[0] == "pi-subagents"
            if source.startswith("npm:") else
            source.startswith("/nix/store/") and "-pi-subagents-" in Path(source).name)


def save_settings(path, settings):
    content = json.dumps(settings, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and json.loads(path.read_text()) == settings:
        return
    backup = path.with_name("settings.json.before-pi-subagents")
    if path.exists() and not os.path.lexists(backup):
        with open(backup, "x", opener=lambda p, flags: os.open(p, flags, 0o600)) as out:
            out.write(path.read_text())
    fd, temporary = tempfile.mkstemp(prefix=".settings.json.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as out:
            out.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def install(home, source):
    package = Path(source)
    manifest = json.loads((package / "package.json").read_text())
    if manifest.get("name") != "pi-subagents" or not (package / "index.js").is_file():
        raise ValueError(f"Not a pi-subagents package: {source}")
    pi = home / ".pi"
    profiles = pi / "profiles"
    if pi.is_symlink() or profiles.is_symlink():
        raise ValueError("Refusing to install through a linked .pi/profiles directory")
    for profile in [pi / "agent"] + [profiles / n for n in ("general", "code", "wiki")]:
        if not os.path.lexists(profile):
            profile.mkdir(parents=True)
    for profile in [pi / "agent"] + sorted(profiles.iterdir()):
        if not profile.is_dir():
            continue
        path = profile / "settings.json"
        if profile.is_symlink() or (profile / "extensions").is_symlink() or path.is_symlink():
            print(f"Pi: preserving linked profile/settings/extensions: {profile}", file=sys.stderr)
            continue
        settings = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(settings, dict) or not isinstance(settings.get("packages", []), list):
            raise ValueError(f"{path}: expected settings object with packages array")
        packages = []
        found = False
        for entry in settings.get("packages", []):
            entry_source = entry.get("source") if isinstance(entry, dict) else entry
            if is_subagents(entry) or entry_source == source:
                if not found:
                    packages.append({**entry, "source": source} if isinstance(entry, dict) else source)
                    found = True
            else:
                packages.append(entry)
        if not found:
            packages.append(source)
        settings["packages"] = packages
        # The previous example extension registers the same subagent tool.
        # Keep it outside discovery, including local replacements and dangling links.
        # Never overwrite a prior backup or touch any agent definitions.
        migrations = []
        for name in ("subagent", "pi-subagents"):
            legacy = profile / "extensions" / name
            if os.path.lexists(legacy):
                backup = profile / f"{name}.before-pi-subagents"
                if os.path.lexists(backup):
                    raise ValueError(f"{backup}: backup exists; refusing to replace {legacy}")
                migrations.append((legacy, backup))
        for legacy, backup in migrations:
            legacy.rename(backup)
            print(f"Pi: preserved previous extension at {backup}")
        save_settings(path, settings)


if __name__ == "__main__":
    install(Path.home(), str(Path(sys.argv[1]).resolve()))
