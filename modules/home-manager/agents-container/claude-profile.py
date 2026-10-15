"""Explicit-policy session preset persistence; no executable, path or flag defaults."""

from pathlib import Path
import hashlib
import json
import os
import re
import shlex
import sys
import tempfile

# Compatibility ownership marker, not a preset or permission policy.
MARKER = "# Managed by agents-container: Claude profile restore wrapper."
FIELDS = {
    "state_dir", "settings_path", "wrapper_path", "executable", "argv0", "python",
    "profile_env", "presets", "default_profile", "selection_rules", "explicit_options",
    "explicit_flags", "resume_options", "legacy_scripts", "legacy_store_suffixes",
}


def string(value):
    return isinstance(value, str) and bool(value) and "\0" not in value


def strings(value):
    return isinstance(value, list) and all(string(item) for item in value)


def load_policy(path):
    path = Path(path).absolute()
    policy = json.loads(path.read_text())
    if not isinstance(policy, dict) or set(policy) != FIELDS:
        raise ValueError("policy requires exactly the documented fields")
    for key in ("state_dir", "settings_path", "wrapper_path", "executable", "argv0", "python", "profile_env"):
        if not string(policy[key]):
            raise ValueError("invalid policy string: " + key)
    if not Path(policy["executable"]).is_absolute():
        raise ValueError("executable must be absolute")
    if "/" in policy["python"] and not Path(policy["python"]).is_absolute():
        raise ValueError("python must be a command name or absolute path")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", policy["profile_env"]):
        raise ValueError("invalid profile_env")
    presets = policy["presets"]
    if not isinstance(presets, dict) or not presets or not all(
        string(name) and strings(flags) for name, flags in presets.items()
    ):
        raise ValueError("presets must map names to argument lists")
    if not string(policy["default_profile"]) or policy["default_profile"] not in presets:
        raise ValueError("unknown default_profile")
    for key in ("explicit_flags", "explicit_options", "resume_options", "legacy_scripts", "legacy_store_suffixes"):
        if not strings(policy[key]):
            raise ValueError("invalid policy list: " + key)
    for key in ("explicit_options", "resume_options"):
        if any(not item.startswith("-") or item == "--" or "=" in item for item in policy[key]):
            raise ValueError("invalid option name: " + key)
    rules = policy["selection_rules"]
    if not isinstance(rules, list):
        raise ValueError("selection_rules must be a list")
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != {"profile", "flags", "options"}:
            raise ValueError("invalid selection rule fields")
        if not string(rule["profile"]) or rule["profile"] not in presets or not strings(rule["flags"]):
            raise ValueError("invalid selection rule profile/flags")
        if not isinstance(rule["options"], dict) or not all(
            string(key) and key.startswith("-") and key != "--" and "=" not in key and string(value)
            for key, value in rule["options"].items()
        ):
            raise ValueError("invalid selection rule options")
    # Policy-relative destinations remain stable when wrappers run from another cwd.
    # Preserve logical symlink paths. No HOME/XDG expansion or discovery.
    for key in ("state_dir", "settings_path", "wrapper_path"):
        policy[key] = str(path.parent / policy[key])
    return policy


def state_path(policy, session_id):
    key = hashlib.sha256(session_id.encode()).hexdigest()
    return Path(policy["state_dir"]) / (key + ".json")


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            output.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def record(policy):
    # Bookkeeping must never block the application or emit event/prompt content.
    try:
        event = json.load(sys.stdin)
        profile = os.environ.get(policy["profile_env"])
        session_id = event.get("session_id")
        if profile in policy["presets"] and isinstance(session_id, str) and session_id:
            atomic_write(state_path(policy, session_id), json.dumps({"profile": profile}) + "\n")
    except (OSError, ValueError, TypeError, AttributeError):
        pass


