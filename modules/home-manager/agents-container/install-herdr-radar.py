#!/usr/bin/env python3
"""Explicit-policy TOML updates and offline linking with serial plugin actions."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import tomllib

FIELDS = {"plugin", "literal_tables", "main_values", "plugin_seed", "plugin_values",
          "backup_suffix", "actions"}


def herdr(*args):
    return subprocess.check_output(["herdr", *args], text=True)


def nonempty(value):
    return isinstance(value, str) and bool(value) and "\x00" not in value


def validate_values(values):
    if not isinstance(values, list):
        raise ValueError("owned values must be an ordered list of key/value entries")
    seen = set()
    for entry in values:
        if not isinstance(entry, dict) or set(entry) != {"key", "value"}:
            raise ValueError("owned values require key/value entries")
        key, value = entry["key"], entry["value"]
        if (not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", key)
                or not nonempty(value) or "\n" in value or "\r" in value):
            raise ValueError("owned values require bare keys and single-line TOML literals")
        if key in seen:
            raise ValueError("duplicate owned key")
        seen.add(key)
        parsed = tomllib.loads(f"{key} = {value}\n")
        if set(parsed) != {key}:
            raise ValueError("one TOML value per owned key required")


def validate(policy):
    if not isinstance(policy, dict) or set(policy) != FIELDS:
        raise ValueError("explicit policy fields required")
    if not nonempty(policy["plugin"]):
        raise ValueError("explicit plugin id required")
    tables, updates = policy["literal_tables"], policy["main_values"]
    if not isinstance(tables, list) or not isinstance(updates, list):
        raise ValueError("literal_tables and main_values must be lists")
    def table_name(value):
        return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*", value)
    if not all(table_name(table) for table in tables) or len(set(tables)) != len(tables):
        raise ValueError("literal_tables require unique bare table names")
    seen = set()
    for update in updates:
        if (not isinstance(update, dict) or set(update) != {"table", "values"}
                or not table_name(update["table"]) or update["table"] in seen):
            raise ValueError("main_values require unique tables and values")
        seen.add(update["table"])
        validate_values(update["values"])
    if not isinstance(policy["plugin_seed"], str):
        raise ValueError("explicit plugin seed TOML required")
    tomllib.loads(policy["plugin_seed"])
    validate_values(policy["plugin_values"])
    suffix = policy["backup_suffix"]
    if not nonempty(suffix) or any(char in suffix for char in ("/", "\n", "\r")):
        raise ValueError("backup_suffix must be a filename suffix")
    actions = policy["actions"]
    if (not isinstance(actions, dict) or set(actions) != {"stop", "configure", "start"}
            or not all(nonempty(value) for value in actions.values())):
        raise ValueError("explicit stop/configure/start action suffixes required")


def invoke(plugin, action):
    # Invocations queue work. Wait for completion before issuing the next action.
    log_id = json.loads(herdr("plugin", "action", "invoke", plugin + "." + action))["result"]["log"]["log_id"]
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        logs = json.loads(herdr("plugin", "log", "list", "--plugin", plugin, "--limit", "100"))["result"]["logs"]
        log = next((entry for entry in logs if entry["log_id"] == log_id), None)
        if log and log["status"] == "succeeded":
            return
        if log and log["status"] != "running":
            raise RuntimeError(f"{plugin} {action}: {log}")
        time.sleep(0.2)
    raise TimeoutError(f"{plugin} {action}: action did not finish in 60 seconds")


def set_values(text, table, values):
    """Update owned keys only; validate before writing any user config."""
    tomllib.loads(text)
    if table:
        header = re.search(r"^\s*\[" + re.escape(table) + r"\][ \t]*(?:#.*)?$", text, re.MULTILINE)
        if header is None:
            text += "\n[" + table + "]\n"
            start = len(text)
        else:
            start = header.end() + (text[header.end():header.end() + 1] == "\n")
    else:
        start = 0
    next_table = re.search(r"^[ \t]*\[", text[start:], re.MULTILINE)
    end = start + next_table.start() if next_table else len(text)
    body = text[start:end]
    for entry in values:
        key, value = entry["key"], entry["value"]
        line = f"{key} = {value}\n"
        pattern = r"^[ \t]*" + re.escape(key) + r"[ \t]*=.*(?:\n|$)"
        if re.search(pattern, body, re.MULTILINE):
            body = re.sub(pattern, lambda _: line, body, count=1, flags=re.MULTILINE)
        else:
            body += ("\n" if body and not body.endswith("\n") else "") + line
    updated = text[:start] + body + text[end:]
    tomllib.loads(updated)
    return updated


def save_config(path, original, updated, suffix):
    if original == updated:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_name(path.name + suffix)
    if original and not backup.exists():
        with open(backup, "x", opener=lambda p, flags: os.open(p, flags, 0o600)) as out:
            out.write(original)
    # Preserve user-owned links and existing target permissions.
    path.write_text(updated)
    return True


def read_config(path):
    if path.is_symlink() and not path.exists():
        raise ValueError(f"{path}: dangling link; refusing to replace it")
    original = path.read_text() if path.exists() else ""
    tomllib.loads(original)
    return original


def prepare_config(policy, config, plugin_config):
    original = read_config(config)
    updated = original
    for table in policy["literal_tables"]:
        if not re.search(r"^\s*\[" + re.escape(table) + r"\]\s*$", updated, re.MULTILINE):
            updated += "\n[" + table + "]\n"
    for update in policy["main_values"]:
        updated = set_values(updated, update["table"], update["values"])
    tomllib.loads(updated)
    plugin_original = read_config(plugin_config)
    plugin_updated = set_values(plugin_original or policy["plugin_seed"], "", policy["plugin_values"])
    # Validate both files before touching either. This is not an I/O transaction.
    changed = save_config(config, original, updated, policy["backup_suffix"])
    return save_config(plugin_config, plugin_original, plugin_updated, policy["backup_suffix"]) or changed


def install(policy, config, plugin_config, source):
    validate(policy)
    for path in (config, plugin_config):
        if not isinstance(path, (str, Path)) or not str(path):
            raise ValueError("explicit config paths required")
    if not nonempty(source) or not Path(source).is_absolute():
        raise ValueError("explicit absolute plugin source required")
    config, plugin_config = Path(config).absolute(), Path(plugin_config).absolute()
    if config == plugin_config or config.resolve() == plugin_config.resolve():
        raise ValueError("main and plugin config must differ")
    config_changed = prepare_config(policy, config, plugin_config)
    plugin = policy["plugin"]
    plugins = json.loads(herdr("plugin", "list", "--json"))["result"]["plugins"]
    current = next((p for p in plugins if p["plugin_id"] == plugin), None)
    enabled = current is None or current["enabled"]
    changed = current is None or current["plugin_root"] != source
    if changed:
        args = ["plugin", "link", source]
        if not enabled:
            args.append("--disabled")
        herdr(*args)
    if enabled and json.loads(herdr("status", "--json"))["server"]["running"]:
        if config_changed or (changed and current is not None):
            invoke(plugin, policy["actions"]["stop"])
            invoke(plugin, policy["actions"]["configure"])
        invoke(plugin, policy["actions"]["start"])


if __name__ == "__main__":
    if len(sys.argv) != 5:
        raise SystemExit("usage: install-herdr-radar.py POLICY_JSON CONFIG PLUGIN_CONFIG SOURCE")
    install(json.loads(Path(sys.argv[1]).read_text()), *sys.argv[2:])
