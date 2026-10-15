#!/usr/bin/env python3
"""Offline plugin linking, initial config seeds and additive caller-owned bindings."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib

FIELDS = {"plugins", "defaults", "bindings", "backup_suffix", "binding_comment"}


def herdr(*args):
    return subprocess.check_output(["herdr", *args], text=True)


def nonempty(value):
    return isinstance(value, str) and bool(value) and "\x00" not in value


def validate(policy):
    """Reject incomplete policy and invalid seed TOML before calls or writes."""
    if not isinstance(policy, dict) or set(policy) != FIELDS:
        raise ValueError("explicit policy fields required")
    for field in ("plugins", "defaults", "bindings"):
        if not isinstance(policy[field], list):
            raise ValueError(f"{field} must be a list")
    ids = set()
    for identifier in policy["plugins"]:
        if not nonempty(identifier) or identifier in ids:
            raise ValueError("plugins require unique nonempty ids")
        ids.add(identifier)
    seeded = set()
    for seed in policy["defaults"]:
        if (not isinstance(seed, dict) or set(seed) != {"plugin", "text"}
                or not nonempty(seed["plugin"]) or seed["plugin"] not in ids
                or seed["plugin"] in seeded or not isinstance(seed["text"], str)):
            raise ValueError("defaults require unique selected plugins and TOML text")
        tomllib.loads(seed["text"])
        seeded.add(seed["plugin"])
    for binding in policy["bindings"]:
        if (not isinstance(binding, dict) or set(binding) != {"key", "action", "description"}
                or not all(nonempty(value) for value in binding.values())
                or binding["action"].rsplit(".", 1)[0] not in ids):
            raise ValueError("bindings require key/action/description and a selected plugin")
    suffix, comment = policy["backup_suffix"], policy["binding_comment"]
    if not nonempty(suffix) or "/" in suffix or "\n" in suffix or "\r" in suffix:
        raise ValueError("backup_suffix must be a nonempty filename suffix")
    if not nonempty(comment) or not comment.startswith("#") or "\n" in comment or "\r" in comment:
        raise ValueError("binding_comment must be a single TOML comment")


def write_config(path, text, backup_suffix):
    """Validate, back up once, then atomically replace the symlink target."""
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


def add_bindings(config, enabled, policy):
    if not config.exists():
        return False  # Do not replace a dangling user-owned symlink.
    text = config.read_text()
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
            print(f"Preserving occupied shortcut {key}; use the action menu for {action}.")
            continue
        additions.append(
            f'\n[[keys.command]]\nkey = {json.dumps(key, ensure_ascii=False)}\ntype = "plugin_action"\n'
            f'command = {json.dumps(action, ensure_ascii=False)}\ndescription = {json.dumps(description, ensure_ascii=False)}\n'
        )
    if not additions:
        return False
    write_config(config, text.rstrip() + "\n\n" + policy["binding_comment"] + "\n" + "".join(additions),
                 policy["backup_suffix"])
    return True


def install(policy, config, sources):
    validate(policy)
    if len(sources) != len(policy["plugins"]) or not all(
            nonempty(source) and Path(source).is_absolute() for source in sources):
        raise ValueError("one explicit absolute source per plugin is required")
    if not isinstance(config, (str, Path)) or not str(config):
        raise ValueError("explicit config path required")
    config = Path(config).absolute()
    if config.exists():
        tomllib.loads(config.read_text())  # Fail before any plugin/config changes.
    plugins = json.loads(herdr("plugin", "list", "--json"))["result"]["plugins"]
    enabled = {}
    for plugin, source in zip(policy["plugins"], sources):
        current = next((p for p in plugins if p["plugin_id"] == plugin), None)
        enabled[plugin] = current is None or current["enabled"]
        if current is None or current["plugin_root"] != source:
            args = ["plugin", "link", source]
            if not enabled[plugin]:
                args.append("--disabled")
            herdr(*args)
    for seed in policy["defaults"]:
        directory = Path(herdr("plugin", "config-dir", seed["plugin"]).strip())
        path = directory / "config.toml"
        directory.mkdir(parents=True, exist_ok=True)
        if not path.exists() and not path.is_symlink():
            path.write_text(seed["text"])
    if add_bindings(config, enabled, policy):
        herdr("config", "check")
        if json.loads(herdr("status", "--json"))["server"]["running"]:
            herdr("server", "reload-config")
    # Never invoke plugin actions, downloads, or stop/restart the server or panes.


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit("usage: install-herdr-review-plugins.py POLICY_JSON CONFIG SOURCES...")
    install(json.loads(Path(sys.argv[1]).read_text()), sys.argv[2], sys.argv[3:])
