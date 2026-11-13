"""Integration tests against private D-Bus daemons and the real filtering proxy."""

import asyncio
import importlib.util
import os
from pathlib import Path
import select
import shutil
import signal as signals
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from dbus_next import DBusError, Message, MessageType, Variant
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method, signal


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bridge = load("notification_service", "notification-service.py")
launcher = load("notification_launcher", "notification-launch.py")


class FakeNotifications(ServiceInterface):
    def __init__(self):
        super().__init__(bridge.NAME)
        self.next_id = 100
        self.calls = []

    @method()
    def GetCapabilities(self) -> 'as':
        return ["body", "actions", "body-markup", "body-images"]

    @method()
    def Notify(self, app_name: 's', replaces_id: 'u', app_icon: 's', summary: 's',
               body: 's', actions: 'as', hints: 'a{sv}', timeout: 'i') -> 'u':
        self.calls.append((app_name, replaces_id, app_icon, summary, body, actions, hints, timeout))
        if replaces_id:
            return replaces_id
        self.next_id += 1
        return self.next_id

    @method()
    def CloseNotification(self, notification_id: 'u'):
        self.NotificationClosed(notification_id, 3)

    @signal()
    def NotificationClosed(self, notification_id, reason) -> 'uu':
        return [notification_id, reason]

    @signal()
    def ActionInvoked(self, notification_id, action_key) -> 'us':
        return [notification_id, action_key]


class UnrelatedInterface(ServiceInterface):
    def __init__(self):
        super().__init__("org.gnome.Shell")

    @method()
    def Forbidden(self) -> 's':
        return "must never be exposed through the proxy"


class HintTests(unittest.TestCase):
    def test_auth_uses_host_uid_instead_of_container_uid(self):
        auth = bridge.HostAuth(1000)
        self.assertEqual(auth._authentication_start(), "AUTH EXTERNAL 31303030")
        self.assertEqual(auth._receive_line("OK test-guid"), "BEGIN")

    def test_host_paths_and_desktop_identity_are_not_taken_from_container(self):
        hints = bridge.host_hints({
            "image-path": Variant("s", "/host/private/image.png"),
            "sound-file": Variant("s", "/host/private/sound.wav"),
            "desktop-entry": Variant("s", "another-app"),
            "urgency": Variant("y", 2),
        })
        self.assertEqual(set(hints), {"desktop-entry", "urgency"})
        self.assertEqual(hints["desktop-entry"].value, "max-container")


@unittest.skipUnless(shutil.which("xdg-dbus-proxy"), "requires xdg-dbus-proxy")
class LauncherTests(unittest.TestCase):
    def test_proxy_mount_and_cleanup_on_exit_and_signal(self):
        checks = '''import os, stat, sys
mount = sys.argv[sys.argv.index("--mount") + 1]
path = next(part[4:] for part in mount.split(",") if part.startswith("src="))
assert stat.S_ISSOCK(os.stat(path).st_mode)
assert "dst=/run/host-notifications" in mount
assert "MAX_NOTIFICATIONS=1" in sys.argv
assert "MAX_HOST_UID=" + str(os.getuid()) in sys.argv
assert sys.argv[-1] == "test-image"
'''
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted), tempfile.TemporaryDirectory(prefix="max-launch-") as runtime:
                previous = {sig: signals.getsignal(sig) for sig in (signals.SIGTERM, signals.SIGINT, signals.SIGHUP)}
                try:
                    code = checks
                    if interrupted:
                        code += "import signal, time; os.kill(os.getppid(), signal.SIGTERM); time.sleep(30)\n"
                    with patch.dict(os.environ, XDG_RUNTIME_DIR=runtime,
                                    DBUS_SESSION_BUS_ADDRESS="unix:path=" + runtime + "/unused-bus"):
                        result = launcher.main("xdg-dbus-proxy", [sys.executable, "-c", code, "test-image"])
                    self.assertEqual(result, -signals.SIGTERM if interrupted else 0)
                    self.assertEqual(list(Path(runtime).iterdir()), [])
                finally:
                    for sig, handler in previous.items():
                        signals.signal(sig, handler)


@unittest.skipUnless(shutil.which("dbus-daemon") and shutil.which("xdg-dbus-proxy"),
                     "requires dbus-daemon and xdg-dbus-proxy")
class NotificationBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="max-notify-test-")
        self.processes = []
        self.buses = []
        self.task = None
        self.ready_socket = None
        self.addAsyncCleanup(self.cleanup)
        addresses = []
        for index in range(2):
            path = Path(self.directory.name) / f"bus{index}"
            config = Path(self.directory.name) / f"bus{index}.conf"
            config.write_text(f'''<busconfig><type>session</type>
<listen>unix:path={path}</listen><auth>EXTERNAL</auth>
<policy context="default"><allow user="*"/><allow own="*"/>
<allow send_destination="*"/><allow receive_sender="*"/></policy></busconfig>''')
            process = subprocess.Popen(
                ["dbus-daemon", "--nofork", "--print-address=1", f"--config-file={config}"],
                stdout=subprocess.PIPE,
            )
            self.processes.append(process)
            self.assertTrue(select.select([process.stdout], [], [], 5)[0])
            address = process.stdout.readline().decode().strip()
            self.assertTrue(address)
            addresses.append(address)
        self.host_bus = await MessageBus(bus_address=addresses[0]).connect()
        self.buses.append(self.host_bus)
        self.host = FakeNotifications()
        self.host_bus.export(bridge.PATH, self.host)
        # Same connection, same object path, different interface: a broad TALK
        # permission would expose this too on a real GNOME Shell process.
        self.host_bus.export(bridge.PATH, UnrelatedInterface())
        await self.host_bus.request_name(bridge.NAME)
        await self.host_bus.request_name("org.gnome.Shell")
        parent, child = socket.socketpair()
        self.ready_socket = parent
        proxy_path = str(Path(self.directory.name) / "proxy")
        process = subprocess.Popen(
            launcher.proxy_command("xdg-dbus-proxy", addresses[0], proxy_path, child.fileno()),
            pass_fds=(child.fileno(),),
        )
        self.processes.append(process)
        child.close()
        self.assertTrue(select.select([parent], [], [], 5)[0])
        self.assertTrue(parent.recv(1))
        self.proxy_address = "unix:path=" + proxy_path
        self.task = asyncio.create_task(bridge.serve(self.proxy_address, addresses[1]))
        client = await MessageBus(bus_address=addresses[1]).connect()
        self.buses.append(client)
        for _ in range(100):
            try:
                info = await client.introspect(bridge.NAME, bridge.PATH)
                self.client = client.get_proxy_object(bridge.NAME, bridge.PATH, info).get_interface(bridge.NAME)
                break
            except DBusError:
                if self.task.done():
                    await self.task
                await asyncio.sleep(0.02)
        else:
            self.fail("notification bridge did not acquire its private bus name")

    async def cleanup(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        for bus in self.buses:
            bus.disconnect()
        if self.ready_socket:
            self.ready_socket.close()
        for process in reversed(self.processes):
            launcher.terminate(process)
            if process.stdout:
                process.stdout.close()
        self.directory.cleanup()

    async def test_notify_replace_actions_close_and_filter(self):
        capabilities = await self.client.call_get_capabilities()
        self.assertIn("actions", capabilities)
        self.assertNotIn("body-images", capabilities)
        notification_id = await self.client.call_notify(
            "max", 0, "/container/icon.png", "Новое сообщение", "Привет", ["default", "Открыть"],
            {"urgency": Variant("y", 1)}, -1,
        )
        self.assertEqual(notification_id, 101)
        self.assertEqual(self.host.calls[-1][0], "MAX (container)")
        self.assertEqual(self.host.calls[-1][3:5], ("Новое сообщение", "Привет"))
        replaced = await self.client.call_notify("max", notification_id, "", "Обновлено", "", [], {}, -1)
        self.assertEqual(replaced, notification_id)
        invoked = asyncio.get_running_loop().create_future()
        closed = asyncio.get_running_loop().create_future()
        self.client.on_action_invoked(lambda n, a: invoked.set_result((n, a)))
        self.client.on_notification_closed(lambda n, r: closed.set_result((n, r)))
        self.host.ActionInvoked(notification_id, "default")
        self.assertEqual(await asyncio.wait_for(invoked, 2), (notification_id, "default"))
        await self.client.call_close_notification(notification_id)
        self.assertEqual(await asyncio.wait_for(closed, 2), (notification_id, 3))
        # dbus-next 0.2.3 sends the expected D-Bus error and also logs the
        # exception from asynchronous service methods via asyncio.
        with self.assertLogs("asyncio", level="ERROR"), self.assertRaises(DBusError):
            await self.client.call_close_notification(999)
        filtered = await MessageBus(bus_address=self.proxy_address).connect()
        self.buses.append(filtered)
        denied = await filtered.call(Message(
            destination=bridge.NAME, path=bridge.PATH,
            interface="org.gnome.Shell", member="Forbidden",
        ))
        self.assertEqual(denied.message_type, MessageType.ERROR)
        self.assertEqual(denied.error_name, "org.freedesktop.DBus.Error.AccessDenied")


if __name__ == "__main__":
    unittest.main()
