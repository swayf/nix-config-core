#!/usr/bin/env python3
"""Headless smoke test using real rootless Podman and direct host Wayland.

Usage: python3 test-gui.py GUI_IMAGE_STREAM GATEWAY_ARCHIVE WESTON_EXECUTABLE [--profiles]
Weston is only the headless host; no compositor runs inside the container.
--profiles uses profiles-test.nix's image with declared browser profile fixtures.
Creates only uniquely named test containers and temporary profiles. Does not
require host GUI, Mihomo or credentials, and does not contact public endpoints.
"""
import base64
import json
import os
from pathlib import Path
import selectors
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time


def run(*args, env=None, check=True, timeout=600, input_text=None):
    try:
        result = subprocess.run(args, env=env, input=input_text, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise AssertionError(f"Command timed out after {timeout}s: {args}\n"
                             f"stdout: {error.stdout!r}\nstderr: {error.stderr!r}") from error
    if check and result.returncode:
        raise AssertionError(f"{args}: {result.stdout}\n{result.stderr}")
    return result


def p(*args, **kwargs):
    return run("podman", *args, **kwargs)


def reference(path, *, stream=False):
    if not stream:
        with tarfile.open(path) as archive:
            return json.load(archive.extractfile("manifest.json"))[0]["RepoTags"][0]
    # Read the stream without persisting a multi-GB archive. Drain it completely
    # so the generator can finish normally, and check its exit status too.
    with tempfile.TemporaryFile() as errors, subprocess.Popen(
            [path], stdout=subprocess.PIPE, stderr=errors) as process:
        try:
            tag = None
            with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
                for member in archive:
                    if member.name == "manifest.json":
                        tag = json.load(archive.extractfile(member))[0]["RepoTags"][0]
                        break
            while process.stdout.read(1024 * 1024):
                pass
            status = process.wait(timeout=600)
            errors.seek(0)
            assert status == 0, errors.read().decode(errors="replace")
            assert tag is not None, "Image stream has no manifest.json"
            return tag
        finally:
            process.stdout.close()


class CDPPipe:
    """Minimal Chrome DevTools pipe client; no socket exposure or dependencies."""
    def __init__(self, process):
        self.process = process
        self.buffer = b""
        self.sequence = 0
        self.selector = selectors.DefaultSelector()
        self.selector.register(process.stdout, selectors.EVENT_READ)

    def call(self, method, params=None, session=None):
        self.sequence += 1
        message = dict(id=self.sequence, method=method, params=params or {})
        if session:
            message["sessionId"] = session
        self.process.stdin.write(json.dumps(message).encode() + b"\0")
        self.process.stdin.flush()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            while b"\0" in self.buffer:
                raw, self.buffer = self.buffer.split(b"\0", 1)
                response = json.loads(raw)
                if response.get("id") == self.sequence:
                    assert "error" not in response, response
                    return response["result"]
            if self.selector.select(max(0, deadline - time.monotonic())):
                chunk = os.read(self.process.stdout.fileno(), 1024 * 1024)
                assert chunk, f"Brave DevTools pipe closed during {method}"
                self.buffer += chunk
        raise AssertionError(f"Brave DevTools timeout: {method}")

    def close(self):
        self.selector.close()


image, gateway_image, weston = sys.argv[1:4]
test_profiles = sys.argv[4:] == ["--profiles"]
assert not sys.argv[4:] or test_profiles, "Unknown test arguments"
work = f"hm-gui-smoke-{os.getpid()}"
gateway = work + "-gateway"
# The exact production script; GUI_CONTAINER isolates container names from users.
source = Path(__file__).with_name("launcher.sh").read_text()
parent = None
with tempfile.TemporaryDirectory(prefix="gui-smoke-") as directory:
    root = Path(directory)
    runtime = root / "parent"
    runtime.mkdir(mode=0o700)
    parent_env = dict(os.environ, XDG_RUNTIME_DIR=str(runtime), LIBGL_ALWAYS_SOFTWARE="1",
                      WAYLAND_DEBUG="server")
    env = dict(os.environ,
               GUI_PODMAN="podman", GUI_STATE=str(root / "state"),
               GUI_SHARED=str(root / "shared"), GUI_IMAGE=image,
               GUI_IMAGE_REF=reference(image, stream=True), GUI_GATEWAY_IMAGE=gateway_image,
               GUI_GATEWAY_REF=reference(gateway_image), GUI_GPU="", GUI_AUDIO="0",
               GUI_SPEC="smoke", GUI_PROXY="http://127.0.0.1:2081", GUI_WAYLAND_SOCKET="",
               GUI_CONTAINER=work, GUI_TIME_ZONE="UTC",
               XDG_RUNTIME_DIR=os.environ.get("XDG_RUNTIME_DIR", "/tmp"),
               WAYLAND_DISPLAY=str(runtime / "parent-wayland"))
    script = root / "launcher.sh"
    script.write_text(source)

    def launch(*args, check=True, environment=None, timeout=600, input_text=None):
        return run("bash", "-Eeuo", "pipefail", str(script), *args,
                   env=environment or env, check=check, timeout=timeout, input_text=input_text)

    def applications():
        return p("exec", work, "/bin/bash", "-c",
                 'for path in /proc/[0-9]*/comm; do cat "$path" 2>/dev/null || :; done').stdout

    def assert_no_browsers():
        apps = applications()
        assert "firefox" not in apps and "brave" not in apps, f"Unexpected browser autostart: {apps}"

    def stop_browser(browser):
        p("exec", work, "/bin/bash", "-c",
          'for path in /proc/[0-9]*/comm; do '
          'if [[ $(cat "$path" 2>/dev/null) == *"$1"* ]]; then '
          'pid=${path%/comm}; kill -TERM "${pid##*/}" 2>/dev/null || :; fi; done',
          "stop-browser", browser, check=False)
        for _ in range(100):
            if browser not in applications():
                return
            time.sleep(0.1)
        details = p("exec", work, "/bin/bash", "-c",
                    'for path in /proc/[0-9]*/comm; do '
                    'if [[ $(cat "$path" 2>/dev/null) == *"$1"* ]]; then '
                    'printf "%s\\n" "$path"; '
                    'while read -r key value rest; do '
                    'case "$key" in State:|PPid:) printf "%s %s %s\\n" "$key" "$value" "$rest";; esac; '
                    'done < "${path%/comm}/status"; fi; done',
                    "browser-status", browser, check=False).stdout
        raise AssertionError(f"{browser} did not stop: {details}")

    def gui_shortcut(browser, *flags):
        # Exercise the user's exact command, not just --version or exec.
        launch("down")
        parent_offset = (root / "parent.log").stat().st_size
        log_path = root / f"{browser}-shortcut.log"
        with log_path.open("w") as log:
            process = subprocess.Popen(
                ["bash", "-Eeuo", "pipefail", str(script), browser, *flags, "file:///shared/page.html"],
                env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
            try:
                for _ in range(200):
                    if p("inspect", "--format", "{{.State.Running}}", work,
                         check=False).stdout.strip() == "true":
                        apps = applications()
                        if browser in apps:
                            break
                    if process.poll() is not None:
                        raise AssertionError(log_path.read_text())
                    time.sleep(0.1)
                else:
                    raise AssertionError(f"{browser} GUI did not start: " + log_path.read_text())
                for _ in range(100):
                    # Host-side protocol trace proves the real shortcut created
                    # a toplevel. Host environment variables are not inherited
                    # by podman exec, so client-side WAYLAND_DEBUG would not work.
                    with (root / "parent.log").open() as trace:
                        trace.seek(parent_offset)
                        text = trace.read()
                    if "get_xdg_surface" in text and "get_toplevel" in text:
                        break
                    if process.poll() is not None:
                        raise AssertionError(text)
                    time.sleep(0.1)
                else:
                    raise AssertionError(f"{browser} did not create a host Wayland surface: {text}")
                assert process.poll() is None, log_path.read_text()
                other = "firefox" if browser == "brave" else "brave"
                assert other not in applications(), f"{browser} also started {other}"
            finally:
                stop_browser(browser)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
        print(f"PASS: cold gcnt {browser} starts only {browser} GUI")

    def start_parent():
        process = subprocess.Popen([weston, "--backend=headless", "--renderer=pixman", "--fake-seat",
                                    "--socket=parent-wayland", "--width=1280", "--height=800",
                                    "--no-config", "--shell=kiosk-shell.so"],
                                   env=parent_env, stdout=parent_log, stderr=parent_log)
        for _ in range(100):
            if (runtime / "parent-wayland").is_socket():
                return process
            if process.poll() is not None:
                break
            time.sleep(0.1)
        raise AssertionError("Headless parent failed: " + (root / "parent.log").read_text())

    def container_id():
        return p("inspect", "--format", "{{.Id}}", work).stdout.strip()

    with (root / "parent.log").open("w") as parent_log:
        try:
            parent = start_parent()
            # A cold shell prepares a session, never a desktop or browser.
            launch("shell", input_text='set -e\ntest "$WAYLAND_DISPLAY" = /run/host-wayland\n'
                   'test -z "${DISPLAY:-}"\n', timeout=600)
            time.sleep(1)
            assert_no_browsers()
            launch("up")
            assert_no_browsers()
            apps = applications()
            assert not any(app in apps for app in ("labwc", "sfwbar", "Xwayland")), apps
            assert "catatonit" in apps, "Persistent session must reap orphaned application children"
            launch("exec", "/bin/bash", "-c",
                   'test -f "$XDG_RUNTIME_DIR/session-ready" && '
                   'test -z "${DISPLAY:-}" && test -S "$WAYLAND_DISPLAY" && '
                   'test ! -e "$XDG_RUNTIME_DIR/wayland-gui"')
            launch("exec", "wayland-info")
            print("PASS: direct host Wayland, cold shell/up without desktop or browser autostart")
            assert "thunar" not in applications().lower(), "File manager must not autostart"
            for mime in ("inode/directory", "inode/mount-point"):
                assert launch("exec", "xdg-mime", "query", "default", mime).stdout.strip() == "thunar.desktop"
            assert "thunar.desktop" in launch("exec", "gio", "mime", "inode/directory").stdout
            (root / "shared/download with space.txt").write_text("download fixture")
            parent_offset = (root / "parent.log").stat().st_size
            # The same API browsers use; exercise cold D-Bus activation, not a
            # manually started file manager, and include an escaped filename.
            launch("exec", "dbus-send", "--session", "--print-reply", "--reply-timeout=20000",
                   "--dest=org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                   "org.freedesktop.FileManager1.ShowItems",
                   "array:string:file:///shared/download%20with%20space.txt", "string:")
            for _ in range(100):
                text = (root / "parent.log").read_text()[parent_offset:]
                if "get_xdg_surface" in text and "get_toplevel" in text:
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("ShowItems did not create a host Wayland window: " + text)
            launch("exec", "dbus-send", "--session", "--print-reply", "--reply-timeout=20000",
                   "--dest=org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                   "org.freedesktop.FileManager1.ShowFolders", "array:string:file:///shared", "string:")
            launch("exec", "gio", "open", "/shared", timeout=20)
            launch("exec", "xdg-open", "/shared", timeout=20)
            launch("exec", "thunar", "--quit", timeout=20)
            assert_no_browsers()
            print("PASS: directory MIME handler, FileManager1 activation and Thunar Wayland window")
            if test_profiles:
                launch("exec", "gcnt-profile-smoke-firefox", "--headless", "--screenshot",
                       "/shared/profile-firefox.png", "about:blank", timeout=90)
                assert (root / "shared/profile-firefox.png").read_bytes().startswith(b"\x89PNG")
                profile = root / "state/home/.local/share/gcnt/browser-profiles"
                prefs = profile / "smoke-firefox/user.js"
                assert not prefs.is_symlink()
                assert 'user_pref("browser.shell.checkDefaultBrowser", false);' in prefs.read_text()
                assert "Brave" in launch("exec", "gcnt-profile-smoke-brave", "--version").stdout
                assert (profile / "smoke-brave").is_dir()
                assert_no_browsers()
                print("PASS: declared profile wrappers and writable preferences")
                processes = []
                try:
                    for browser in ("firefox", "brave"):
                        processes.append(subprocess.Popen(
                            ["bash", "-Eeuo", "pipefail", str(script), "exec",
                             f"gcnt-profile-smoke-{browser}", "about:blank"],
                            env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
                    for _ in range(200):
                        apps = applications()
                        if "firefox" in apps and "brave" in apps:
                            break
                        assert all(process.poll() is None for process in processes), "Profile launch failed"
                        time.sleep(0.1)
                    else:
                        raise AssertionError("Profiles did not launch concurrently")
                finally:
                    stop_browser("firefox")
                    stop_browser("brave")
                    for process in processes:
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=10)
                assert_no_browsers()
                print("PASS: Firefox and Brave profiles run concurrently on host Wayland")
            version = launch("brave", "--version", timeout=20).stdout
            assert "Brave" in version, f"Brave shortcut failed: {version}"
            launch("exec", "/bin/bash", "-c", 'test -f "$LOCALE_ARCHIVE"; test "$(stat -c %s "$LOCALE_ARCHIVE")" -lt 5000000; test "$(printf "привет" | wc -m)" -eq 6')
            print("PASS: compact locale archive and UTF-8 shell")
            launch("exec", "/bin/bash", "-c",
                   'test "$WAYLAND_DISPLAY" = /run/host-wayland; '
                   'test -n "$DBUS_SESSION_BUS_ADDRESS"; '
                   'test -S "$WAYLAND_DISPLAY"; '
                   'printf persisted > "$HOME/smoke-marker"')
            # Exercise actual browser content rendering, not just its parent PID.
            (root / "shared/page.html").write_text("<html><body>offline smoke test</body></html>")
            launch("exec", "/bin/mkdir", "-p", "/tmp/firefox-smoke")
            launch("firefox", "--headless", "--no-remote", "--profile", "/tmp/firefox-smoke",
                   "--screenshot", "/shared/page.png", "file:///shared/page.html", timeout=90)
            print("PASS: Firefox shortcut rendered local page")
            assert (root / "shared/page.png").read_bytes().startswith(b"\x89PNG")
            assert "brave" not in applications(), "Firefox shortcut also started Brave"
            launch("shell", input_text='set -e\nmkdir -p /tmp/firefox-shell\n'
                   'firefox --headless --no-remote --profile /tmp/firefox-shell '
                   '--screenshot /shared/shell-firefox.png file:///shared/page.html\n', timeout=90)
            assert (root / "shared/shell-firefox.png").read_bytes().startswith(b"\x89PNG")
            print("PASS: Firefox launched from container shell")
            gui_shortcut("firefox", "--no-remote")
            gui_shortcut("brave", "--no-first-run", "--no-default-browser-check",
                         "--user-data-dir=/tmp/brave-gui")
            # Reuse the cold Brave session above for a full rendering check.
            # The pinned Brave did not complete the headless --screenshot CLI.
            # Capture a real GUI page via DevTools instead, using only a pipe
            # through shell (no listening TCP port). Keep the browser sandbox,
            # Podman seccomp/no-new-privileges and default rendering settings.
            brave_flags = ("--no-first-run", "--no-default-browser-check",
                           "--disable-background-networking", "--disable-component-update",
                           "--user-data-dir=/tmp/brave-smoke")
            with (root / "brave.log").open("w") as brave_log:
                browser = subprocess.Popen([
                    "bash", "-Eeuo", "pipefail", str(script), "shell",
                ], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=brave_log)
                # Feed one shell command; after exec the same stdin/stdout
                # carry DevTools messages. No debug TCP listener is needed.
                shell_command = "exec " + shlex.join(["brave", *brave_flags, "--remote-debugging-pipe"])
                browser.stdin.write((shell_command + " 3<&0 4>&1\n").encode())
                browser.stdin.flush()
                cdp = CDPPipe(browser)
                try:
                    target = cdp.call("Target.createTarget", {"url": "file:///shared/page.html"})["targetId"]
                    assert "firefox" not in applications(), "Brave launch also started Firefox"
                    session = cdp.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
                    for _ in range(100):
                        result = cdp.call("Runtime.evaluate", {
                            "expression": "document.body && document.body.innerText", "returnByValue": True,
                        }, session)
                        if result["result"].get("value") == "offline smoke test":
                            break
                        time.sleep(0.1)
                    else:
                        raise AssertionError(f"Brave did not render the local page: {result}")
                    screenshot = cdp.call("Page.captureScreenshot", {"format": "png"}, session)
                    png = base64.b64decode(screenshot["data"], validate=True)
                    assert png.startswith(b"\x89PNG\r\n\x1a\n")
                    (root / "shared/brave.png").write_bytes(png)
                    cdp.call("Browser.close")
                    assert browser.wait(timeout=10) == 0
                except Exception as error:
                    raise AssertionError("Brave rendering failed: " + (root / "brave.log").read_text()) from error
                finally:
                    cdp.close()
                    if browser.poll() is None:
                        browser.kill()
                    browser.wait(timeout=10)
                    browser.stdin.close()
                    browser.stdout.close()
            print("PASS: GUI Brave launched from container shell rendered a page with sandbox enabled")
            # A real GUI terminal must connect directly to host Wayland.
            launch("shell", input_text='set -e\nbrave --version > /shared/shell-brave-version\n'
                   'foot /bin/bash -c "printf terminal > /shared/terminal-ok"\n', timeout=30)
            assert "Brave" in (root / "shared/shell-brave-version").read_text()
            assert (root / "shared/terminal-ok").read_text() == "terminal"
            print("PASS: container shell has Brave and GUI terminal access")
            assert p("exec", work, "/bin/touch", "/nix/host-write-test", check=False).returncode != 0
            print("PASS: direct Wayland, Firefox, GUI terminal, private bus, read-only image")
            first = container_id()
            launch("up")
            assert container_id() == first
            # WSLg-style: socket lives outside the systemd/Podman runtime dir.
            # Explicit selection must not require WAYLAND_DISPLAY or alter XDG.
            explicit_env = dict(env, GUI_WAYLAND_SOCKET=str(runtime / "parent-wayland"))
            explicit_env.pop("WAYLAND_DISPLAY")
            launch("up", environment=explicit_env)
            assert container_id() == first
            for invalid in ("relative-socket", str(root / "missing-socket")):
                bad = dict(explicit_env, GUI_WAYLAND_SOCKET=invalid)
                assert launch("up", environment=bad, check=False).returncode != 0
                assert container_id() == first
            print("PASS: explicit host socket without WAYLAND_DISPLAY, invalid overrides refused")
            launch("rebuild", environment=explicit_env)
            assert p("container", "exists", work, check=False).returncode != 0
            assert p("container", "exists", gateway, check=False).returncode != 0
            launch("shell", environment=explicit_env, input_text='exit\n')
            assert_no_browsers()
            assert container_id() != first
            assert (root / "state/home/smoke-marker").read_text() == "persisted"
            print("PASS: idempotent up, image recreation, persistent profile and shared files")
            # Unsafe mounts are refused without stopping the running desktop.
            first = container_id()
            for path in (str(Path.home()), "/", str(root / "bad,comma")):
                bad = dict(env, GUI_SHARED=path)
                assert launch("up", check=False, environment=bad).returncode != 0
                assert container_id() == first
            (root / "home-link").symlink_to(Path.home())
            assert launch("up", check=False, environment=dict(env, GUI_SHARED=str(root / "home-link"))).returncode != 0
            print("PASS: HOME, ancestors, comma paths and symlink aliases refused")
            # Weston replaces the socket inode while keeping the same name.
            parent.terminate()
            parent.wait(timeout=10)
            parent = start_parent()
            launch("up", environment=explicit_env)
            assert container_id() != first
            print("PASS: parent compositor restart invalidates stale socket mount")
            # A failing gateway must never leave a workspace behind. Using the
            # desktop image as gateway fails because it has no parent socket.
            bad_gateway = dict(env, GUI_GATEWAY_REF=env["GUI_IMAGE_REF"], GUI_GATEWAY_IMAGE=image)
            assert launch("up", check=False, environment=bad_gateway).returncode != 0
            assert p("container", "exists", work, check=False).returncode != 0
            assert p("container", "exists", gateway, check=False).returncode != 0
            assert (root / "state/home/smoke-marker").read_text() == "persisted"
            print("PASS: gateway initialization failure cleans containers, preserves profile")
        finally:
            p("rm", "-f", work, check=False)
            p("rm", "-f", gateway, check=False)
            if parent is not None and parent.poll() is None:
                parent.terminate()
                parent.wait(timeout=10)
