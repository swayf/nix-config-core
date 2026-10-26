{
  pkgs,
  lib,
  packages,
  gpu,
  browserProfiles ? { },
  browserPolicies ? { firefox = { }; brave = { }; chrome = { }; },
  timeZone ? "UTC",
}:
assert builtins.match "[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)*" timeZone != null;
let
  # Keep UTF-8 English/Russian without shipping the full locale archive.
  locales = pkgs.glibcLocales.override {
    allLocales = false;
    locales = [
      "en_US.UTF-8/UTF-8"
      "ru_RU.UTF-8/UTF-8"
    ];
  };
  fonts = pkgs.makeFontsConf {
    fontDirectories = [
      pkgs.dejavu_fonts
      pkgs.noto-fonts-color-emoji
    ];
  };
  graphics = pkgs.buildEnv {
    name = "gui-container-mesa";
    paths = [
      pkgs.mesa
      pkgs.libglvnd
    ];
    pathsToLink = [
      "/lib"
      "/share"
    ];
  };
  applications = import ./applications.nix {
    inherit pkgs lib browserProfiles;
    firefoxPolicies = browserPolicies.firefox;
  };
  execApp = pkgs.writeShellScript "gui-exec" (builtins.readFile ./exec.sh);
  session = pkgs.writeShellScript "gui-session" (builtins.readFile ./session.sh);
  entrypoint = pkgs.writeShellScript "gui-entrypoint" ''
    set -euo pipefail
    mkdir -p "$XDG_RUNTIME_DIR" "$HOME/.cache/fontconfig"
    chmod 700 "$XDG_RUNTIME_DIR"
    # No applications from the previous container are alive at this point.
    ${applications.resetChromiumLocks}
    unset DISPLAY
    # This long-lived session also adopts children of podman exec applications.
    # A real PID 1 must reap them after the last window closes.
    exec ${pkgs.catatonit}/bin/catatonit -- ${pkgs.dbus}/bin/dbus-run-session \
      --config-file=${pkgs.dbus}/share/dbus-1/session.conf -- ${session}
  '';
  etc = pkgs.runCommand "gui-container-etc" { } ''
    mkdir -p "$out/etc/ssl/certs" "$out/etc/xdg/foot"
    ${lib.optionalString (browserPolicies.brave != { }) ''
      mkdir -p "$out/etc/brave/policies/managed"
      cp ${pkgs.writeText "gcnt-brave-policies.json" (builtins.toJSON browserPolicies.brave)} \
        "$out/etc/brave/policies/managed/gcnt.json"
    ''}
    ${lib.optionalString (browserPolicies.chrome != { }) ''
      mkdir -p "$out/etc/opt/chrome/policies/managed"
      cp ${pkgs.writeText "gcnt-chrome-policies.json" (builtins.toJSON browserPolicies.chrome)} \
        "$out/etc/opt/chrome/policies/managed/gcnt.json"
    ''}
    # Container defaults only; persistent user mimeapps.list can override these.
    cat > "$out/etc/xdg/mimeapps.list" <<'EOF'
    [Default Applications]
    inode/directory=thunar.desktop;
    # libmagic reports bind-mount roots (/shared, HOME) as mount points.
    inode/mount-point=thunar.desktop;
    EOF
    cat > "$out/etc/xdg/foot/foot.ini" <<'EOF'
    [main]
    font=DejaVu Sans Mono:size=11
    shell=/bin/bash
    EOF
    printf 'root:x:0:0:root:/root:/bin/bash\ngui:x:1000:1000:GUI:/home/gui:/bin/bash\nnobody:x:65534:65534:nobody:/var/empty:/bin/false\n' > "$out/etc/passwd"
    printf 'root:x:0:\ngui:x:1000:\nnogroup:x:65534:\n' > "$out/etc/group"
    echo 'hosts: files dns' > "$out/etc/nsswitch.conf"
    test -f ${pkgs.tzdata}/share/zoneinfo/${timeZone}
    ln -s ${pkgs.tzdata}/share/zoneinfo/${timeZone} "$out/etc/localtime"
    echo ${timeZone} > "$out/etc/timezone"
    ln -s ${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt "$out/etc/ssl/certs/ca-certificates.crt"
  '';
  root = pkgs.buildEnv {
    name = "gui-container-root";
    paths =
      packages
      ++ applications.packages
      ++ [
        (lib.hiPrio etc)
        applications.firefox
        pkgs.brave
        pkgs.google-chrome
        pkgs.foot
        # Thunar supplies org.freedesktop.FileManager1 for browser "Show in folder".
        # Its settings daemon must also be discoverable on our private session bus.
        pkgs.thunar
        pkgs.xfconf
        pkgs.xdg-utils
        pkgs.glib.bin
        pkgs.shared-mime-info
        pkgs.dbus
        pkgs.wayland-utils
        pkgs.bashInteractive
        pkgs.coreutils
        pkgs.curl
        pkgs.dnsutils
        pkgs.cacert
        pkgs.fontconfig
        pkgs.dejavu_fonts
        pkgs.noto-fonts-color-emoji
        pkgs.adwaita-icon-theme
        pkgs.hicolor-icon-theme
      ];
    pathsToLink = [
      "/bin"
      "/etc"
      "/share"
    ];
    postBuild = ''
      mkdir -p "$out/opt/gui" "$out/run"
      ln -s ${execApp} "$out/opt/gui/exec"
      ln -s ${entrypoint} "$out/opt/gui/entrypoint"
      ln -s ${graphics} "$out/run/opengl-driver"
      # GIO's MIME fallback needs a cache covering all merged desktop entries.
      rm -f "$out/share/applications/mimeinfo.cache"
      ${pkgs.desktop-file-utils}/bin/update-desktop-database "$out/share/applications"
    '';
  };
in
# Keep only the stream executable in the store, not a duplicate image tarball.
pkgs.dockerTools.streamLayeredImage {
  name = "localhost/hm-gui";
  # Fewer overlay layers; keep some granularity for reuse between rebuilds.
  maxLayers = 16;
  contents = [ root ];
  extraCommands = ''
    mkdir -p tmp run home/gui shared usr/bin
    chmod 1777 tmp
    ln -s /bin/env usr/bin/env
    # Podman resolves the image user before mounting anything.
    for file in passwd group; do
      rm "etc/$file"
      cp "${etc}/etc/$file" "etc/$file"
    done
  '';
  fakeRootCommands = "chown -R 1000:1000 home/gui shared";
  config = {
    User = "1000:1000";
    WorkingDir = "/home/gui";
    Entrypoint = [ "/opt/gui/entrypoint" ];
    Env = [
      "HOME=/home/gui"
      "USER=gui"
      "SHELL=/bin/bash"
      "PATH=/bin"
      "XDG_RUNTIME_DIR=/tmp/runtime-1000"
      "WAYLAND_DISPLAY=/run/host-wayland"
      "XDG_SESSION_TYPE=wayland"
      "GDK_BACKEND=wayland"
      "QT_QPA_PLATFORM=wayland"
      "XDG_DATA_DIRS=/share"
      "XDG_CONFIG_DIRS=/etc/xdg"
      "LANG=en_US.UTF-8"
      "TZ=${timeZone}"
      "TZDIR=${pkgs.tzdata}/share/zoneinfo"
      "LOCALE_ARCHIVE=${locales}/lib/locale/locale-archive"
      "FONTCONFIG_FILE=${fonts}"
      "MOZ_ENABLE_WAYLAND=1"
      "NIXOS_OZONE_WL=1"
      "XCURSOR_PATH=/share/icons"
      "XCURSOR_THEME=Adwaita"
      "SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt"
      "TERM=xterm-256color"
      "LIBGL_DRIVERS_PATH=${graphics}/lib/dri"
      "__EGL_VENDOR_LIBRARY_DIRS=${graphics}/share/glvnd/egl_vendor.d"
    ]
    ++ lib.optional (!gpu) "LIBGL_ALWAYS_SOFTWARE=1";
  };
}