def option(args, name):
    for index, arg in enumerate(args):
        if arg == name:
            if index + 1 < len(args) and not args[index + 1].startswith("-"):
                return args[index + 1]
            return None
        if arg.startswith(name + "="):
            return arg[len(name) + 1:]
    return None


def launch(policy, args):
    options = args[:args.index("--")] if "--" in args else args
    profile = policy["default_profile"]
    for rule in policy["selection_rules"]:
        if all(flag in options for flag in rule["flags"]) and all(
            option(options, key) == value for key, value in rule["options"].items()
        ):
            profile = rule["profile"]
            break
    session_id = next((value for name in policy["resume_options"]
                       if (value := option(options, name))), None)
    explicit = any(arg in policy["explicit_flags"] or arg.split("=", 1)[0] in policy["explicit_options"]
                   for arg in options)
    extra = []
    if session_id and not explicit:
        try:
            saved = json.loads(state_path(policy, session_id).read_text()).get("profile")
            if saved in policy["presets"]:
                profile = saved
                extra = policy["presets"][profile]
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    env = dict(os.environ, **{policy["profile_env"]: profile})
    os.execve(policy["executable"], [policy["argv0"], *extra, *args], env)


def managed_record_hook(hook, policy, script, policy_path):
    if hook.get("type") != "command":
        return False
    try:
        args = shlex.split(hook.get("command", ""))
    except ValueError:
        return False
    if args == [policy["python"], str(script), str(policy_path), "record"]:
        return True
    if len(args) != 3 or args[0] != policy["python"] or args[2] != "record":
        return False
    # Only explicitly selected legacy scripts are migrated; other policies/hooks stay.
    path = args[1]
    return path in policy["legacy_scripts"] or (
        path.startswith("/nix/store/")
        and any(path.endswith(suffix) for suffix in policy["legacy_store_suffixes"])
    )


def install(policy, script, policy_path):
    settings_path = Path(policy["settings_path"])
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    command = shlex.join([policy["python"], str(script), str(policy_path), "record"])
    hook = {"matcher": "", "hooks": [{"type": "command", "command": command}]}
    hooks = settings.setdefault("hooks", {}).setdefault("SessionStart", [])
    migrated = []
    for entry in hooks:
        remaining = [item for item in entry.get("hooks", [])
                     if not managed_record_hook(item, policy, script, policy_path)]
        if remaining == entry.get("hooks", []):
            migrated.append(entry)
        elif remaining:
            migrated.append(dict(entry, hooks=remaining))
    migrated.append(hook)
    if migrated != hooks:
        settings["hooks"]["SessionStart"] = migrated
        atomic_write(settings_path, json.dumps(settings, indent=2) + "\n")
    wrapper = Path(policy["wrapper_path"])
    if (not wrapper.exists() and not wrapper.is_symlink()) or (
        wrapper.is_file() and MARKER in wrapper.read_text().splitlines()
    ):
        atomic_write(wrapper, "#!/bin/sh\n" + MARKER + "\nexec " + shlex.join(
            [policy["python"], str(script), str(policy_path), "run"]
        ) + ' "$@"\n')
        wrapper.chmod(0o755)


def main(argv):
    if len(argv) < 2 or argv[1] not in ("install", "record", "run") or (
        argv[1] != "run" and len(argv) != 2
    ) or not argv[0]:
        raise SystemExit("usage: claude-profile.py POLICY_JSON {install|record|run [ARGS...]}")
    policy_path = Path(argv[0]).absolute()
    mode = argv[1]
    try:
        policy = load_policy(policy_path)
    except (OSError, ValueError, TypeError):
        if mode == "record":
            return
        raise SystemExit("invalid or unreadable policy")
    if mode == "record":
        record(policy)
    elif mode == "install":
        # Never resolve image /etc symlinks into ephemeral store paths.
        install(policy, Path(__file__).absolute(), policy_path)
    else:
        launch(policy, argv[2:])


if __name__ == "__main__":
    main(sys.argv[1:])
