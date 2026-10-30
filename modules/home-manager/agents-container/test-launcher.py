"""Test launcher updates, credential sharing and cleanup without real containers."""

from pathlib import Path
import ctypes
import json
import os
import signal
import socket
import subprocess
import tempfile
import time
import unittest


def bounded_launcher(args, *, env, cwd=None, timeout=15):
    """Own one fixture group and reap its children, including adopted orphans."""
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    if libc.prctl(37, ctypes.byref(previous), 0, 0, 0) or libc.prctl(36, 1, 0, 0, 0):
        raise OSError(ctypes.get_errno(), "fixture subreaper setup failed")
    proc = None
    try:
        proc = subprocess.Popen(args, env=env, cwd=cwd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, start_new_session=True)
        timed_out = False
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            # This group contains only this fixture's launcher/children.
            os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate(timeout=3)
        return subprocess.CompletedProcess(args, 124 if timed_out else proc.returncode,
                                           stdout, stderr + ("fixture timed out\n" if timed_out else ""))
    finally:
        try:
            if proc is not None:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=3)
                deadline = time.monotonic() + 3
                while True:
                    try:
                        child, _ = os.waitpid(-proc.pid, os.WNOHANG)
                    except ChildProcessError:
                        break
                    if child == 0:
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        if time.monotonic() >= deadline:
                            raise RuntimeError("fixture process group did not settle")
                        time.sleep(0.01)
        finally:
            if libc.prctl(36, previous.value, 0, 0, 0):
                raise OSError(ctypes.get_errno(), "fixture subreaper restore failed")


def launcher_source(command_name="agents"):
    return Path(__file__).with_name("launcher.sh").read_text().replace(
        "@workspaceName@", "agents-dallas").replace(
        "@gatewayName@", "agents-dallas-gateway").replace(
        "@projectDirectoryName@", "projects").replace(
        "@shellCommand@", "herdr").replace(
        "@updateCommand@", 'nix flake update --flake "path:$flake" llm-agents').replace(
        "@commandName@", command_name).replace("@proxyLabel@", "US")


class LauncherUpdateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.cwd = self.base / "cwd"
        self.cwd.mkdir()
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.calls = self.base / "nix-args"
        nix = self.bin / "nix"
        nix.write_text(
            '#!/bin/bash\n'
            'printf "%s\\n" "$@" > "$TEST_NIX_ARGS"\n'
            'exit "${TEST_NIX_STATUS:-0}"\n'
        )
        nix.chmod(0o755)
        self.env = dict(os.environ)
        for key in list(self.env):
            if key.startswith("AGENTS_") or key == "HM_FLAKE":
                self.env.pop(key)
        self.env.update(
            HOME=str(self.home),
            PATH=f"{self.bin}:{self.env['PATH']}",
            TEST_NIX_ARGS=str(self.calls),
        )
        self.launcher = self.base / "launcher.sh"
        self.launcher.write_text(launcher_source())

    def checkout(self, path):
        path.mkdir(parents=True, exist_ok=True)
        (path / "flake.nix").write_text("{}\n")
        return path

    def run_update(self, *args):
        # Match writeShellApplication's strict mode. Unset AGENTS_* proves
        # update does not touch Podman, persistent state or the lifecycle lock.
        return subprocess.run(
            ["bash", "-euo", "pipefail", str(self.launcher), "update", *args],
            cwd=self.cwd, env=self.env, capture_output=True, text=True,
        )

    def assert_updated(self, result, checkout):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls.read_text().splitlines(), [
            "flake", "update", "--flake", f"path:{checkout.resolve()}", "llm-agents",
        ])
        self.assertIn("-- rebuild", result.stdout)

    def test_current_checkout(self):
        self.checkout(self.cwd)
        self.assert_updated(self.run_update(), self.cwd)

    def test_home_fallback(self):
        checkout = self.checkout(self.home / ".config" / "home-manager")
        self.assert_updated(self.run_update(), checkout)

    def test_override_wins_and_handles_spaces(self):
        self.checkout(self.cwd)
        checkout = self.checkout(self.base / "other checkout")
        self.env["HM_FLAKE"] = str(checkout)
        result = self.run_update()
        self.assert_updated(result, checkout)
        self.assertIn("other\\ checkout#agents", result.stdout)

    def test_wsl_update_recommends_wsl_output(self):
        self.checkout(self.cwd)
        self.env["AGENTS_FLAKE_OUTPUT"] = "agents-wsl"
        result = self.run_update()
        self.assert_updated(result, self.cwd)
        self.assertIn("#agents-wsl -- rebuild", result.stdout)

    def test_missing_checkout(self):
        result = self.run_update()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("set HM_FLAKE", result.stderr)
        self.assertFalse(self.calls.exists())

    def test_rejects_extra_arguments(self):
        self.checkout(self.cwd)
        result = self.run_update("nixpkgs")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.calls.exists())

    def test_update_failure_is_propagated(self):
        self.checkout(self.cwd)
        self.env["TEST_NIX_STATUS"] = "17"
        result = self.run_update()
        self.assertEqual(result.returncode, 17)
        self.assertNotIn("Lock updated", result.stdout)


