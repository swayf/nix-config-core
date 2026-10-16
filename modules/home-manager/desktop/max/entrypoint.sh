#!/bin/sh
set -eu
umask 077
mkdir -p "$XDG_RUNTIME_DIR" "$HOME/.config"
chmod 700 "$XDG_RUNTIME_DIR"

# /tmp is a fresh tmpfs on every launch. Weston binds the Xwayland socket
# before starting Xwayland, so its parent directory must already exist.
mkdir -p /tmp/.X11-unix
chmod 1777 /tmp/.X11-unix

# Both file dialogs and default downloads lead to the explicitly shared folder.
cat > "$HOME/.config/user-dirs.dirs" <<'EOF'
XDG_DOWNLOAD_DIR="/shared"
XDG_DOCUMENTS_DIR="/shared"
EOF
if [ ! -e "$HOME/Downloads" ] && [ ! -L "$HOME/Downloads" ]; then
    ln -s /shared "$HOME/Downloads"
fi

# Make the bridge activatable on the private bus before MAX queries it.
# The activation file lives on the runtime tmpfs, so disabling notifications
# cannot leave an old service registration in the persistent profile.
if [ "${MAX_NOTIFICATIONS:-0}" = 1 ]; then
    notification_data="$XDG_RUNTIME_DIR/notification-data"
    mkdir -p "$notification_data/dbus-1/services"
    cat > "$notification_data/dbus-1/services/org.freedesktop.Notifications.service" <<'EOF'
[D-BUS Service]
Name=org.freedesktop.Notifications
Exec=/usr/bin/python3 /usr/local/bin/notification-service.py
EOF
    export XDG_DATA_DIRS="$notification_data:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
fi

# The nested compositor connects to GNOME; its Xwayland and session bus stay
# private. Notifications use a separate, filtered socket when enabled.
if [ "${MAX_GPU:-0}" = 1 ]; then
    unset LIBGL_ALWAYS_SOFTWARE
    # The Wayland backend defaults to GL when --use-pixman is absent.
    set --
else
    export LIBGL_ALWAYS_SOFTWARE=1
    set -- --use-pixman
fi
exec dbus-run-session -- weston \
    --backend=wayland-backend.so "$@" \
    --socket=wayland-max --config=/etc/xdg/weston.ini \
    --width=1100 --height=800 --idle-time=0
