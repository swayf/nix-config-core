"""Run MAX with a notifications-only proxy to the host session bus."""

import os
import select
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time


def proxy_command(executable, address, path, ready_fd):
    name = "org.freedesktop.Notifications"
    obj = "/org/freedesktop/Notifications"
    # GNOME Shell owns several interfaces under one unique bus name. Do not
    # grant --talk: restrict both the interface and the object path instead.
    return [
        executable, f"--fd={ready_fd}", address, path, "--filter",
        f"--call={name}={name}.*@{obj}",
        f"--call={name}=org.freedesktop.DBus.Introspectable.Introspect@{obj}",
        f"--broadcast={name}={name}.*@{obj}",
    ]


def terminate(process):
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
    process.wait()


def main(proxy_executable, command):
    address = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not address or not runtime:
        raise RuntimeError("run MAX from the logged-in desktop session (D-Bus and XDG_RUNTIME_DIR required)")
    if "," in runtime:
        raise RuntimeError("XDG_RUNTIME_DIR cannot contain commas")
    stopped = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda *_: stopped.set())
    child = proxy = None
    with tempfile.TemporaryDirectory(prefix="hm-max-notify-", dir=runtime) as directory:
        path = os.path.join(directory, "bus")
        parent, ready = socket.socketpair()
        try:
            proxy = subprocess.Popen(
                proxy_command(proxy_executable, address, path, ready.fileno()),
                pass_fds=(ready.fileno(),), stdin=subprocess.DEVNULL,
            )
            ready.close()
            deadline = time.monotonic() + 15
            while not select.select([parent], [], [], 0.2)[0]:
                if stopped.is_set():
                    return 0
                if proxy.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("notification proxy failed to start")
            if not parent.recv(1):
                raise RuntimeError("notification proxy exited before becoming ready")
            # The module always puts the image last, with no image command.
            # command may include the clipboard supervisor before `podman run`.
            run = command[:-1] + [
                "--mount", f"type=bind,src={path},dst=/run/host-notifications,ro",
                "--env", "MAX_NOTIFICATIONS=1",
                "--env", f"MAX_HOST_UID={os.getuid()}",
            ] + command[-1:]
            child = subprocess.Popen(run)
            while child.poll() is None and not stopped.wait(0.2):
                if proxy.poll() is not None:
                    print("MAX notifications: proxy exited; notifications are unavailable",
                          file=sys.stderr, flush=True)
                    # Keep the app running if only notifications fail.
                    while child.poll() is None and not stopped.wait(0.2):
                        pass
                    break
            terminate(child)
            return child.returncode or 0
        finally:
            terminate(child)
            parent.close()  # xdg-dbus-proxy exits when its readiness fd closes.
            ready.close()
            terminate(proxy)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1], sys.argv[2:]))
    except (OSError, RuntimeError) as error:
        print(f"MAX notifications: {error}", file=sys.stderr)
        sys.exit(1)
