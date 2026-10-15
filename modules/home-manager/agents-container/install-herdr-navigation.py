#!/usr/bin/env python3
"""Offline plugin linking and additive TOML bindings with explicit caller policy."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib


FIELDS = {"plugins", "directories", "seeds", "bindings", "backup_suffix", "binding_comment"}


def herdr(*args):
    return subprocess.check_output(["herdr", *args], text=True)


def nonempty(value):
    return isinstance(value, str) and bool(value) and "\x00" not in value


def relative(value):
    return nonempty(value) and not Path(value).is_absolute() and ".." not in Path(value).parts


def validate(policy):
    """Reject malformed policy, including seed TOML, before any external call/write."""
    if not isinstance(policy, dict) or set(policy) != FIELDS:
        raise ValueError("explicit policy fields required")
    for field in ("plugins", "directories", "seeds", "bindings"):
        if not isinstance(policy[field], list):
            raise ValueError(f"{field} must be a list")
    ids = set()
    for identifier in policy["plugins"]:
        if not nonempty(identifier) or identifier in ids:
            raise ValueError("plugins require unique nonempty ids")
        ids.add(identifier)
    if not all(relative(value) and value != "." for value in policy["directories"]):
        raise ValueError("directories must be root-relative children")
    for seed in policy["seeds"]:
        if (not isinstance(seed, dict) or set(seed) != {"path", "text"}
                or not relative(seed["path"]) or seed["path"] == "."
                or not isinstance(seed["text"], str)):
            raise ValueError("seeds require relative paths and TOML text")
        tomllib.loads(seed["text"])
    for binding in policy["bindings"]:
        if (not isinstance(binding, dict) or set(binding) != {"key", "action", "description"}
                or not all(nonempty(value) for value in binding.values())
                or binding["action"].rsplit(".", 1)[0] not in ids):
            raise ValueError("bindings require key/action/description and a selected plugin")
    suffix, comment = policy["backup_suffix"], policy["binding_comment"]
    if not nonempty(suffix) or "/" in suffix or "\n" in suffix:
        raise ValueError("backup_suffix must be a nonempty filename suffix")
    if not nonempty(comment) or not comment.startswith("#") or "\n" in comment or "\r" in comment:
        raise ValueError("binding_comment must be a single TOML comment")


def write_config(path, text, backup_suffix):
    """Validate first, back up once, and atomically replace the symlink target."""
    tomllib.loads(text)
    target = path.resolve(strict=True)
    backup = target.with_name(target.name + backup_suffix)
    if not backup.exists():
        with backup.open("xb") as stream:
            os.chmod(backup, 0o600)
            stream.write(target.read_bytes())
    with tempfile.NamedTemporaryFile(mode="w", dir=target.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            os.chmod(temporary, target.stat().st_mode & 0o777)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


def add_bindings(path, enabled, policy):
    # Do not repair a user-owned dangling symlink or invent a full config.
    if not path.exists():
        return False
    text = path.read_text()
    keys = tomllib.loads(text).get("keys", {})
    commands = keys.get("command", [])
    occupied = {entry.get("key") for entry in commands}
    actions = {entry.get("command") for entry in commands if entry.get("type") == "plugin_action"}
    for value in keys.values():
        if isinstance(value, str):
            occupied.update(re.split(r"[\s,]+", value))
        elif isinstance(value, dict):
            occupied.update(v for v in value.values() if isinstance(v, str))
    additions = []
    for binding in policy["bindings"]:
        key, action, description = (binding[name] for name in ("key", "action", "description"))
        if not enabled.get(action.rsplit(".", 1)[0]) or action in actions:
            continue
        if key in occupied:
            print(f"Preserving occupied shortcut {key}; {action} is available in the action menu.")
            continue
        additions.append(
            f'\n[[keys.command]]\nkey = {json.dumps(key, ensure_ascii=False)}\ntype = "plugin_action"\n'
            f'command = {json.dumps(action, ensure_ascii=False)}\ndescription = {json.dumps(description, ensure_ascii=False)}\n'
        )
    if not additions:
        return False
    write_config(path, text.rstrip() + "\n\n" + policy["binding_comment"] + "\n" + "".join(additions),
                 policy["backup_suffix"])
    return True


def install(policy, root, config, sources):
    validate(policy)
    if len(sources) != len(policy["plugins"]) or not all(
            nonempty(source) and Path(source).is_absolute() for source in sources):
        raise ValueError("one explicit absolute source per plugin is required")
    root, config = Path(root).absolute(), Path(config).absolute()
    if config.exists():
        tomllib.loads(config.read_text())
    plugins = json.loads(herdr("plugin", "list", "--json"))["result"]["plugins"]
    enabled = {}
    for identifier, source in zip(policy["plugins"], sources):
        current = next((p for p in plugins if p["plugin_id"] == identifier), None)
        enabled[identifier] = current is None or current["enabled"]
        if current is None or current["plugin_root"] != source:
            args = ["plugin", "link", source]
            if not enabled[identifier]:
                args.append("--disabled")
            herdr(*args)
    for directory in policy["directories"]:
        (root / directory).mkdir(parents=True, exist_ok=True)
    for seed in policy["seeds"]:
        target = root / seed["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() and not target.is_symlink():
            target.write_text(seed["text"])
    changed = add_bindings(config, enabled, policy)
    if changed and json.loads(herdr("status", "--json"))["server"]["running"]:
        herdr("server", "reload-config")
    # Never stop/restart the server or plugins, download, or execute seed commands.


if __name__ == "__main__":
    if len(sys.argv) < 4:
        raise SystemExit("usage: install-herdr-navigation.py POLICY_JSON ROOT CONFIG SOURCES...")
    install(json.loads(Path(sys.argv[1]).read_text()), sys.argv[2], sys.argv[3], sys.argv[4:])
