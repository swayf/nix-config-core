#!/usr/bin/env python3
"""Install a managed package declaration and routing in every Pi profile.

Only this script and the credential-free defaults enter the Nix store. Existing
model settings, package filters and unrelated web options remain writable.
"""
import json
import os
from pathlib import Path
import sys
import tempfile


def read_object(path):
    if not path.exists() and not path.is_symlink():
        return {}
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def save_object(path, value):
    content = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and json.loads(path.read_text()) == value:
        return
    # Preserve the original once, including any credentials, outside the store.
    backup = path.with_name(path.name + ".before-web-access")
    if path.exists() and not backup.exists():
        with open(backup, "x", opener=lambda p, flags: os.open(p, flags, 0o600)) as out:
            out.write(path.read_text())
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as out:
            out.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def is_web_access(entry):
    source = entry.get("source") if isinstance(entry, dict) else entry
    if not isinstance(source, str):
        return False
    if source.startswith("npm:"):
        return source[4:].split("@", 1)[0] == "pi-web-access"
    # Upgrade the store path on each generation without accumulating entries.
    return source.startswith("/nix/store/") and "-pi-web-access-" in Path(source).name


def install(home, source, defaults, xdg_config_home=None):
    agent = home / ".pi/agent"
    profiles = home / ".pi/profiles"
    for name in ("general", "code", "wiki"):
        (profiles / name).mkdir(parents=True, exist_ok=True)
    agent.mkdir(parents=True, exist_ok=True)
    agent_dirs = [agent] + sorted(p for p in profiles.iterdir() if p.is_dir())
    config_dirs = set(agent_dirs)
    # Upstream prefers XDG_CONFIG_HOME/pi for plain `pi` when XDG is set.
    if xdg_config_home:
        config_dirs.add(Path(xdg_config_home) / "pi")
    for agent_dir in agent_dirs:
        settings_path = agent_dir / "settings.json"
        if settings_path.is_symlink() and not settings_path.exists():
            print(f"Pi: preserving dangling settings link {settings_path}", file=sys.stderr)
            continue
        settings = read_object(settings_path)
        packages = settings.get("packages", [])
        if not isinstance(packages, list):
            raise ValueError(f"{settings_path}: packages must be an array")
        updated = []
        found = False
        for entry in packages:
            if is_web_access(entry):
                if not found:
                    updated.append({**entry, "source": source} if isinstance(entry, dict) else source)
                    found = True
            else:
                updated.append(entry)
        if not found:
            updated.append(source)
        settings["packages"] = updated
        save_object(settings_path, settings)
    for config_dir in sorted(config_dirs):
        path = config_dir / "web-search.json"
        config = read_object(path)
        # These fields override searchRouting, so the managed route must win.
        config.pop("provider", None)
        config.pop("searchProvider", None)
        for key, value in defaults.items():
            if isinstance(value, dict) and isinstance(config.get(key), dict):
                config[key] = {**config[key], **value}
            else:
                config[key] = value
        save_object(path, config)


if __name__ == "__main__":
    install(Path.home(), sys.argv[1], read_object(Path(sys.argv[2])), os.environ.get("XDG_CONFIG_HOME"))
