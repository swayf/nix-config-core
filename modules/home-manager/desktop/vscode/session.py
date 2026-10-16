"""Restore Nix settings on a cold VS Code launch, leaving writable session copies."""
import argparse
import fcntl
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


def editor_running(proc=Path("/proc")):
    # Match this user's Electron processes, including an older package version.
    # Another user's editor must not prevent our own settings reset.
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            if (entry / "comm").read_text().strip() != "code":
                continue
            if "State:\tZ" not in (entry / "status").read_text():
                return True
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return False


def restore(defaults, user_data):
    for source in sorted(defaults.iterdir()):
        directory = user_data / "User"
        if source.name != "default":
            directory = directory / "profiles" / source.name
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "settings.json"
        # Never write through an old Home Manager symlink into /nix/store.
        fd, temporary = tempfile.mkstemp(prefix=".settings-nix-", dir=directory)
        try:
            with os.fdopen(fd, "wb") as stream, source.open("rb") as baseline:
                shutil.copyfileobj(baseline, stream)
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)


def bypass_reset(arguments):
    # Queries must not discard session changes. Explicit alternate data dirs
    # are outside this configuration and must not reset the managed directory.
    queries = {
        "--version", "-v", "--help", "-h", "--status", "-s",
        "--list-extensions", "--locate-extension", "--install-extension",
        "--uninstall-extension", "--update-extensions", "--telemetry",
        "--user-data-dir",
    }
    return any(arg.split("=", 1)[0] in queries for arg in arguments)


def launch(defaults, user_data, editor, arguments):
    if bypass_reset(arguments):
        os.execv(editor, [editor, *arguments])
    user_data.mkdir(parents=True, exist_ok=True)
    # Serialize cold launches until Electron appears; do not hold this lock
    # through --wait, which would prevent opening another window.
    with (user_data / ".nix-settings.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not editor_running():
            restore(defaults, user_data)
        child = subprocess.Popen([editor, *arguments])
        deadline = time.monotonic() + 15
        while not editor_running() and child.poll() is None:
            if time.monotonic() >= deadline:
                break
            time.sleep(0.05)
    return child.wait()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--defaults-dir", type=Path, required=True)
    parser.add_argument("--user-data-dir", type=Path, required=True)
    parser.add_argument("--editor", required=True)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    arguments = args.arguments
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    return launch(args.defaults_dir, args.user_data_dir, args.editor, arguments)


if __name__ == "__main__":
    raise SystemExit(main())
