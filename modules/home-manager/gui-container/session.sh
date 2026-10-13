# Runs under the container's private dbus-run-session, not the host session bus.
set -euo pipefail
umask 077
unset DISPLAY
: "${XDG_RUNTIME_DIR:?}" "${WAYLAND_DISPLAY:?}" "${DBUS_SESSION_BUS_ADDRESS:?}"
[[ $WAYLAND_DISPLAY == /* && -S $WAYLAND_DISPLAY ]] || {
  echo 'Host Wayland socket is unavailable.' >&2; exit 1;
}
rm -f "$XDG_RUNTIME_DIR/session-ready" "$XDG_RUNTIME_DIR/dbus-address"
# Connect once before publishing readiness; an existing socket need not be live.
wayland-info >/dev/null
temporary=$(mktemp "$XDG_RUNTIME_DIR/.dbus-address.XXXXXXXX")
trap 'rm -f -- "$temporary"' EXIT
printf '%s\n' "$DBUS_SESSION_BUS_ADDRESS" > "$temporary"
mv -f -- "$temporary" "$XDG_RUNTIME_DIR/dbus-address"
trap - EXIT
touch "$XDG_RUNTIME_DIR/session-ready"
# No desktop or application is autostarted. Closing the last window does not
# stop the container or its private bus; gcnt down controls their lifetime.
exec sleep infinity
