#!/bin/sh
set -eu
# Weston sets DISPLAY to its own Xwayland server before autolaunching us.
# MAX currently requires X11; never fall back to the host's Wayland socket.
unset WAYLAND_DISPLAY
export QT_QPA_PLATFORM=xcb
# The clipboard helper uses the display selected by Weston, not a guessed :0.
printf '%s\n' "$DISPLAY" > "$XDG_RUNTIME_DIR/clipboard-display"

# MAX requires a Secret Service on this private D-Bus session. Keep both
# the keyring and its automatic-unlock password in the persistent profile.
# This isolates host secrets; it does not encrypt the profile against its owner.
umask 077
keyring_dir="$HOME/.local/share/keyrings"
mkdir -p "$keyring_dir"
chmod 700 "$keyring_dir"
keyring_password="$keyring_dir/.max-unlock-password"
if [ ! -s "$keyring_password" ]; then
    head -c 32 /dev/urandom | base64 > "$keyring_password.tmp"
    chmod 600 "$keyring_password.tmp"
    mv "$keyring_password.tmp" "$keyring_password"
fi
gnome-keyring-daemon --daemonize --components=secrets --unlock \
    < "$keyring_password" > /dev/null

exec /usr/share/max/bin/max