class LauncherDoctorTest(unittest.TestCase):
    """Run real doctor logic against fake device files and Podman mappings."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        for name, body in {
            "id": "echo 1000\n",
            "podman": 'if [[ $1 == info ]]; then echo "crun true"; '
                      'else echo "0 100000 65537"; fi\n',
        }.items():
            stub = self.bin / name
            stub.write_text("#!/bin/bash\n" + body)
            stub.chmod(0o755)
        source = launcher_source()
        self.devices = {}
        for name in ("kvm", "fuse", "net/tun", "dri/renderD128"):
            device = self.base / name
            device.parent.mkdir(parents=True, exist_ok=True)
            self.devices[name] = device
            source = source.replace("/dev/" + name, str(device))
        for name in ("fuse", "net/tun"):
            self.devices[name].touch()
        self.launcher = self.base / "launcher.sh"
        self.launcher.write_text(source)
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                        AGENTS_PODMAN=str(self.bin / "podman"),
                        AGENTS_STATE=str(self.base / "state"), AGENTS_DEVICES="",
                        AGENTS_KVM="1")

    def doctor(self):
        return subprocess.run(["bash", "-euo", "pipefail", str(self.launcher), "doctor"],
                              env=self.env, capture_output=True, text=True)

    def test_linux_requires_kvm_by_default(self):
        self.env.pop("AGENTS_KVM")
        result = self.doctor()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(self.devices["kvm"]), result.stderr)
        self.devices["kvm"].touch()
        self.assertEqual(self.doctor().returncode, 0)

    def test_wsl_does_not_require_kvm_or_gpu(self):
        self.env["AGENTS_KVM"] = "0"
        result = self.doctor()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_wsl_still_requires_tun_and_fuse(self):
        self.env["AGENTS_KVM"] = "0"
        for name in ("net/tun", "fuse"):
            with self.subTest(device=name):
                self.devices[name].unlink()
                result = self.doctor()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(str(self.devices[name]), result.stderr)
                self.devices[name].touch()

    def test_configured_gpu_is_required(self):
        self.env["AGENTS_KVM"] = "0"
        self.env["AGENTS_DEVICES"] = str(self.devices["dri/renderD128"])
        result = self.doctor()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(self.env["AGENTS_DEVICES"], result.stderr)
        self.devices["dri/renderD128"].touch()
        self.assertEqual(self.doctor().returncode, 0)


class LauncherImageCleanupTest(unittest.TestCase):
    work_ref = "localhost/agents-dallas:new"
    gateway_ref = "localhost/agents-dallas-gateway:new"
    old_work = "localhost/agents-dallas:old"
    old_gateway = "localhost/agents-dallas-gateway:old"
    used_image = "localhost/agents-dallas:used"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.store = self.base / "store"
        self.store.mkdir()
        self.state_path = self.base / "podman.json"
        self.state = {
            "images": [self.work_ref, self.gateway_ref, self.old_work,
                       self.old_gateway, self.used_image,
                       "localhost/agents-dallas-other:old", "alpine:old"],
            "containers": {
                "unrelated-stopped": {"image": self.used_image, "running": False},
            },
            "calls": [],
        }
        podman = self.bin / "podman"
        podman.write_text('''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path
path = Path(os.environ["TEST_PODMAN_STATE"])
s = json.loads(path.read_text())
a = sys.argv[1:]
s["calls"].append(a)
status = 0
if a[0] == "unshare":
    if a[1:3] == ["nix", "copy"]:
        root = next(arg.split("local?root=", 1)[1] for arg in a if arg.startswith("local?root="))
        (Path(root) / "nix/store").mkdir(parents=True, exist_ok=True)
    elif a[1] in ("mkdir", "ln", "chmod"):
        import subprocess
        status = subprocess.run(a[1:]).returncode
    elif a[1] == "stat":
        print(s.get("store_owner", "1:30000"))
    elif a[1] == "chown":
        if a[-1].endswith("/nix/store"):
            s["store_owner"] = a[-2]
    else:
        raise AssertionError(a)
elif a[0] == "inspect":
    c = s["containers"].get(a[-1])
    if c is None:
        status = 1
    elif a[2] == "{{.State.Running}}":
        status = int(os.environ.get("TEST_FAIL_WORK_STATE", "0")) if a[-1] == "agents-dallas" else 0
        if not status:
            print(str(c["running"]).lower())
    elif a[2] == "{{.ImageName}}":
        print(c["image"])
    elif a[2] == "{{.HostConfig.Init}}":
        status = int(os.environ.get("TEST_FAIL_INIT_INSPECT", "0"))
        if not status:
            print(str(c.get("init", False)).lower())
    else:
        print(os.environ["AGENTS_SPEC"])
elif a[:2] == ["container", "exists"]:
    status = int(os.environ.get("TEST_FAIL_WORK_EXISTS", "0")) if a[2] == "agents-dallas" else 0
    if not status:
        status = 0 if a[2] in s["containers"] else 1
elif a[0] == "rm":
    del s["containers"][a[-1]]
elif a[:2] == ["image", "exists"]:
    status = 0 if a[2] in s["images"] else 1
elif a[0] == "info":
    print(os.environ["TEST_GRAPH_ROOT"])
elif a[0] == "load":
    ref = (os.environ["AGENTS_IMAGE_REF"] if a[-1] == os.environ["AGENTS_IMAGE"]
           else os.environ["AGENTS_GATEWAY_REF"])
    s["images"].append(ref)
elif a[0] == "create":
    name = a[a.index("--name") + 1]
    if os.environ.get("TEST_HANG_CREATE") == name:
        import signal
        import time
        Path(os.environ["TEST_CHILD_PID"]).write_text(str(os.getpid()))
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        time.sleep(60)
    if os.environ.get("TEST_FAIL_CREATE") == name:
        status = 19
    else:
        image = next(arg for arg in a if arg in (os.environ["AGENTS_IMAGE_REF"], os.environ["AGENTS_GATEWAY_REF"]))
        s["containers"][name] = {"image": image, "running": False, "init": "--init" in a}
elif a[0] == "start":
    s["containers"][a[1]]["running"] = not (a[1] == "agents-dallas" and os.environ.get("TEST_REJECT_START"))
    if a[1] == "agents-dallas" and os.environ.get("TEST_PENDING_START"):
        s["containers"][a[1]]["ready"] = False
elif a[0] == "logs":
    if a[1] == "agents-dallas" and os.environ.get("TEST_REJECT_START"):
        print("agents: insufficient process headroom: current=2032 max=2048 free=16 minimum=256")
elif a[0] == "images":
    status = int(os.environ.get("TEST_FAIL_LIST", "0"))
    if not status:
        print("\\n".join(s["images"]))
elif a[0] == "ps":
    if "--filter" in a and a[a.index("--filter") + 1].startswith("ancestor="):
        ref = a[a.index("--filter") + 1].split("=", 1)[1]
        if os.environ.get("TEST_FAIL_ANCESTOR") == ref:
            status = 26
        else:
            print("\\n".join(n for n, c in s["containers"].items() if c["image"] == ref))
elif a[0] == "rmi":
    assert len(a) == 2, "cleanup must never force removal"
    if os.environ.get("TEST_FAIL_RMI") == a[1]:
        status = 23
    else:
        s["images"].remove(a[1])
elif a[0] == "exec":
    if a[-3:] == ["daemon", "store", "ping"]:
        status = int(os.environ.get("TEST_FAIL_DAEMON_PING", "0"))
    elif a[-1] == "/tmp/agents-workspace-ready":
        if os.environ.get("TEST_HANG_READINESS"):
            import signal
            import time
            Path(os.environ["TEST_READINESS_PIDS"]).write_text(json.dumps([os.getpid(), os.getppid()]))
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            time.sleep(60)
        status = 0 if s["containers"]["agents-dallas"].get("ready", True) else 1
else:
    raise AssertionError(a)
path.write_text(json.dumps(s))
sys.exit(status)
''')
        podman.chmod(0o755)
        nix = self.bin / "nix"
        nix.write_text("#!/bin/bash\nexit 0\n")
        nix.chmod(0o755)
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                        AGENTS_PODMAN=str(podman),
                        AGENTS_STATE=str(self.base / "agents-state"),
                        XDG_RUNTIME_DIR=str(self.base / "runtime"),
                        AGENTS_PROJECTS=str(self.base / "projects"),
                        AGENTS_HOME="/container-home/test", AGENTS_USER="test", AGENTS_ROOT="/nix/store/test",
                        AGENTS_INIT="/nix/store/test-init",
                        AGENTS_IMAGE="work.tar", AGENTS_GATEWAY_IMAGE="gateway.tar",
                        AGENTS_IMAGE_REF=self.work_ref, AGENTS_GATEWAY_REF=self.gateway_ref,
                        AGENTS_CLIPBOARD="0", AGENTS_SOUND="0", AGENTS_DEVICES="", AGENTS_SPEC="test-spec",
                        AGENTS_NIX_ACCESS_TOKENS="",
                        AGENTS_SECRET_HELPER=str(Path(__file__).with_name("secret-environment.py").resolve()),
                        AGENTS_SECRET_SOURCES="{}",
                        TEST_PODMAN_STATE=str(self.state_path), TEST_GRAPH_ROOT=str(self.store))
        # Exercise the actual dispatcher and lifecycle; stub only host device/
        # UID checks, which cannot run on an arbitrary unit-test host.
        source = launcher_source()
        prefix, dispatch = source.split('case "$command" in\n', 1)
        self.launcher = self.base / "launcher.sh"
        # Shrink only the readiness polling window in fixtures; production
        # retains 30 seconds. Real client timeout/cleanup behaviour is unchanged.
        prefix = prefix.replace("SECONDS + 30", "SECONDS + 1")
        self.launcher.write_text(prefix + '\ndoctor() { :; }\ncase "$command" in\n' + dispatch)

    def run_launcher(self, command):
        self.state_path.write_text(json.dumps(self.state))
        result = bounded_launcher(
            ["bash", "-euo", "pipefail", str(self.launcher), command],
            env=self.env, timeout=float(self.env.get("TEST_FIXTURE_TIMEOUT", "15")),
        )
        self.state = json.loads(self.state_path.read_text())
        return result

    def current_containers(self):
        self.state["containers"].update({
            "agents-dallas": {"image": self.work_ref, "running": True, "init": True},
            "agents-dallas-gateway": {"image": self.gateway_ref, "running": True},
            "agents-dallas-nix": {"image": self.work_ref, "running": True, "init": True},
        })

    def creation(self, name="agents-dallas"):
        return next(c for c in self.state["calls"] if c[0] == "create" and c[c.index("--name") + 1] == name)

    def assert_cleaned(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c for c in self.state["calls"] if c[0] == "rmi"], [
            ["rmi", self.old_work], ["rmi", self.old_gateway],
        ])
        self.assertIn(self.work_ref, self.state["images"])
        self.assertIn(self.gateway_ref, self.state["images"])
        self.assertIn(self.used_image, self.state["images"])
        self.assertIn("alpine:old", self.state["images"])
        self.assertIn("localhost/agents-dallas-other:old", self.state["images"])

    def test_shell_cleans_even_when_current_containers_are_running(self):
        self.current_containers()
        result = self.run_launcher("shell")
        self.assert_cleaned(result)
        self.assertEqual(result.stdout, "")
        self.assertFalse(any(c[0] in ("load", "create", "rm") for c in self.state["calls"]))
        self.assertEqual(self.state["calls"][-1][-1], "herdr")

    def test_rebuild_imports_missing_images_then_cleans_after_start(self):
        self.state["images"].remove(self.work_ref)
        self.state["images"].remove(self.gateway_ref)
        self.state["containers"].update({
            "agents-dallas": {"image": self.old_work, "running": True},
            "agents-dallas-gateway": {"image": self.old_gateway, "running": True},
        })
        self.assert_cleaned(self.run_launcher("rebuild"))
        calls = self.state["calls"]
        self.assertEqual(sum(c[0] == "load" for c in calls), 2)
        self.assertLess(calls.index(["start", "agents-dallas"]),
                        calls.index(["rmi", self.old_work]))

    def test_shell_starts_new_workspace_then_cleans(self):
        self.assert_cleaned(self.run_launcher("shell"))
        self.assertFalse(any(c[0] == "load" for c in self.state["calls"]))

    def test_pending_startup_cannot_bypass_acceptance_on_reuse(self):
        self.env["TEST_PENDING_START"] = "1"
        first = self.run_launcher("up")
        self.assertEqual(first.returncode, 1, first.stderr)
        self.assertTrue(self.state["containers"]["agents-dallas"]["running"])
        self.state["calls"] = []
        second = self.run_launcher("shell")
        self.assertEqual(second.returncode, 1, second.stderr)
        self.assertFalse(any(c[0] in ("create", "rm", "start", "rmi")
                             for c in self.state["calls"]))
        self.assertFalse(any(c[0] == "exec" and c[-1] == "herdr"
                             for c in self.state["calls"]))
        self.state["containers"]["agents-dallas"]["ready"] = True
        self.state["calls"] = []
        third = self.run_launcher("shell")
        self.assertEqual(third.returncode, 0, third.stderr)
        self.assertFalse(any(c[0] in ("create", "rm", "start") for c in self.state["calls"]))
        self.assertEqual(self.state["calls"][-1][-1], "herdr")

    def test_readiness_timeout_collects_client_and_monitor(self):
        pids_file = self.base / "readiness-pids.json"
        self.env.update(TEST_HANG_READINESS="1", TEST_READINESS_PIDS=str(pids_file))
        result = self.run_launcher("up")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertTrue(pids_file.exists())
        for pid in json.loads(pids_file.read_text()):
            self.assertFalse(Path(f"/proc/{pid}").exists(), f"owned process {pid} remains")

    def test_startup_rejection_is_not_ready_or_dispatched(self):
        self.env["TEST_REJECT_START"] = "1"
        result = self.run_launcher("shell")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Workspace ready", result.stderr)
        self.assertIn("insufficient process headroom", result.stderr)
        self.assertFalse(any(c[0] == "rmi" for c in self.state["calls"]))
        self.assertFalse(any(c[0] == "exec" and c[-1] == "herdr"
                             for c in self.state["calls"]))
        self.assertEqual(sum(c == ["start", "agents-dallas"] for c in self.state["calls"]), 1)

    def test_deadline_collects_stalled_owned_child(self):
        child_file = self.base / "child.pid"
        self.env.update(TEST_HANG_CREATE="agents-dallas", TEST_CHILD_PID=str(child_file),
                        TEST_FIXTURE_TIMEOUT="5")
        started = time.monotonic()
        result = self.run_launcher("up")
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertIn("fixture timed out", result.stderr)
        self.assertLess(time.monotonic() - started, 9)
        self.assertTrue(child_file.exists())
        self.assertFalse(Path(f"/proc/{child_file.read_text()}").exists())

    def test_store_is_read_only_only_in_workspace(self):
        result = self.run_launcher("up")
        self.assertEqual(result.returncode, 0, result.stderr)
        mount = f"type=bind,src={self.env['AGENTS_STATE']}/nix-root/nix,dst=/nix"
        workspace, daemon = self.creation(), self.creation("agents-dallas-nix")
        self.assertIn(mount + ",ro=true", workspace)
        self.assertNotIn(mount, workspace)
        self.assertIn(mount, daemon)
        self.assertEqual(workspace[workspace.index("--user") + 1], "1000:1000")
        self.assertEqual(daemon[daemon.index("--user") + 1], "0:0")
        for directory in ("profiles", "gcroots"):
            self.assertIn(mount.split(",dst=", 1)[0] + f"/var/nix/{directory}/per-user/test,dst=/nix/var/nix/{directory}/per-user/test", workspace)
        self.assertFalse(any(arg.startswith("--device") for arg in daemon))
        self.assertNotIn("--pid", daemon)
        self.assertNotIn("--privileged", workspace + daemon)
        self.assertIn("--entrypoint", daemon)
        self.assertEqual(daemon[daemon.index("--entrypoint") + 1], "/bin/nix-daemon")
        self.assertNotIn("--cap-add=SYS_ADMIN", workspace + daemon)
        self.assertFalse(any("dst=/container-home" in arg for arg in daemon))
        calls = self.state["calls"]
        self.assertLess(calls.index(["start", "agents-dallas-nix"]), calls.index(self.creation()))

    def test_daemon_admission_failure_never_starts_workspace(self):
        self.env["TEST_FAIL_DAEMON_PING"] = "1"
        result = self.run_launcher("shell")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("Private Nix daemon startup failed", result.stderr)
        self.assertFalse(any(c[0] == "create" and c[c.index("--name") + 1] == "agents-dallas"
                             for c in self.state["calls"]))
        self.assertFalse(any(c[0] == "rmi" for c in self.state["calls"]))

    def test_failed_daemon_ping_on_reuse_does_not_restart_or_dispatch(self):
        self.current_containers()
        self.env["TEST_FAIL_DAEMON_PING"] = "1"
        result = self.run_launcher("shell")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(any(c[0] in ("create", "rm", "start", "rmi") for c in self.state["calls"]))
        self.assertNotEqual(self.state["calls"][-1][-1], "herdr")

    def test_legacy_store_refuses_even_explicit_rebuild_before_container_removal(self):
        self.current_containers()
        store = Path(self.env["AGENTS_STATE"]) / "nix-root/nix/store"
        store.mkdir(parents=True)
        result = self.run_launcher("rebuild")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("offline ownership conversion", result.stderr)
        self.assertFalse(any(c[0] in ("create", "rm", "start", "unshare") for c in self.state["calls"]))

    def test_marker_cannot_bypass_wrong_store_owner(self):
        state = Path(self.env["AGENTS_STATE"])
        (state / "nix-root/nix/store").mkdir(parents=True)
        (state / "nix-daemon-layout").write_text("keep-id-root-v1\n")
        self.state["store_owner"] = "0:0"
        result = self.run_launcher("up")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(any(c[0] in ("create", "rm", "start") for c in self.state["calls"]))

    def test_rebuild_preserves_store_layout_and_removes_daemon_before_gateway(self):
        self.assertEqual(self.run_launcher("up").returncode, 0)
        self.state["calls"] = []
        result = self.run_launcher("rebuild")
        self.assertEqual(result.returncode, 0, result.stderr)
        marker = Path(self.env["AGENTS_STATE"]) / "nix-daemon-layout"
        self.assertEqual(marker.read_text(), "keep-id-root-v1\n")
        calls = self.state["calls"]
        removals = [c[-1] for c in calls if c[0] == "rm"]
        self.assertEqual(removals, ["agents-dallas", "agents-dallas-nix", "agents-dallas-gateway"])
        self.assertEqual(self.state["store_owner"], "1:30000")

    def test_workspace_uses_reaping_init(self):
        result = self.run_launcher("up")
        self.assertEqual(result.returncode, 0, result.stderr)
        workspace = self.creation()
        self.assertIn("--init", workspace)
        self.assertEqual(workspace[workspace.index("--init-path") + 1], self.env["AGENTS_INIT"])

    def test_workspace_process_limit(self):
        result = self.run_launcher("up")
        self.assertEqual(result.returncode, 0, result.stderr)
        workspace = self.creation()
        self.assertIn("--pids-limit=4096", workspace)

    def test_running_workspace_without_init_refuses_without_restart(self):
        self.current_containers()
        self.state["containers"]["agents-dallas"]["init"] = False
        self.state["containers"]["agents-dallas"]["image"] = self.old_work
        result = self.run_launcher("shell")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("agents rebuild", result.stderr)
        self.assertFalse(any(c[0] in ("rm", "create", "start", "exec", "rmi")
                             for c in self.state["calls"]))
        self.assertFalse(self.staged_environment().exists())
        self.assertTrue(self.state["containers"]["agents-dallas"]["running"])

    def test_init_inspection_failure_refuses_without_restart(self):
        self.current_containers()
        self.env["TEST_FAIL_INIT_INSPECT"] = "27"
        result = self.run_launcher("shell")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("init", result.stderr)
        self.assertFalse(any(c[0] in ("rm", "create", "start", "exec", "rmi")
                             for c in self.state["calls"]))
        self.assertFalse(self.staged_environment().exists())

    def test_workspace_state_inspection_failure_refuses_without_restart(self):
        self.current_containers()
        self.env["TEST_FAIL_WORK_STATE"] = "28"
        result = self.run_launcher("shell")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[0] in ("rm", "create", "start", "exec", "rmi")
                             for c in self.state["calls"]))
        self.assertFalse(self.staged_environment().exists())

    def test_workspace_existence_failure_refuses_before_bootstrap(self):
        self.current_containers()
        self.env["TEST_FAIL_WORK_EXISTS"] = "29"
        result = self.run_launcher("shell")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[0] in ("rm", "create", "start", "exec", "rmi")
                             for c in self.state["calls"]))
        self.assertFalse(self.staged_environment().exists())

    def test_explicit_rebuild_replaces_workspace_without_init(self):
        self.current_containers()
        self.state["containers"]["agents-dallas"]["init"] = False
        result = self.run_launcher("rebuild")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.state["containers"]["agents-dallas"]["init"])

    def test_workspace_has_private_graphics_shared_memory(self):
        result = self.run_launcher("up")
        self.assertEqual(result.returncode, 0, result.stderr)
        workspace = self.creation()
        gateway = self.creation("agents-dallas-gateway")
        self.assertIn("--shm-size=1g", workspace)
        self.assertNotIn("--shm-size=1g", gateway)
        self.assertFalse(any(arg.startswith("--ipc") for arg in workspace + gateway))

    def test_linux_mounts_kvm_and_configured_gpu(self):
        self.env["AGENTS_KVM"] = "1"
        self.env["AGENTS_DEVICES"] = "/dev/dri/renderD128:/dev/dri/renderD129"
        result = self.run_launcher("up")
        self.assertEqual(result.returncode, 0, result.stderr)
        workspace = self.creation()
        for device in ("/dev/kvm", "/dev/net/tun", "/dev/fuse",
                       "/dev/dri/renderD128", "/dev/dri/renderD129"):
            self.assertIn("--device=" + device, workspace)

    def test_wsl_mounts_only_tun_and_fuse(self):
        self.env["AGENTS_KVM"] = "0"
        result = self.run_launcher("up")
        self.assertEqual(result.returncode, 0, result.stderr)
        workspace = self.creation()
        self.assertEqual({arg for arg in workspace if arg.startswith("--device=")},
                         {"--device=/dev/net/tun", "--device=/dev/fuse"})
        gateway = next(c for c in self.state["calls"] if c[0] == "create" and c[-1] == self.gateway_ref)
        self.assertIn("--cap-add=NET_ADMIN", gateway)
        self.assertIn("--network=container:agents-dallas-gateway", workspace)
        self.assertNotIn("--privileged", workspace + gateway)

    def test_check_respects_kvm_setting(self):
        self.current_containers()
        curl = self.bin / "curl"
        curl.write_text("#!/bin/bash\nexit 0\n")
        curl.chmod(0o755)
        self.env["AGENTS_PROXY"] = "http://127.0.0.1:2081"
        for enabled in ("0", "1"):
            with self.subTest(kvm=enabled):
                self.env["AGENTS_KVM"] = enabled
                result = self.run_launcher("check")
                self.assertEqual(result.returncode, 0, result.stderr)
                call = next(c for c in reversed(self.state["calls"]) if c[0] == "exec")
                self.assertEqual(call[-2:], ["agents-check", enabled])
                self.assertIn('if [[ $1 == 1 ]]; then test -r /dev/kvm', call[-3])

    def test_failed_start_keeps_old_images(self):
        self.env["TEST_FAIL_CREATE"] = "agents-dallas"
        result = self.run_launcher("rebuild")
        self.assertEqual(result.returncode, 19, result.stderr)
        self.assertFalse(any(c[0] in ("images", "rmi") for c in self.state["calls"]))
        self.assertIn(self.old_work, self.state["images"])

    def test_removal_failure_does_not_block_shell(self):
        self.current_containers()
        self.env["TEST_FAIL_RMI"] = self.old_work
        result = self.run_launcher("shell")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Warning: could not remove", result.stderr)
        self.assertIn(self.old_work, self.state["images"])
        self.assertNotIn(self.old_gateway, self.state["images"])
        self.assertEqual(self.state["calls"][-1][0], "exec")

    def test_listing_failure_does_not_block_shell(self):
        self.current_containers()
        self.env["TEST_FAIL_LIST"] = "25"
        result = self.run_launcher("shell")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("skipping cleanup", result.stderr)
        self.assertFalse(any(c[0] == "rmi" for c in self.state["calls"]))

    def test_container_query_failure_keeps_image(self):
        self.current_containers()
        self.env["TEST_FAIL_ANCESTOR"] = self.old_work
        result = self.run_launcher("shell")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("could not check containers", result.stderr)
        self.assertIn(self.old_work, self.state["images"])
        self.assertNotIn(["rmi", self.old_work], self.state["calls"])

    def test_repeated_shell_does_not_remove_current_images(self):
        self.current_containers()
        self.assert_cleaned(self.run_launcher("shell"))
        self.state["calls"] = []
        result = self.run_launcher("shell")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(c[0] in ("rmi", "load", "create") for c in self.state["calls"]))

    def token_source(self):
        # Like SOPS: a stable symlink whose target changes on rotation.
        source = self.base / "host config" / "access-tokens.conf"
        source.parent.mkdir()
        target = self.base / "sops-template-1"
        target.write_text("access-tokens = github.com=test-secret-one\n")
        source.symlink_to(target)
        self.env["AGENTS_NIX_ACCESS_TOKENS"] = str(source)
        return source

    def staged_token(self):
        return Path(self.env["AGENTS_STATE"]) / "nix-auth/access-tokens.conf"

    def test_nix_token_is_private_and_mounted_only_in_workspace_and_daemon(self):
        source = self.token_source()
        result = self.run_launcher("up")
        self.assertEqual(result.returncode, 0, result.stderr)
        staged = self.staged_token()
        self.assertEqual(staged.read_text(), source.read_text())
        self.assertEqual(staged.stat().st_mode & 0o777, 0o600)
        self.assertEqual(staged.parent.stat().st_mode & 0o777, 0o700)
        mount = f"type=bind,src={staged.parent},dst=/run/agents-nix-auth,ro=true"
        calls = self.state["calls"]
        workspace = self.creation()
        gateway = self.creation("agents-dallas-gateway")
        self.assertIn(mount, self.creation("agents-dallas-nix"))
        self.assertIn(mount, workspace)
        self.assertNotIn(mount, gateway)
        self.assertNotIn("test-secret-one", result.stdout + result.stderr + json.dumps(calls))
        self.assertEqual(list(staged.parent.iterdir()), [staged])

    def test_nix_token_rotation_reaches_running_workspace(self):
        source = self.token_source()
        self.current_containers()
        result = self.run_launcher("up")
        self.assertEqual(result.returncode, 0, result.stderr)
        staged = self.staged_token()
        old_inode = staged.stat().st_ino
        replacement = self.base / "sops-template-2"
        replacement.write_text("access-tokens = github.com=test-secret-two\n")
        source.unlink()
        source.symlink_to(replacement)
        result = self.run_launcher("shell")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(staged.read_text(), replacement.read_text())
        self.assertNotEqual(staged.stat().st_ino, old_inode)
        self.assertFalse(any(c[0] in ("create", "rm") for c in self.state["calls"]))
        self.assertNotIn("test-secret-two", result.stdout + result.stderr)

    def test_missing_nix_token_removes_stale_copy(self):
        source = self.token_source()
        self.current_containers()
        self.assertEqual(self.run_launcher("up").returncode, 0)
        source.resolve().unlink()  # dangling SOPS symlink
        result = self.run_launcher("up")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.staged_token().exists())
        self.assertIn("private GitHub flakes may fail", result.stderr)

    def test_disabled_nix_token_removes_stale_copy(self):
        self.token_source()
        self.current_containers()
        self.assertEqual(self.run_launcher("up").returncode, 0)
        self.env["AGENTS_NIX_ACCESS_TOKENS"] = ""
        result = self.run_launcher("up")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.staged_token().exists())
        self.assertNotIn("access-tokens.conf is missing", result.stderr)

    def test_invalid_nix_token_fails_without_stale_credentials(self):
        self.token_source()
        self.current_containers()
        self.assertEqual(self.run_launcher("up").returncode, 0)
        self.env["AGENTS_NIX_ACCESS_TOKENS"] = str(self.base)
        result = self.run_launcher("up")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.staged_token().exists())
        self.assertIn("not a readable regular file", result.stderr)

    def secret_source(self):
        source = self.base / "tavily-secret"
        source.write_text("private-tavily-test-key\n")
        self.env["AGENTS_SECRET_SOURCES"] = json.dumps({
            "TAVILY_API_KEY": str(source), "TINYFISH_API_KEY": None,
        })
        return source

    def staged_environment(self):
        return Path(self.env["XDG_RUNTIME_DIR"]) / "agents-container-secrets/environment.json"

    def test_secret_environment_is_private_and_only_mounted_in_workspace(self):
        source = self.secret_source()
        result = self.run_launcher("up")
        self.assertEqual(result.returncode, 0, result.stderr)
        staged = self.staged_environment()
        self.assertEqual(json.loads(staged.read_text()), {
            "TAVILY_API_KEY": source.read_text().strip(), "TINYFISH_API_KEY": None,
        })
        self.assertEqual(staged.stat().st_mode & 0o777, 0o600)
        self.assertEqual(staged.parent.stat().st_mode & 0o777, 0o700)
        mount = f"type=bind,src={staged.parent},dst=/run/agents-secret-environment,ro=true"
        self.assertIn(mount, self.creation())
        self.assertNotIn(mount, self.creation("agents-dallas-nix"))
        self.assertNotIn(mount, self.creation("agents-dallas-gateway"))
        self.assertNotIn("private-tavily-test-key", result.stdout + result.stderr + json.dumps(self.state["calls"]))
        self.assertEqual(list(staged.parent.iterdir()), [staged])
        self.assertFalse((Path(self.env["AGENTS_STATE"]) / "secret-environment").exists())

    def test_secret_rotation_and_removal_refresh_running_workspace(self):
        source = self.secret_source()
        self.current_containers()
        self.assertEqual(self.run_launcher("up").returncode, 0)
        source.write_text("rotated-key\n")
        self.assertEqual(self.run_launcher("shell").returncode, 0)
        self.assertEqual(json.loads(self.staged_environment().read_text())["TAVILY_API_KEY"], "rotated-key")
        source.unlink()
        result = self.run_launcher("up")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(json.loads(self.staged_environment().read_text())["TAVILY_API_KEY"])
        self.assertIn("variable will be unset", result.stderr)
        self.assertFalse(any(c[0] in ("create", "rm") for c in self.state["calls"]))

    def test_disabled_secret_environment_removes_values(self):
        self.secret_source()
        self.current_containers()
        self.assertEqual(self.run_launcher("up").returncode, 0)
        self.env["AGENTS_SECRET_SOURCES"] = "{}"
        self.assertEqual(self.run_launcher("up").returncode, 0)
        self.assertEqual(json.loads(self.staged_environment().read_text()), {})

    def test_second_instance_isolates_host_brokers_and_staged_secrets(self):
        source = launcher_source("agents-lt")
        prefix, dispatch = source.split('case "$command" in\n', 1)
        self.launcher.write_text(prefix + '\ndoctor() { :; }\ncase "$command" in\n' + dispatch)
        runtime = Path(self.env["XDG_RUNTIME_DIR"])
        old_secret = runtime / "agents-container-secrets/environment.json"
        old_secret.parent.mkdir(parents=True)
        old_secret.write_text('{"OLD_FIXTURE_KEY":"keep"}')
        calls = self.base / "systemctl-calls"
        systemctl = self.bin / "systemctl"
        systemctl.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$TEST_SYSTEMCTL_CALLS"\n')
        systemctl.chmod(0o755)
        self.env.update(AGENTS_CLIPBOARD="1", AGENTS_SOUND="1", AGENTS_WEB_LINKS="1",
                        TEST_SYSTEMCTL_CALLS=str(calls))
        for kind in ("clipboard", "sound", "web-links"):
            directory = runtime / ("agents-lt-" + kind)
            directory.mkdir()
            server = socket.socket(socket.AF_UNIX)
            self.addCleanup(server.close)
            server.bind(str(directory / "socket"))
        result = self.run_launcher("up")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Run: agents-lt shell", result.stdout)
        self.assertEqual(old_secret.read_text(), '{"OLD_FIXTURE_KEY":"keep"}')
        workspace = self.creation()
        for kind in ("clipboard", "sound", "web-links"):
            self.assertIn(f"--user start agents-lt-{kind}.service", calls.read_text())
            self.assertNotIn(f"--user start agents-{kind}.service", calls.read_text())
            self.assertIn(f"type=bind,src={runtime}/agents-lt-{kind},dst=/run/agents-{kind},ro=true", workspace)
        self.assertIn(f"type=bind,src={runtime}/agents-lt-container-secrets,dst=/run/agents-secret-environment,ro=true", workspace)

    def enable_web_links(self):
        self.env["AGENTS_WEB_LINKS"] = "1"
        self.env["DISPLAY"] = ":host-test"
        directory = Path(self.env["XDG_RUNTIME_DIR"]) / "agents-web-links"
        directory.mkdir(parents=True)
        sock = socket.socket(socket.AF_UNIX)
        sock.bind(str(directory / "socket"))
        self.addCleanup(sock.close)
        self.web_link_calls = self.base / "systemctl-calls"
        systemctl = self.bin / "systemctl"
        systemctl.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$TEST_SYSTEMCTL_CALLS"\n'
                             'exit "${TEST_SYSTEMCTL_STATUS:-0}"\n')
        systemctl.chmod(0o755)
        self.env["TEST_SYSTEMCTL_CALLS"] = str(self.web_link_calls)
        return directory

    def test_web_link_socket_is_workspace_only_without_desktop_sockets(self):
        directory = self.enable_web_links()
        result = self.run_launcher("up")
        self.assertEqual(result.returncode, 0, result.stderr)
        mount = f"type=bind,src={directory},dst=/run/agents-web-links,ro=true"
        self.assertIn(mount, self.creation())
        self.assertNotIn(mount, self.creation("agents-dallas-nix"))
        self.assertNotIn(mount, self.creation("agents-dallas-gateway"))
        calls = json.dumps([call for call in self.state["calls"] if call[0] == "create"])
        for forbidden in ("/tmp/.X11-unix", "wayland-0", "DBUS_SESSION_BUS_ADDRESS", ":host-test"):
            self.assertNotIn(forbidden, calls)
        self.assertIn("--user start agents-web-links.service", self.web_link_calls.read_text())
        self.assertIn("--user import-environment DISPLAY", self.web_link_calls.read_text())

    def test_web_link_service_is_started_for_reused_workspace(self):
        self.enable_web_links()
        self.current_containers()
        result = self.run_launcher("up")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--user start agents-web-links.service", self.web_link_calls.read_text())
        self.assertFalse(any(call[0] in ("create", "rm") for call in self.state["calls"]))

    def test_web_link_service_failure_stops_before_replacing_containers(self):
        self.enable_web_links()
        self.env["TEST_SYSTEMCTL_STATUS"] = "19"
        result = self.run_launcher("up")
        self.assertEqual(result.returncode, 19)
        self.assertFalse(any(call[0] in ("create", "rm") for call in self.state["calls"]))

    def test_disabled_web_links_has_no_host_socket_mount(self):
        result = self.run_launcher("up")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("agents-web-links", json.dumps(self.state["calls"]))

    def enable_sound(self):
        self.env["AGENTS_SOUND"] = "1"
        directory = Path(self.env["XDG_RUNTIME_DIR"]) / "agents-sound"
        directory.mkdir(parents=True)
        sock = socket.socket(socket.AF_UNIX)
        sock.bind(str(directory / "socket"))
        self.addCleanup(sock.close)
        self.sound_calls = self.base / "systemctl-calls"
        systemctl = self.bin / "systemctl"
        systemctl.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$TEST_SYSTEMCTL_CALLS"\n'
                             'exit "${TEST_SYSTEMCTL_STATUS:-0}"\n')
        systemctl.chmod(0o755)
        self.env["TEST_SYSTEMCTL_CALLS"] = str(self.sound_calls)
        return directory

    def test_sound_mount_is_playback_only_and_workspace_only(self):
        directory = self.enable_sound()
        result = self.run_launcher("up")
        self.assertEqual(result.returncode, 0, result.stderr)
        mount = f"type=bind,src={directory},dst=/run/agents-sound,ro=true"
        creates = [c for c in self.state["calls"] if c[0] == "create"]
        self.assertIn(mount, self.creation())
        self.assertNotIn(mount, self.creation("agents-dallas-nix"))
        self.assertNotIn(mount, self.creation("agents-dallas-gateway"))
        self.assertNotIn("/pulse/", json.dumps(creates))
        self.assertIn("--user start agents-sound.service", self.sound_calls.read_text())

    def test_sound_service_is_started_even_for_reused_workspace(self):
        self.enable_sound()
        self.current_containers()
        result = self.run_launcher("up")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--user start agents-sound.service", self.sound_calls.read_text())
        self.assertFalse(any(c[0] == "create" for c in self.state["calls"]))

    def test_sound_service_failure_stops_startup(self):
        self.enable_sound()
        self.env["TEST_SYSTEMCTL_STATUS"] = "19"
        result = self.run_launcher("up")
        self.assertEqual(result.returncode, 19)
        self.assertFalse(any(c[0] == "create" for c in self.state["calls"]))

    def test_disabled_sound_has_no_mount(self):
        result = self.run_launcher("up")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("/run/agents-sound", json.dumps(self.state["calls"]))

    def test_down_does_not_clean_images(self):
        self.current_containers()
        result = self.run_launcher("down")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(c[0] in ("images", "rmi") for c in self.state["calls"]))


if __name__ == "__main__":
    unittest.main()
