"""Private-bus Notifications service forwarding through a filtered host socket."""

import asyncio
import os
import sys

from dbus_next import DBusError, Variant
from dbus_next.aio import MessageBus
from dbus_next.auth import AuthExternal
from dbus_next.constants import NameFlag, RequestNameReply
from dbus_next.service import ServiceInterface, method, signal


NAME = "org.freedesktop.Notifications"
PATH = "/org/freedesktop/Notifications"


class HostAuth(AuthExternal):
    """Authenticate as the proxy's host UID, not the remapped container UID."""

    def __init__(self, uid):
        super().__init__()
        self.uid = int(uid)
        if self.uid < 0:
            raise ValueError("invalid host UID")

    def _authentication_start(self, negotiate_unix_fd=False):
        self.negotiate_unix_fd = negotiate_unix_fd
        return "AUTH EXTERNAL " + str(self.uid).encode("ascii").hex()


def host_hints(hints):
    # File paths in the container do not name the same files on the host.
    # Never ask the host to load arbitrary image/sound paths from the message.
    allowed = {
        "urgency": "y", "category": "s", "transient": "b", "resident": "b",
        "suppress-sound": "b", "image-data": "(iiibiiay)",
    }
    result = {key: value for key, value in hints.items()
              if key in allowed and value.signature == allowed[key]}
    result["desktop-entry"] = Variant("s", "max-container")
    return result


class Notifications(ServiceInterface):
    def __init__(self, host):
        super().__init__(NAME)
        self.host = host
        self.owned = set()
        host.on_notification_closed(self.closed)
        host.on_action_invoked(self.invoked)

    @method()
    async def GetCapabilities(self) -> 'as':
        supported = await self.host.call_get_capabilities()
        # We do not resolve container-local paths or desktop activation tokens.
        return [cap for cap in supported if cap in ("actions", "body", "body-markup", "persistence")]

    @method()
    def GetServerInformation(self) -> 'ssss':
        return ["MAX notification bridge", "Home Manager", "1", "1.2"]

    @method()
    async def Notify(self, app_name: 's', replaces_id: 'u', app_icon: 's',
                     summary: 's', body: 's', actions: 'as', hints: 'a{sv}',
                     expire_timeout: 'i') -> 'u':
        notification_id = await self.host.call_notify(
            "MAX (container)", replaces_id if replaces_id in self.owned else 0,
            "internet-chat", summary, body, actions, host_hints(hints), expire_timeout,
        )
        self.owned.add(notification_id)
        return notification_id

    @method()
    async def CloseNotification(self, notification_id: 'u'):
        if notification_id not in self.owned:
            raise DBusError("org.freedesktop.DBus.Error.InvalidArgs", "Unknown notification")
        await self.host.call_close_notification(notification_id)

    @signal()
    def NotificationClosed(self, notification_id, reason) -> 'uu':
        return [notification_id, reason]

    @signal()
    def ActionInvoked(self, notification_id, action_key) -> 'us':
        return [notification_id, action_key]

    def closed(self, notification_id, reason):
        if notification_id in self.owned:
            self.owned.remove(notification_id)
            self.NotificationClosed(notification_id, reason)

    def invoked(self, notification_id, action_key):
        if notification_id in self.owned:
            self.ActionInvoked(notification_id, action_key)


async def serve(host_address="unix:path=/run/host-notifications", private_address=None):
    auth = HostAuth(os.environ.get("MAX_HOST_UID", os.getuid()))
    host_bus = await MessageBus(bus_address=host_address, auth=auth).connect()
    private_bus = None
    try:
        introspection = await host_bus.introspect(NAME, PATH)
        host = host_bus.get_proxy_object(NAME, PATH, introspection).get_interface(NAME)
        service = Notifications(host)
        private_bus = await MessageBus(bus_address=private_address).connect()
        private_bus.export(PATH, service)
        result = await private_bus.request_name(NAME, NameFlag.DO_NOT_QUEUE)
        if result != RequestNameReply.PRIMARY_OWNER:
            raise RuntimeError("private notification service is already owned")
        print("MAX notifications: forwarding to host active", file=sys.stderr, flush=True)
        await private_bus.wait_for_disconnect()
    finally:
        if private_bus is not None:
            private_bus.disconnect()
        host_bus.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(serve())
    except (OSError, RuntimeError, DBusError) as error:
        print(f"MAX notifications: {error}", file=sys.stderr)
        sys.exit(1)
