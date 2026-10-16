"""Clipboard-only bridge: host Xwayland <-> private Xwayland over Podman stdio.

XFixes supplies change notifications; xclip handles X11 selection/INCR transfers.
No host X socket, Xauthority, D-Bus, or clipboard files enter the container.
Exchange is fail-closed and gated by GNOME host focus, not guest assertions.
"""

import base64
import hashlib
import json
import os
import queue
import re
import select
import signal
import subprocess
import sys
import threading
import time


MAX_BYTES = 16 * 1024 * 1024
MAX_FRAME = (MAX_BYTES + 2) // 3 * 4 + 1024
TARGETS = ("image/png", "UTF8_STRING", "text/plain;charset=utf-8", "text/plain")


def log(message):
    print(f"MAX clipboard: {message}", file=sys.stderr, flush=True)


def stop(process):
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
    process.wait()


def decode(frame):
    if not isinstance(frame, dict) or set(frame) != {"target", "data", "epoch"}:
        raise ValueError("invalid clipboard message")
    if type(frame["epoch"]) is not int or frame["epoch"] < 0:
        raise ValueError("invalid focus epoch")
    if not isinstance(frame["data"], str):
        raise ValueError("invalid clipboard payload")
    if frame.get("target") not in ("UTF8_STRING", "image/png"):
        raise ValueError("unsupported clipboard format")
    data = base64.b64decode(frame["data"], validate=True)
    if len(data) > MAX_BYTES:
        raise ValueError("clipboard exceeds 16 MiB")
    if frame["target"] == "UTF8_STRING":
        data.decode("utf-8")
    return data


def fingerprint(target, data):
    return target, hashlib.sha256(data).digest()


