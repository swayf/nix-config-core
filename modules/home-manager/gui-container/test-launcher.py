#!/usr/bin/env python3
"""Regression tests for the GUI CLI; no real containers or external requests."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest

LAUNCHER = Path(__file__).with_name("launcher.sh").resolve()


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.launcher = LAUNCHER
        self.home = self.root / "home"
        self.home.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.store = self.root / "store"
        self.store.mkdir()
        self.state = self.root / "podman.json"
        self.state.write_text(json.dumps({"calls": [], "containers": {}, "images": []}))
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.bind(str(self.root / "wayland"))
        self.addCleanup(self.sock.close)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith("GUI_") and k not in ("HM_FLAKE", "GCNT_WAYLAND_SOCKET")}
        self.env.update(
            HOME=str(self.home), PATH=f"{self.bin}:{self.env['PATH']}",
            GUI_PODMAN=str(self.bin / "podman"), GUI_STATE=str(self.root / "state"),
            GUI_SHARED=str(self.root / "shared"), GUI_IMAGE=str(self.bin / "gui stream"),
            GUI_GATEWAY_IMAGE="gateway.tar", GUI_IMAGE_REF="localhost/hm-gui:new",
            GUI_GATEWAY_REF="localhost/hm-gui-gateway:new", GUI_GPU="", GUI_GPU_AUTO="0", GUI_AUDIO="0",
            GUI_SPEC="test", GUI_PROXY="http://127.0.0.1:2081", GUI_WAYLAND_SOCKET="",
            GUI_TIME_ZONE="America/Chicago",
            XDG_RUNTIME_DIR=str(self.root), WAYLAND_DISPLAY="wayland",
            TEST_STATE=str(self.state), TEST_STORE=str(self.store),
            TEST_NIX_ARGS=str(self.root / "nix-args"),
        )
        self.executable("nix", '#!/bin/bash\nprintf "%s\\n" "$@" > "$TEST_NIX_ARGS"\nexit "${TEST_NIX_STATUS:-0}"\n')
        self.executable("gui stream", '''#!/bin/bash
printf 'invoked\\n' >> "$TEST_STATE.stream-calls"
printf 'gui image bytes'
printf 'generator diagnostic\\n' >&2
exit "${TEST_STREAM_STATUS:-0}"
''')
        self.executable("podman", '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
path = Path(os.environ["TEST_STATE"])
s = json.loads(path.read_text())
a = sys.argv[1:]
s["calls"].append(a)
status = 0
if a[0] == "inspect":
    c = s["containers"].get(a[-1])
    if c is None or os.environ.get("TEST_INSPECT_STATUS"): status = 1
    elif a[2] == "{{.State.Status}}": print(c.get("status", "running" if c["running"] else "exited"))
    elif a[2] == "{{.State.Running}}": print(str(c["running"]).lower())
    elif a[2] == "{{.ImageName}}": print(c["image"])
    else: print(c["spec"])
elif a[:2] == ["container", "exists"]:
    status = int(a[2] not in s["containers"])
elif a[0] == "rm":
    status = int(os.environ.get("TEST_RM_STATUS", "0"))
    if "-f" not in a and s["containers"].get(a[-1], {}).get("running"):
        status = 1
    if not status: s["containers"].pop(a[-1], None)
elif a[:2] == ["image", "exists"]:
    status = int(a[2] not in s["images"])
elif a[0] == "info":
    print("crun true" if "OCIRuntime" in a[-1] else os.environ["TEST_STORE"])
elif a[0] == "load":
    if a == ["load"]:
        assert sys.stdin.read() == "gui image bytes"
        image = os.environ["GUI_IMAGE_REF"]
    else:
        assert a == ["load", "-i", "gateway.tar"]
        image = os.environ["GUI_GATEWAY_REF"]
    print("import output")
    status = int(os.environ.get("TEST_LOAD_STATUS", "0"))
    if image == os.environ["GUI_GATEWAY_REF"]:
        status = int(os.environ.get("TEST_GATEWAY_LOAD_STATUS", str(status)))
    if not status:
        s["images"].append(image)
elif a[0] == "images":
    status = int(os.environ.get("TEST_IMAGES_STATUS", "0"))
    if not status: print("\\n".join(s["images"]))
elif a[0] == "rmi":
    status = int(os.environ.get("TEST_RMI_STATUS", "0"))
    if not status: s["images"].remove(a[-1])
elif a[0] == "create":
    name = a[a.index("--name") + 1]
    spec = next((x.split("=", 1)[1] for x in a if x.startswith("home-manager.gui.spec=")), "")
    s["containers"][name] = {"image": a[-1], "running": False, "spec": spec}
    if name == "hm-gui":
        status = int(os.environ.get("TEST_CREATE_STATUS", "0"))
        if status: print("GUI create diagnostic", file=sys.stderr)
elif a[0] == "start":
    s["containers"][a[-1]]["running"] = True
elif a[0] == "exec":
    if a[-1] == "/tmp/ready":
        status = int(os.environ.get("TEST_GATEWAY_FAIL", "0"))
        if status: s["containers"]["hm-gui-gateway"]["running"] = False
    elif "session-ready" in a[-1]:
        status = int(os.environ.get("TEST_SESSION_FAIL", "0"))
        if status: s["containers"]["hm-gui"]["running"] = False
    else:
        print("command output")
elif a[0] == "stats": print("resource counters")
elif a[0] == "logs": print("container logs")
elif a[0] == "ps":
    if "--format" in a:
        status = int(os.environ.get("TEST_PS_STATUS", "0"))
        image = a[a.index("--filter") + 1].removeprefix("ancestor=")
        if not status:
            print("\\n".join(name for name, c in s["containers"].items() if c["image"] == image))
    else: print("container status")
else: raise AssertionError(a)
path.write_text(json.dumps(s))
sys.exit(status)
''')

    def executable(self, name, text):
        path = self.bin / name
        path.write_text(text)
        path.chmod(0o755)

    def launch(self, *args, env=None):
        return subprocess.run(["bash", "-Eeuo", "pipefail", str(self.launcher), *args],
                              cwd=self.root, env=env or self.env,
                              capture_output=True, text=True, timeout=20)

    def data(self):
        return json.loads(self.state.read_text())

    def ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def checkout(self, path):
        path.mkdir(parents=True, exist_ok=True)
        (path / "flake.nix").write_text("{}")
        return path

    def test_help_without_runtime_or_lock(self):
        for command in ("help", "--help", "-h"):
            env = {k: v for k, v in self.env.items() if not k.startswith("GUI_")}
            result = self.ok(self.launch(command, env=env))
            self.assertIn("rebuild|update|shell", result.stdout)
            self.assertIn("chrome [ARG...]", result.stdout)
            self.assertNotIn("reload", result.stdout)
            self.assertIn("without opening windows", result.stdout)
            self.assertNotIn("private desktop", result.stdout)
        self.assertEqual(self.data()["calls"], [])
        self.assertFalse((self.root / "state").exists())

    def test_invalid_arguments_do_not_touch_state(self):
        for args in (("reload",), ("unknown",), ("shell", "extra"), ("exec",), ("update", "extra")):
            self.assertEqual(self.launch(*args).returncode, 2)
        self.assertFalse((self.root / "state").exists())
        self.assertEqual(self.data()["calls"], [])

    def test_profile_help_lists_declared_names_without_runtime(self):
        env = {k: v for k, v in self.env.items() if not k.startswith("GUI_")}
        env["GUI_BROWSER_PROFILES"] = "firefox-personal chrome-work help"
        result = self.ok(self.launch("help", env=env))
        self.assertIn("PROFILE [ARG...]", result.stdout)
        self.assertIn("Profiles: firefox-personal chrome-work help\n", result.stdout)
        self.assertIn("default profile", result.stdout)
        self.assertIn("built-in commands take precedence", result.stdout)
        self.assertEqual(self.data()["calls"], [])
        self.assertFalse((self.root / "state").exists())

    def test_profiles_list_grouped_by_declared_browser_without_runtime(self):
        env = {k: v for k, v in self.env.items() if not k.startswith("GUI_")}
        # Names are arbitrary; grouping must use metadata, not name prefixes.
        env["GUI_BROWSER_PROFILE_LIST"] = "chrome work\nfirefox personal\nbrave firefox-other\nfirefox profiles"
        result = self.ok(self.launch("profiles", env=env))
        self.assertEqual(result.stdout, "BROWSER\tPROFILE\n"
                         "firefox\tdefault\nfirefox\tpersonal\nfirefox\tprofiles\n"
                         "brave\tdefault\nbrave\tfirefox-other\n"
                         "chrome\tdefault\nchrome\twork\n")
        self.assertEqual(result.stderr, "")
        self.assertEqual(self.data()["calls"], [])
        self.assertFalse((self.root / "state").exists())

    def test_profiles_filter_each_browser(self):
        env = dict(self.env, GUI_BROWSER_PROFILE_LIST="firefox personal\nbrave work\nchrome testing")
        for browser, name in (("firefox", "personal"), ("brave", "work"), ("chrome", "testing")):
            with self.subTest(browser=browser):
                result = self.ok(self.launch("profiles", browser, env=env))
                self.assertEqual(result.stdout, f"BROWSER\tPROFILE\n{browser}\tdefault\n{browser}\t{name}\n")
        self.assertEqual(self.data()["calls"], [])
        self.assertFalse((self.root / "state").exists())

    def test_profiles_without_declarations_show_defaults(self):
        env = {k: v for k, v in self.env.items() if not k.startswith("GUI_")}
        for value in (None, ""):
            if value is not None:
                env["GUI_BROWSER_PROFILE_LIST"] = value
            result = self.ok(self.launch("profiles", env=env))
            self.assertEqual(result.stdout, "BROWSER\tPROFILE\nfirefox\tdefault\nbrave\tdefault\nchrome\tdefault\n")
            result = self.ok(self.launch("profiles", "brave", env=env))
            self.assertEqual(result.stdout, "BROWSER\tPROFILE\nbrave\tdefault\n")
        self.assertEqual(self.data()["calls"], [])
        self.assertFalse((self.root / "state").exists())

    def test_profiles_reject_invalid_arguments_without_state(self):
        for args in (("unknown",), ("",), ("--help",), ("firefox", "brave"), ("$(touch marker)",)):
            with self.subTest(args=args):
                result = self.launch("profiles", *args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("Usage: gcnt profiles", result.stderr)
                self.assertEqual(result.stdout, "")
        self.assertFalse((self.root / "state").exists())
        self.assertFalse((self.root / "marker").exists())
        self.assertEqual(self.data()["calls"], [])

    def test_profiles_command_takes_precedence_over_profile_name(self):
        env = dict(self.env, GUI_BROWSER_PROFILES="profiles", GUI_BROWSER_PROFILE_LIST="firefox profiles")
        result = self.ok(self.launch("profiles", "firefox", env=env))
        self.assertIn("firefox\tprofiles\n", result.stdout)
        self.assertEqual(self.data()["calls"], [])
        self.assertFalse((self.root / "state").exists())

    def test_declared_profile_shortcuts_preserve_arguments(self):
        names = ("firefox-personal", "chrome-work", "brave-work", "1_Test")
        env = dict(self.env, GUI_BROWSER_PROFILES=" ".join(names))
        for name in names:
            for args in ((), ("https://example.com/path?a=1&b=2", "two words", "", "$(touch marker)")):
                with self.subTest(name=name, args=args):
                    self.cleanup_fixture(running=True)
                    result = self.ok(self.launch(name, *args, env=env))
                    self.assertEqual(self.data()["calls"][-1], [
                        "exec", "-i", "--user=1000:1000", "hm-gui", "/opt/gui/exec",
                        f"gcnt-profile-{name}", *args,
                    ])
                    self.assertEqual(result.stdout, "command output\n")
                    self.assertIn("GUI container timezone: America/Chicago\n", result.stderr)
                    self.assertFalse((self.root / "marker").exists())
                    self.assertFalse(any(c[0] in ("rm", "create", "start", "load")
                                         for c in self.data()["calls"]))

    def test_unknown_profiles_do_not_touch_state(self):
        for profiles in ("", "firefox-personal chrome-work"):
            env = dict(self.env, GUI_BROWSER_PROFILES=profiles)
            for name in ("firefox-missing", "firefox-person", "chrome-work-extra",
                         "firefox-personal chrome-work", "firefox-*", "$(touch marker)"):
                with self.subTest(profiles=profiles, name=name):
                    self.assertEqual(self.launch(name, env=env).returncode, 2)
        self.assertFalse((self.root / "state").exists())
        self.assertFalse((self.root / "marker").exists())
        self.assertEqual(self.data()["calls"], [])

    def test_default_browsers_take_precedence_over_profile_names(self):
        env = dict(self.env, GUI_BROWSER_PROFILES="firefox brave chrome")
        for browser in ("firefox", "brave", "chrome"):
            with self.subTest(browser=browser):
                self.cleanup_fixture(running=True)
                self.ok(self.launch(browser, "https://example.com", env=env))
                self.assertEqual(self.data()["calls"][-1], [
                    "exec", "-i", "--user=1000:1000", "hm-gui", "/opt/gui/exec",
                    browser, "https://example.com",
                ])

    def test_explicit_profile_exec_remains_available(self):
        self.cleanup_fixture(running=True)
        self.ok(self.launch("exec", "gcnt-profile-firefox", "about:blank",
                            env=dict(self.env, GUI_BROWSER_PROFILES="firefox")))
        self.assertEqual(self.data()["calls"][-1], [
            "exec", "-i", "--user=1000:1000", "hm-gui", "/opt/gui/exec",
            "gcnt-profile-firefox", "about:blank",
        ])

    def test_update_current_checkout_without_gui_environment(self):
        self.checkout(self.root)
        env = {k: v for k, v in self.env.items() if not k.startswith("GUI_")}
        result = self.ok(self.launch("update", env=env))
        self.assertEqual((self.root / "nix-args").read_text().splitlines(),
                         ["flake", "update", "--flake", f"path:{self.root}", "nixpkgs"])
        self.assertIn("#gcnt -- rebuild", result.stdout)
        self.assertEqual(self.data()["calls"], [])
        self.assertFalse((self.root / "state").exists())

    def test_update_override_spaces_and_wsl_output(self):
        path = self.checkout(self.root / "other checkout")
        result = self.ok(self.launch("update", env=dict(self.env, HM_FLAKE=str(path), GUI_FLAKE_OUTPUT="gcnt-wsl")))
        self.assertIn("other\\ checkout#gcnt-wsl", result.stdout)
        self.assertEqual((self.root / "nix-args").read_text().splitlines()[-2:], [f"path:{path}", "nixpkgs"])

    def test_update_fallback_and_failure(self):
        path = self.checkout(self.home / ".config/home-manager")
        self.ok(self.launch("update"))
        self.assertIn(str(path), (self.root / "nix-args").read_text())
        result = self.launch("update", env=dict(self.env, TEST_NIX_STATUS="17"))
        self.assertEqual(result.returncode, 17)
        self.assertNotIn("Lock updated", result.stdout)

    def test_update_missing_checkout(self):
        result = self.launch("update")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("set HM_FLAKE", result.stderr)
        self.assertFalse((self.root / "nix-args").exists())

    def test_logs_when_stopped(self):
        self.ok(self.launch("logs"))
        self.assertFalse(any(c[0] == "logs" for c in self.data()["calls"]))

    def test_stats_does_not_start_desktop(self):
        result = self.ok(self.launch("stats"))
        self.assertIn("resource counters", result.stdout)
        self.assertNotIn("container timezone:", result.stdout + result.stderr)
        self.assertEqual(self.data()["calls"][-1], ["stats", "--no-stream", "hm-gui", "hm-gui-gateway"])
        self.assertFalse(any(c[0] in ("create", "start", "load") for c in self.data()["calls"]))

    def require_devices(self):
        if os.geteuid() == 0 or not os.access("/dev/net/tun", os.R_OK | os.W_OK):
            self.skipTest("up validates non-root and host TUN access")

    def cleanup_fixture(self, running=False):
        stat = (self.root / "wayland").stat()
        spec = hashlib.sha256(
            f"test\n{self.root / 'wayland'}\n{stat.st_dev}:{stat.st_ino}\n\n\n\n".encode()
        ).hexdigest()
        self.state.write_text(json.dumps({
            "calls": [],
            "images": [self.env["GUI_IMAGE_REF"], self.env["GUI_GATEWAY_REF"],
                       "localhost/hm-gui:old", "localhost/hm-gui-gateway:old",
                       "localhost/hm-gui:used", "alpine:old", "localhost/hm-gui-other:old"],
            "containers": {
                "hm-gui": {"image": self.env["GUI_IMAGE_REF"] if running else "localhost/hm-gui:old",
                           "running": running, "spec": spec},
                "hm-gui-gateway": {"image": self.env["GUI_GATEWAY_REF"] if running else "localhost/hm-gui-gateway:old",
                                   "running": running, "spec": ""},
                "other": {"image": "localhost/hm-gui:used", "running": False},
                "hm-gui-other": {"image": "alpine:old", "running": False},
            },
        }))

    def assert_clean_images(self):
        self.assertEqual(set(self.data()["images"]), {
            self.env["GUI_IMAGE_REF"], self.env["GUI_GATEWAY_REF"],
            "localhost/hm-gui:used", "alpine:old", "localhost/hm-gui-other:old",
        })

    def test_inspection_and_down_commands_automatically_clean(self):
        for command in ("status", "stats", "logs", "doctor", "check", "down"):
            with self.subTest(command=command):
                self.cleanup_fixture()
                result = self.launch(command)
                if command not in ("doctor", "check"):
                    self.ok(result)
                self.assertEqual(set(self.data()["containers"]), {"other", "hm-gui-other"})
                self.assert_clean_images()
                self.assertFalse(any(c[0] in ("load", "create", "start") for c in self.data()["calls"]))
                if command != "down":
                    self.assertFalse(any("-f" in c for c in self.data()["calls"] if c[0] == "rm"))

    def test_cached_start_commands_clean_before_exec_without_restarting(self):
        for args in (("up",), ("shell",), ("exec", "true"),
                     ("firefox",), ("brave",), ("chrome",)):
            with self.subTest(args=args):
                self.cleanup_fixture(running=True)
                result = self.ok(self.launch(*args))
                self.assertIn("GUI container timezone: America/Chicago\n", result.stderr)
                self.assertNotIn("container timezone:", result.stdout)
                self.assert_clean_images()
                self.assertEqual(len(self.data()["containers"]), 4)
                calls = self.data()["calls"]
                self.assertFalse(any(c[0] in ("rm", "load", "create", "start") for c in calls))
                if args != ("up",):
                    self.assertEqual(result.stdout, "command output\n")
                    self.assertEqual(calls[-1][0], "exec")
                    self.assertLess(max(i for i, c in enumerate(calls) if c[0] == "rmi"), len(calls) - 1)

    def test_timezone_message_uses_configuration_not_host(self):
        self.cleanup_fixture(running=True)
        result = self.ok(self.launch("up", env=dict(self.env, GUI_TIME_ZONE="Etc/UTC", TZ="Asia/Tokyo")))
        self.assertIn("GUI container timezone: Etc/UTC\n", result.stderr)
        self.assertNotIn("America/Chicago", result.stderr)
        self.assertNotIn("Asia/Tokyo", result.stderr)
        self.assertFalse(any(c[0] in ("create", "start") for c in self.data()["calls"]))

    def test_cold_start_cleans_replaced_containers_and_old_images(self):
        self.require_devices()
        self.cleanup_fixture()
        profile = self.root / "state/home"
        profile.mkdir(parents=True)
        (profile / "marker").write_text("persist")
        self.ok(self.launch("up"))
        self.assert_clean_images()
        data = self.data()
        self.assertEqual(set(data["containers"]), {"hm-gui", "hm-gui-gateway", "other", "hm-gui-other"})
        self.assertTrue(data["containers"]["hm-gui"]["running"])
        self.assertEqual((profile / "marker").read_text(), "persist")
        calls = data["calls"]
        self.assertGreater(min(i for i, c in enumerate(calls) if c[0] == "rmi"),
                           max(i for i, c in enumerate(calls) if c[0] == "start"))

    def gpu_fixture(self, vendors):
        devices = self.root / "dri"
        sysfs = self.root / "drm"
        devices.mkdir()
        for index, vendor in enumerate(vendors, 128):
            name = f"renderD{index}"
            (devices / name).symlink_to("/dev/null")
            directory = sysfs / name / "device"
            directory.mkdir(parents=True)
            if vendor is not None:
                (directory / "vendor").write_text(vendor + "\n")
        self.launcher = self.root / "launcher.sh"
        self.launcher.write_text(LAUNCHER.read_text().replace(
            "/dev/dri/renderD", str(devices / "renderD")).replace(
            "/sys/class/drm", str(sysfs)))
        return devices

    def resolve_gpu(self, env):
        # Run the production selection function without needing host TUN/GPU.
        source = self.launcher.read_text()
        function = source[source.index("select_gpu() {"):source.index("doctor() {")]
        result = subprocess.run(["bash", "-Eeuo", "pipefail", "-c",
                                 function + '\nselect_gpu\nprintf "%s" "$GUI_GPU"'],
                                env=env, capture_output=True, text=True, timeout=5)
        return self.ok(result).stdout

    def test_gpu_detection_selects_accessible_intel_or_amd(self):
        devices = self.gpu_fixture(["0x10de", None, "0x1002", "0x8086"])
        self.assertEqual(self.resolve_gpu(dict(self.env, GUI_GPU_AUTO="1")),
                         str(devices / "renderD130"))

    def test_gpu_detection_falls_back_without_supported_device(self):
        self.gpu_fixture(["0x10de", None])
        self.assertEqual(self.resolve_gpu(dict(self.env, GUI_GPU_AUTO="1")), "")

    def test_gpu_detection_can_be_disabled_or_overridden(self):
        self.gpu_fixture(["0x8086"])
        self.assertEqual(self.resolve_gpu(self.env), "")
        self.assertEqual(self.resolve_gpu(dict(self.env, GUI_GPU_AUTO="1",
                                              GUI_GPU="/explicit-device")), "/explicit-device")

    def test_gpu_and_tmpfs_create_arguments(self):
        self.require_devices()
        devices = self.gpu_fixture(["0x8086"])
        self.ok(self.launch("up", env=dict(self.env, GUI_GPU_AUTO="1")))
        create = next(c for c in self.data()["calls"] if c[:3] == ["create", "--name", "hm-gui"])
        self.assertIn(f"--device={devices / 'renderD128'}", create)
        self.assertIn("--group-add=keep-groups", create)
        self.assertIn("LIBGL_ALWAYS_SOFTWARE=0", create)
        self.assertIn("--shm-size=1g", create)
        self.assertIn("/tmp:rw,nosuid,nodev,size=5g,mode=1777", create)
        self.ok(self.launch("down"))
        self.ok(self.launch("up"))
        create = [c for c in self.data()["calls"] if c[:3] == ["create", "--name", "hm-gui"]][-1]
        self.assertIn("LIBGL_ALWAYS_SOFTWARE=1", create)
        self.assertFalse(any(c.startswith("--device=") for c in create))

    def test_gpu_selection_change_recreates_cached_container(self):
        self.require_devices()
        self.cleanup_fixture(running=True)
        self.gpu_fixture(["0x8086"])
        self.ok(self.launch("up", env=dict(self.env, GUI_GPU_AUTO="1")))
        self.assertTrue(any(c[:3] == ["create", "--name", "hm-gui"] for c in self.data()["calls"]))

    def test_inspection_keeps_running_containers_using_old_images(self):
        self.cleanup_fixture()
        data = self.data()
        for name in ("hm-gui", "hm-gui-gateway"):
            data["containers"][name]["running"] = True
        self.state.write_text(json.dumps(data))
        self.ok(self.launch("status"))
        self.assertEqual(self.data()["containers"], data["containers"])
        self.assertEqual(self.data()["images"], data["images"])
        self.assertFalse(any(c[0] in ("rm", "rmi") for c in self.data()["calls"]))

    def test_cleanup_keeps_paused_containers_and_their_images(self):
        self.cleanup_fixture()
        data = self.data()
        for name in ("hm-gui", "hm-gui-gateway"):
            data["containers"][name].update(running=True, status="paused")
        self.state.write_text(json.dumps(data))
        self.ok(self.launch("status"))
        self.assertEqual(self.data()["containers"], data["containers"])
        self.assertEqual(self.data()["images"], data["images"])
        self.assertFalse(any(c[0] in ("rm", "rmi") for c in self.data()["calls"]))

    def test_automatic_cleanup_failures_are_nonfatal(self):
        for variable in ("TEST_INSPECT_STATUS", "TEST_RM_STATUS", "TEST_IMAGES_STATUS",
                         "TEST_PS_STATUS", "TEST_RMI_STATUS"):
            with self.subTest(variable=variable):
                self.cleanup_fixture()
                result = self.ok(self.launch("status", env=dict(self.env, **{variable: "1"})))
                self.assertIn("Warning:", result.stderr)
                self.assertIn("localhost/hm-gui:old", self.data()["images"])

    def test_rebuild_without_desktop_prunes_only_unused_gui_images(self):
        old_work = "localhost/hm-gui:old"
        old_gateway = "localhost/hm-gui-gateway:old"
        used = "localhost/hm-gui:used"
        unrelated = ["localhost/hm-gui-other:old", "alpine:old"]
        self.state.write_text(json.dumps({
            "calls": [], "images": [old_work, old_gateway, used, *unrelated],
            "containers": {
                "hm-gui": {"image": old_work, "running": True},
                "hm-gui-gateway": {"image": old_gateway, "running": True},
                "other": {"image": used, "running": False},
            },
        }))
        profile = self.root / "state/home"
        profile.mkdir(parents=True)
        (profile / "marker").write_text("persist")
        shared = self.root / "shared"
        shared.mkdir()
        (shared / "marker").write_text("shared")
        env = dict(self.env, GUI_GPU="/missing-device", GUI_AUDIO="1")
        env.pop("XDG_RUNTIME_DIR", None)
        env.pop("WAYLAND_DISPLAY", None)
        self.ok(self.launch("rebuild", env=env))
        data = self.data()
        self.assertEqual(set(data["containers"]), {"other"})
        self.assertEqual(set(data["images"]), {
            self.env["GUI_IMAGE_REF"], self.env["GUI_GATEWAY_REF"], used, *unrelated,
        })
        self.assertFalse(any(c[0] in ("create", "start", "exec") for c in data["calls"]))
        self.assertEqual((profile / "marker").read_text(), "persist")
        self.assertEqual((shared / "marker").read_text(), "shared")
        self.assertFalse(list(self.store.iterdir()))
        before = len(data["calls"])
        self.ok(self.launch("rebuild", env=env))
        self.assertFalse(any(c[0] in ("load", "info", "create", "start")
                             for c in self.data()["calls"][before:]))

    def test_rebuild_import_failure_keeps_existing_containers_and_images(self):
        containers = {"hm-gui": {"image": "localhost/hm-gui:old", "running": True}}
        self.state.write_text(json.dumps({
            "calls": [], "containers": containers, "images": ["localhost/hm-gui:old"],
        }))
        result = self.launch("rebuild", env=dict(self.env, TEST_LOAD_STATUS="19"))
        self.assertEqual(result.returncode, 19)
        self.assertEqual(self.data()["containers"], containers)
        self.assertEqual(self.data()["images"], ["localhost/hm-gui:old"])
        self.assertFalse(any(c[0] in ("rm", "rmi", "create", "start") for c in self.data()["calls"]))
        self.assertFalse(list(self.store.iterdir()))

    def test_rebuild_stream_import_and_cached_skip(self):
        result = self.ok(self.launch("rebuild"))
        self.assertIn("Images ready; containers stopped.", result.stdout)
        self.assertNotIn("import output", result.stdout)
        self.assertNotIn("generator diagnostic", result.stdout)
        self.assertIn("Importing GUI image: finished with status 0", result.stderr)
        self.assertNotIn("generator diagnostic", result.stderr)
        self.assertNotIn("import output", result.stderr)
        self.assertEqual([c for c in self.data()["calls"] if c[0] == "load"],
                         [["load"], ["load", "-i", "gateway.tar"]])
        stream_calls = Path(str(self.state) + ".stream-calls")
        self.assertEqual(stream_calls.read_text(), "invoked\n")
        self.ok(self.launch("rebuild"))
        self.assertEqual(stream_calls.read_text(), "invoked\n")
        self.assertFalse(list((self.root / "state").glob("podman-step.*")))
        with (self.root / "state/lock").open("w") as lock:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_rebuild_generator_failure_keeps_existing_containers(self):
        containers = {"hm-gui": {"image": "localhost/hm-gui:old", "running": True}}
        self.state.write_text(json.dumps({
            "calls": [], "containers": containers, "images": ["localhost/hm-gui:old"],
        }))
        # Podman accepts the bytes and succeeds; pipefail must still catch the generator.
        result = self.launch("rebuild", env=dict(self.env, TEST_STREAM_STATUS="23"))
        self.assertEqual(result.returncode, 23)
        self.assertIn("generator diagnostic", result.stderr)
        self.assertIn("import output", result.stderr)
        self.assertEqual(self.data()["containers"], containers)
        self.assertFalse(any(c[0] in ("rm", "rmi", "create", "start") for c in self.data()["calls"]))
        self.assertNotIn(["load", "-i", "gateway.tar"], self.data()["calls"])
        self.assertFalse(list(self.store.iterdir()))
        self.assertFalse(list((self.root / "state").glob("podman-step.*")))

    def test_rebuild_gateway_import_failure_keeps_existing_containers(self):
        containers = {"hm-gui": {"image": "localhost/hm-gui:old", "running": True}}
        self.state.write_text(json.dumps({
            "calls": [], "containers": containers, "images": ["localhost/hm-gui:old"],
        }))
        result = self.launch("rebuild", env=dict(self.env, TEST_GATEWAY_LOAD_STATUS="29"))
        self.assertEqual(result.returncode, 29)
        self.assertIn("Importing gateway image: finished with status 29", result.stderr)
        self.assertEqual(self.data()["containers"], containers)
        self.assertFalse(any(c[0] in ("rm", "rmi", "create", "start") for c in self.data()["calls"]))
        self.assertFalse(list(self.store.iterdir()))

    def test_rebuild_cleanup_failures_are_nonfatal(self):
        for variable in ("TEST_IMAGES_STATUS", "TEST_PS_STATUS", "TEST_RMI_STATUS"):
            with self.subTest(variable=variable):
                self.state.write_text(json.dumps({
                    "calls": [], "containers": {},
                    "images": [self.env["GUI_IMAGE_REF"], self.env["GUI_GATEWAY_REF"],
                               "localhost/hm-gui:old"],
                }))
                result = self.ok(self.launch("rebuild", env=dict(self.env, **{variable: "1"})))
                self.assertIn("Warning:", result.stderr)
                self.assertIn("localhost/hm-gui:old", self.data()["images"])
                self.assertFalse(any(c[0] in ("create", "start") for c in self.data()["calls"]))

    def test_gui_create_failure_reports_diagnostic_and_cleans_up(self):
        self.require_devices()
        result = self.launch("up", env=dict(self.env, TEST_CREATE_STATUS="31"))
        self.assertEqual(result.returncode, 31)
        self.assertIn("Creating GUI container: finished with status 31", result.stderr)
        self.assertIn("GUI create diagnostic", result.stderr)
        self.assertEqual(self.data()["containers"], {})
        self.assertFalse(list((self.root / "state").glob("podman-step.*")))
        self.assertFalse(any(c == ["start", "hm-gui"] for c in self.data()["calls"]))

    def test_up_mounts_only_host_wayland_and_keeps_sandbox(self):
        self.require_devices()
        result = self.ok(self.launch("up"))
        self.assertIn("Creating gateway: finished with status 0", result.stderr)
        self.assertIn("Creating GUI container: finished with status 0", result.stderr)
        self.assertLess(result.stderr.index("GUI container timezone: America/Chicago\n"),
                        result.stderr.index("Starting gateway: finished with status 0"))
        self.assertLess(result.stderr.index("GUI container timezone: America/Chicago\n"),
                        result.stderr.index("Starting Wayland/WSLg session: finished with status 0"))
        create = next(c for c in self.data()["calls"]
                      if c[0] == "create" and "hm-gui" in c)
        self.assertIn(f"type=bind,src={self.root / 'wayland'},dst=/run/host-wayland,ro=true", create)
        for flag in ("--network=container:hm-gui-gateway", "--cap-drop=ALL",
                     "--cap-add=SYS_CHROOT", "--security-opt=no-new-privileges",
                     "--read-only", "--pid=private", "--ipc=private"):
            self.assertIn(flag, create)
        self.assertFalse(any("X11" in x or "dbus" in x or "DISPLAY=" in x for x in create))
        self.assertFalse(any("seccomp=unconfined" in x or "--privileged" in x for x in create))
        ready = next(c for c in self.data()["calls"] if c[0] == "exec" and "session-ready" in c[-1])
        self.assertIn('test -S "$WAYLAND_DISPLAY"', ready[-1])
        self.assertNotIn("panel-ready", ready[-1])

    def test_wsl_socket_override_and_inode_recreation(self):
        self.require_devices()
        env = dict(self.env, GUI_WAYLAND_SOCKET=str(self.root / "wayland"))
        env.pop("WAYLAND_DISPLAY")
        self.ok(self.launch("up", env=env))
        before = len(self.data()["calls"])
        self.ok(self.launch("up", env=env))
        self.assertFalse(any(c[0] in ("rm", "rmi", "load", "info", "create", "start")
                             for c in self.data()["calls"][before:]))
        for path in ("relative-socket", str(self.root / "missing"), str(self.root)):
            before = len(self.data()["calls"])
            self.assertNotEqual(self.launch("up", env=dict(env, GUI_WAYLAND_SOCKET=path)).returncode, 0)
            self.assertFalse(any(c[0] in ("rm", "create", "start")
                                 for c in self.data()["calls"][before:]))
        replacement = socket.socket(socket.AF_UNIX)
        self.addCleanup(replacement.close)
        replacement.bind(str(self.root / "replacement"))
        (self.root / "replacement").replace(self.root / "wayland")
        before = len(self.data()["calls"])
        self.ok(self.launch("up", env=env))
        self.assertTrue(any(c[0] == "create" for c in self.data()["calls"][before:]))

    def test_waypipe_runtime_override_switches_socket_and_preserves_home(self):
        self.require_devices()
        default = dict(self.env, GUI_WAYLAND_SOCKET=str(self.root / "wayland"))
        self.ok(self.launch("up", env=default))
        marker = self.root / "state/home/marker"
        marker.write_text("persist")
        forwarded = socket.socket(socket.AF_UNIX)
        self.addCleanup(forwarded.close)
        forwarded.bind(str(self.root / "waypipe-display"))
        before = len(self.data()["calls"])
        env = dict(default, GCNT_WAYLAND_SOCKET="", WAYLAND_DISPLAY="waypipe-display")
        self.ok(self.launch("exec", "firefox", env=env))
        create = next(c for c in self.data()["calls"][before:]
                      if c[0] == "create" and "hm-gui" in c)
        self.assertIn(f"type=bind,src={self.root / 'waypipe-display'},dst=/run/host-wayland,ro=true", create)
        self.assertEqual(marker.read_text(), "persist")
        before = len(self.data()["calls"])
        self.ok(self.launch("up", env=env))
        self.assertFalse(any(c[0] in ("rm", "create", "start")
                             for c in self.data()["calls"][before:]))
        # Absolute runtime socket takes priority over both declaration and display.
        self.ok(self.launch("up", env=dict(default, GCNT_WAYLAND_SOCKET=str(self.root / "waypipe-display"))))
        # Invalid forwarding must not silently fall back to the Windows display.
        before = len(self.data()["calls"])
        self.assertNotEqual(self.launch("up", env=dict(env, WAYLAND_DISPLAY="missing")).returncode, 0)
        self.assertFalse(any(c[0] in ("rm", "create", "start")
                             for c in self.data()["calls"][before:]))
        # An ordinary local invocation still returns to the declared WSLg socket.
        self.ok(self.launch("up", env=default))
        self.assertEqual(marker.read_text(), "persist")

    def test_up_reuse_and_rebuild_preserve_data(self):
        self.require_devices()
        self.ok(self.launch("up"))
        self.assertEqual(len(self.data()["containers"]), 2)
        marker = self.root / "state/home/marker"
        marker.write_text("persist")
        before = len(self.data()["calls"])
        self.ok(self.launch("up"))
        self.assertFalse(any(c[0] in ("rm", "rmi", "load", "info", "create", "start")
                             for c in self.data()["calls"][before:]))
        before = len(self.data()["calls"])
        self.ok(self.launch("rebuild"))
        calls = self.data()["calls"][before:]
        self.assertFalse(any(c[0] in ("create", "start", "exec") for c in calls))
        self.assertEqual(self.data()["containers"], {})
        self.assertFalse(any(c[0] == "load" or (c[0] == "info" and "GraphRoot" in c[-1]) for c in calls))
        self.assertFalse(list(self.store.iterdir()))
        self.assertEqual(marker.read_text(), "persist")

    def test_exec_stdout_and_lock_release(self):
        self.require_devices()
        result = self.ok(self.launch("exec", "printf", "hello"))
        self.assertEqual(result.stdout, "command output\n")
        self.assertIn("Importing GUI image", result.stderr)
        self.assertFalse(list((self.root / "state").glob("podman-step.*")))
        with (self.root / "state/lock").open("w") as lock:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_shell_starts_only_bash_with_gui_environment(self):
        self.require_devices()
        self.ok(self.launch("shell"))
        self.assertEqual(self.data()["calls"][-1], [
            "exec", "-i", "--user=1000:1000", "hm-gui", "/opt/gui/exec", "/bin/bash",
        ])
        self.assertFalse(any(c[0] == "exec" and any(x in ("firefox", "brave", "chrome") for x in c)
                             for c in self.data()["calls"]))

    def test_browser_shortcuts_forward_arguments(self):
        self.require_devices()
        for browser in ("firefox", "brave", "chrome"):
            self.ok(self.launch("down"))  # Exercise every shortcut on a cold desktop.
            for args in ((), ("--new-window", "https://example.invalid/path with spaces", ""), ("--help",)):
                result = self.ok(self.launch(browser, *args))
                self.assertEqual(result.stdout, "command output\n")
                self.assertEqual(self.data()["calls"][-1], [
                    "exec", "-i", "--user=1000:1000", "hm-gui", "/opt/gui/exec", browser, *args,
                ])
                self.assertNotIn("--no-sandbox", self.data()["calls"][-1])

    def test_import_failure_preserves_profile(self):
        self.require_devices()
        profile = self.root / "state/home"
        profile.mkdir(parents=True)
        (profile / "marker").write_text("persist")
        result = self.launch("up", env=dict(self.env, TEST_LOAD_STATUS="19"))
        self.assertEqual(result.returncode, 19)
        self.assertIn("import output", result.stderr)
        self.assertEqual(self.data()["containers"], {})
        self.assertFalse(list(self.store.iterdir()))
        self.assertEqual((profile / "marker").read_text(), "persist")

    def test_gateway_and_session_failures_cleanup(self):
        self.require_devices()
        for variable in ("TEST_GATEWAY_FAIL", "TEST_SESSION_FAIL"):
            result = self.launch("up", env=dict(self.env, **{variable: "1"}))
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.data()["containers"], {})
            self.assertTrue((self.root / "state/home").is_dir())


if __name__ == "__main__":
    unittest.main()
