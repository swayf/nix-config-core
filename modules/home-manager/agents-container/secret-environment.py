#!/usr/bin/env python3
"""Stage only declared SOPS keys, then load them without shell evaluation.

The host keeps a mode-0600 JSON file in a mode-0700 runtime (tmpfs) directory
mounted read-only in the workspace. Neither paths' contents nor values enter Podman's config.
"""
import json
import os
from pathlib import Path
import re
import sys
import tempfile


class SecretEnvironmentError(Exception):
    pass


def mapping(value):
    if not isinstance(value, dict) or any(
        not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
        or (entry is not None and not isinstance(entry, str))
        for name, entry in value.items()
    ):
        raise SecretEnvironmentError("invalid secret environment mapping")
    return value


def stage(sources, directory):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    target = directory / "environment.json"
    temporary = None
    try:
        sources = mapping(json.loads(sources))
        values = {}
        for name, source in sources.items():
            values[name] = None  # Missing keys revoke an inherited stale value.
            if source is None:
                continue
            path = Path(source)
            if not path.exists():
                print(f"Warning: agents secret for {name} is missing; variable will be unset.", file=sys.stderr)
                continue
            try:
                # SOPS/API keys may have a terminal newline; keep other bytes
                # literal (quotes, $, backticks, spaces are never shell code).
                value = path.read_text().rstrip("\r\n")
            except (OSError, UnicodeError):
                raise SecretEnvironmentError(f"cannot read agents secret for {name}") from None
            if "\0" in value:
                raise SecretEnvironmentError(f"agents secret for {name} contains a NUL byte")
            if value in ("", "CHANGEME"):
                print(f"Warning: agents secret for {name} is empty or CHANGEME; variable will be unset.", file=sys.stderr)
                continue
            values[name] = value
        fd, temporary = tempfile.mkstemp(prefix=".environment.", dir=directory)
        with os.fdopen(fd, "w") as out:
            json.dump(values, out)
            out.write("\n")
        os.replace(temporary, target)
        temporary = None
    except (OSError, ValueError, SecretEnvironmentError):
        # An invalid/unreadable replacement must never leave old keys usable.
        target.unlink(missing_ok=True)
        raise SecretEnvironmentError("could not stage agents secret environment; stale copy removed") from None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def execute(path, command):
    environment = dict(os.environ)
    try:
        values = mapping(json.loads(path.read_text()))
    except (OSError, ValueError, SecretEnvironmentError):
        raise SecretEnvironmentError("cannot load agents secret environment; run agents up on the host") from None
    for name, value in values.items():
        if value is None:
            environment.pop(name, None)
        elif "\0" in value:
            raise SecretEnvironmentError("invalid agents secret environment value")
        else:
            environment[name] = value
    if not command:
        raise SecretEnvironmentError("missing command")
    os.execvpe(command[0], command, environment)


if __name__ == "__main__":
    try:
        if len(sys.argv) == 4 and sys.argv[1] == "stage":
            stage(sys.argv[2], Path(sys.argv[3]))
        elif len(sys.argv) >= 4 and sys.argv[1] == "exec":
            execute(Path(sys.argv[2]), sys.argv[3:])
        else:
            raise SecretEnvironmentError("usage: secret-environment.py {stage SOURCES DIRECTORY|exec FILE COMMAND...}")
    except SecretEnvironmentError as error:
        print(f"agents: {error}", file=sys.stderr)
        sys.exit(1)