def emit(frame):
    sys.stdout.write(json.dumps(frame, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def send(process, frame, stopped):
    """Do not hang container shutdown if a clipboard peer stops reading."""
    payload = memoryview(json.dumps(frame, separators=(",", ":")).encode("ascii") + b"\n")
    deadline = time.monotonic() + 5
    while payload:
        if stopped.is_set() or time.monotonic() >= deadline:
            raise RuntimeError("clipboard peer stopped reading")
        if select.select([], [process.stdin], [], 0.1)[1]:
            try:
                payload = payload[os.write(process.stdin.fileno(), payload):]
            except BlockingIOError:
                pass


def capture(target, limit=MAX_BYTES):
    """Bound both selection transfer time and memory, including INCR transfers."""
    process = subprocess.Popen(
        ["xclip", "-selection", "clipboard", "-out", "-target", target],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    data = bytearray()
    deadline = time.monotonic() + 3
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
                return None
            chunk = os.read(process.stdout.fileno(), min(65536, limit + 1 - len(data)))
            if not chunk:
                return bytes(data) if process.wait(timeout=1) == 0 else None
            data.extend(chunk)
            if len(data) > limit:
                log("selection exceeds size limit; skipped")
                return None
    finally:
        stop(process)
        process.stdout.close()


def snapshot(permitted=None):
    if permitted is not None and not permitted():
        return None
    targets = capture("TARGETS", 65536)
    if targets is None:
        return None
    available = targets.decode("ascii", errors="replace").splitlines()
    for target in TARGETS:
        if target not in available:
            continue
        if permitted is not None and not permitted():
            return None
        data = capture(target)
        if data is None:
            continue
        if target != "image/png":
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            target = "UTF8_STRING"
        return target, data
    return None


def publish(target, data):
    # A memory-backed fd avoids blocking on xclip's stdin and stores no history.
    fd = os.memfd_create("max-clipboard", os.MFD_CLOEXEC)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(data)
        os.lseek(fd, 0, os.SEEK_SET)
        return subprocess.Popen(
            ["xclip", "-selection", "clipboard", "-in", "-target", target, "-quiet"],
            stdin=fd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    finally:
        os.close(fd)


def focus_state(pid):
    """Ask GNOME on the host; errors, lock screen and overview fail closed."""
    if not pid:
        return False, None
    try:
        result = subprocess.run(
            ["gdbus", "call", "--session", "--dest", "org.gnome.Shell",
             "--object-path", "/org/home_manager/MAXClipboardFocus",
             "--method", "org.home_manager.MAXClipboardFocus.GetFocus", str(pid)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=1,
        )
        match = re.fullmatch(r"\((true|false), (?:uint32 )?(\d+)\)\s*", result.stdout)
        if result.returncode == 0 and match:
            return match[1] == "true", int(match[2])
    except (OSError, subprocess.TimeoutExpired):
        pass
    return False, None


def weston_host_pid(podman):
    """Use host PID credentials, never window titles or a guest-supplied PID."""
    try:
        result = subprocess.run(
            [podman, "top", "hm-max", "hpid", "comm"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=5,
        )
        if result.returncode == 0:
            pids = [int(fields[0]) for line in result.stdout.splitlines()
                    if len(fields := line.split()) == 2 and fields[0].isdigit()
                    and fields[1] == "weston"]
            if len(pids) == 1:
                return pids[0]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def control_state(frame):
    if not isinstance(frame, dict) or set(frame) != {"active", "epoch", "focus_serial"}:
        raise ValueError("invalid focus control")
    if type(frame["active"]) is not bool or type(frame["epoch"]) is not int or frame["epoch"] < 0:
        raise ValueError("invalid focus control")
    serial = frame["focus_serial"]
    if frame["active"]:
        if type(serial) is not int or serial < 0:
            raise ValueError("invalid focus serial")
    elif serial is not None:
        raise ValueError("inactive focus control has a serial")
    return frame["active"], frame["epoch"], serial


def can_forward(frame, epoch, serial, pid):
    # Recheck immediately before forwarding: even a fast loss/regain invalidates
    # queued data, and endpoint epochs reject frames from the previous session.
    return (serial is not None and frame["epoch"] == epoch
            and focus_state(pid) == (True, serial))


def endpoint(guest=False, display_path="/tmp/max-runtime/clipboard-display"):
    from Xlib.display import Display
    from Xlib.ext import xfixes

    if guest:
        # Written by Weston's autolaunch script, which knows its actual DISPLAY.
        while True:
            try:
                with open(display_path, encoding="ascii") as stream:
                    name = stream.read().strip()
                if name:
                    os.environ["DISPLAY"] = name
                    break
            except FileNotFoundError:
                pass
            # EOF means the supervisor has stopped. Do not leave an exec helper
            # behind if Weston never reaches autolaunch.
            if select.select([sys.stdin.fileno()], [], [], 0.2)[0]:
                return
        os.environ.pop("XAUTHORITY", None)
    display = Display()
    if not display.has_extension("XFIXES"):
        raise RuntimeError("XFixes is unavailable")
    display.xfixes_query_version()
    selection = display.intern_atom("CLIPBOARD")
    display.xfixes_select_selection_input(
        display.screen().root, selection, xfixes.XFixesSetSelectionOwnerNotifyMask,
    )
    display.sync()
    writers = []
    last = None
    buffer = bytearray()
    dirty = False
    active, epoch, serial = False, 0, None
    awaiting_host = guest
    initial = False
    focus_pid = int(os.environ.get("MAX_FOCUS_PID", "0"))

    def permitted():
        return active and (guest or focus_state(focus_pid) == (True, serial))

    def write_selection(target, data):
        previous_owner = display.get_selection_owner(selection)
        writer = publish(target, data)
        writers.append(writer)
        deadline = time.monotonic() + 2
        while display.get_selection_owner(selection) == previous_owner:
            if writer.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("could not set clipboard selection")
            time.sleep(0.01)

    try:
        emit({"ready": True})
        while True:
            if dirty:
                if permitted() and not awaiting_host:
                    value = snapshot(permitted)
                    if value is None and initial:
                        value = ("UTF8_STRING", b"")  # Initialize guest even for an empty/unsupported host clipboard.
                    # Focus may have changed during an X11 selection transfer.
                    if value is not None and permitted():
                        target, data = value
                        current = fingerprint(target, data)
                        if current != last:
                            last = current
                            emit({"target": target, "data": base64.b64encode(data).decode("ascii"),
                                  "epoch": epoch})
                    elif value is None:
                        last = None
                    initial = False
                dirty = False
            writers[:] = [writer for writer in writers if writer.poll() is None]
            if not display.pending_events():
                readable, _, _ = select.select([display.fileno(), sys.stdin.fileno()], [], [])
            else:
                readable = [display.fileno()]
                if select.select([sys.stdin.fileno()], [], [], 0)[0]:
                    readable.append(sys.stdin.fileno())
            if sys.stdin.fileno() in readable:
                chunk = os.read(sys.stdin.fileno(), 65536)
                if not chunk:
                    return
                buffer.extend(chunk)
                while b"\n" in buffer:
                    line, _, tail = buffer.partition(b"\n")
                    buffer = bytearray(tail)
                    if len(line) > MAX_FRAME:
                        raise ValueError("oversized bridge message")
                    frame = json.loads(line)
                    if isinstance(frame, dict) and "active" in frame:
                        active, epoch, serial = control_state(frame)
                        last = None
                        initial = active and not guest
                        awaiting_host = guest and active
                        dirty = initial  # Host wins on focus gain; guest waits for its baseline.
                        if guest and not active:
                            for writer in writers:
                                stop(writer)
                            writers.clear()
                            write_selection("UTF8_STRING", b"")
                        continue
                    data = decode(frame)
                    if frame["epoch"] != epoch or not permitted():
                        continue
                    awaiting_host = False
                    current = fingerprint(frame["target"], data)
                    if current != last:
                        last = current
                        # Establish ownership before handling queued XFixes events.
                        write_selection(frame["target"], data)
                if len(buffer) > MAX_FRAME:
                    raise ValueError("oversized bridge message")
            while display.pending_events():
                event = display.next_event()
                if active and not awaiting_host and getattr(event, "selection", None) == selection:
                    dirty = True
    finally:
        if guest:
            try:
                write_selection("UTF8_STRING", b"")
            except (OSError, RuntimeError):
                pass
        for writer in writers:
            stop(writer)
        display.close()


def read_frames(process, side, events):
    try:
        while True:
            line = process.stdout.readline(MAX_FRAME + 1)
            if not line:
                break
            if len(line) > MAX_FRAME or not line.endswith(b"\n"):
                raise ValueError("oversized bridge message")
            frame = json.loads(line)
            if frame != {"ready": True}:
                decode(frame)
            events.put((side, frame))
    except (ValueError, KeyError, TypeError):
        log(f"invalid message from {side}; bridge stopped")
    finally:
        events.put((side, None))


def wait_for_container(podman, run, stopped):
    # Inspect is read-only; repeatedly executing helpers during container setup
    # can contend with Podman's startup. There is no total startup deadline.
    announced = False
    started = time.monotonic()
    while run.poll() is None and not stopped.is_set():
        try:
            result = subprocess.run(
                [podman, "inspect", "--format", "{{.State.Running}}", "hm-max"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip() == b"true":
                return True
        except subprocess.TimeoutExpired:
            pass
        if not announced and time.monotonic() - started >= 30:
            log("waiting for container startup; clipboard will connect when ready")
            announced = True
        stopped.wait(2)
    return False


def supervise(podman, arguments):
    stopped = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda *_: stopped.set())
    run = subprocess.Popen([podman, *arguments], stdin=subprocess.DEVNULL)
    endpoints = {}
    try:
        if not wait_for_container(podman, run, stopped):
            return run.poll() or 0
        pid = None
        while run.poll() is None and not stopped.is_set():
            pid = weston_host_pid(podman)
            if pid is not None:
                break
            stopped.wait(0.2)
        if pid is None:
            return run.poll() or 0
        commands = {
            "guest": [podman, "exec", "-i", "hm-max", "python3",
                      "/usr/local/bin/clipboard.py", "guest"],
            "host": [sys.executable, __file__, "host"],
        }
        events = queue.Queue(maxsize=4)
        for side, command in commands.items():
            env = dict(os.environ, MAX_FOCUS_PID=str(pid)) if side == "host" else None
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
            os.set_blocking(process.stdin.fileno(), False)
            endpoints[side] = process
            threading.Thread(target=read_frames, args=(process, side, events), daemon=True).start()
        ready_sides = set()
        serial = None
        epoch = 0
        initialized = False
        log("focus-gated clipboard: enable the MAX Clipboard Focus Gate GNOME extension")
        while run.poll() is None and not stopped.is_set():
            if len(ready_sides) == 2:
                focused, current_serial = focus_state(pid)
                current_serial = current_serial if focused else None
                if not initialized or current_serial != serial:
                    initialized = True
                    serial = current_serial
                    epoch += 1
                    control = {"active": serial is not None, "epoch": epoch, "focus_serial": serial}
                    for process in endpoints.values():
                        send(process, control, stopped)
                    log("exchange active" if serial is not None else "exchange paused; guest clipboard cleared")
            try:
                side, frame = events.get(timeout=0.2)
            except queue.Empty:
                continue
            if frame is None:
                raise RuntimeError(f"{side} clipboard endpoint exited")
            if frame == {"ready": True}:
                ready_sides.add(side)
                continue
            if len(ready_sides) == 2 and can_forward(frame, epoch, serial, pid):
                other = endpoints["guest" if side == "host" else "host"]
                send(other, frame, stopped)
    except (OSError, RuntimeError) as error:
        log(str(error))
        log("MAX remains running, but clipboard exchange is unavailable")
    finally:
        for process in endpoints.values():
            # EOF lets the guest clean up xclip, unlike killing the Podman client.
            try:
                process.stdin.close()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                stop(process)
        while run.poll() is None and not stopped.wait(0.2):
            pass
        if run.poll() is None:
            # Podman forwards SIGTERM to the container's init process.
            stop(run)
    return run.returncode or 0


if __name__ == "__main__":
    try:
        if sys.argv[1] == "supervise":
            sys.exit(supervise(sys.argv[2], sys.argv[3:]))
        endpoint(guest=sys.argv[1] == "guest")
    except (OSError, RuntimeError, ValueError) as error:
        log(str(error))
        sys.exit(1)
