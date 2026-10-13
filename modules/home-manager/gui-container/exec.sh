set -euo pipefail
unset DISPLAY
: "${XDG_RUNTIME_DIR:?}" "${WAYLAND_DISPLAY:?}"
[[ $WAYLAND_DISPLAY == /* && -S $WAYLAND_DISPLAY ]] \
  && test -f "$XDG_RUNTIME_DIR/session-ready" \
  && test -s "$XDG_RUNTIME_DIR/dbus-address" || {
  echo 'The container Wayland session is not ready.' >&2; exit 1;
}
DBUS_SESSION_BUS_ADDRESS=$(cat "$XDG_RUNTIME_DIR/dbus-address")
export DBUS_SESSION_BUS_ADDRESS
exec "$@"
