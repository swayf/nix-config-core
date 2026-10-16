{ version, source, containerfile }:
{ config, lib, pkgs, ... }:

let
  cfg = config.programs.max-container;
  clipboardPython = pkgs.python3.withPackages (ps: [ ps.python-xlib ]);
  buildContext = pkgs.runCommand "max-container-context-${version}" { } ''
    mkdir -p "$out"
    cp ${containerfile} "$out/Containerfile"
    cp ${./max/entrypoint.sh} "$out/entrypoint.sh"
    cp ${./max/max-session.sh} "$out/max-session.sh"
    cp ${./max/weston.ini} "$out/weston.ini"
    cp ${./max/weston-xwayland-unframed.patch} "$out/weston-xwayland-unframed.patch"
    cp ${./max/test_weston.py} "$out/test_weston.py"
    cp ${./max/clipboard.py} "$out/clipboard.py"
    cp ${./max/notification-service.py} "$out/notification-service.py"
    cp ${source} "$out/MAX.deb"
  '';
  # Changes to the package or build scripts select a new image automatically.
  image = "localhost/hm-max:${builtins.substring 0 32 (builtins.baseNameOf buildContext)}";
  launcher = pkgs.writeShellApplication {
    name = "max-container";
    runtimeInputs = [ pkgs.coreutils pkgs.util-linux ]
      ++ lib.optionals cfg.clipboard.enable [ pkgs.xclip pkgs.glib ];
    text = ''
      umask 077
      podman=${lib.escapeShellArg cfg.podmanExecutable}
      image=${lib.escapeShellArg image}
      shared_dir=${lib.escapeShellArg cfg.sharedDirectory}

      if (( EUID == 0 )); then
        echo "MAX must run as your desktop user, without sudo." >&2
        exit 1
      fi
      if ! command -v "$podman" >/dev/null 2>&1; then
        echo "Install rootless Podman on the host (including subuid/subgid mappings)." >&2
        echo "Configured executable: $podman" >&2
        exit 1
      fi
      if (( $# > 1 )); then
        echo "Usage: max-container [--build|--stop]" >&2
        exit 2
      fi
      case "''${1:-}" in
        --stop) exec "$podman" stop hm-max ;;
        ""|--build) ;;
        *) echo "Usage: max-container [--build|--stop]" >&2; exit 2 ;;
      esac
      # Podman fails even on `image exists` when a subordinate range includes
      # the invoking user's own ID. Show the offending host entry up front.
      host_uid=$(id -u)
      check_subid_range() {
        local subid_file=$1 own_id=$2 subid_owner subid_start subid_count
        [[ -r "$subid_file" ]] || return 0
        while IFS=: read -r subid_owner subid_start subid_count; do
          if [[ "$subid_owner" != ${lib.escapeShellArg config.home.username}
                && "$subid_owner" != "$host_uid" ]]; then
            continue
          fi
          if [[ "$subid_start" =~ ^[0-9]+$ && "$subid_count" =~ ^[0-9]+$ ]] \
            && (( own_id >= subid_start && own_id < subid_start + subid_count )); then
            echo "Invalid $subid_file entry $subid_owner:$subid_start:$subid_count: it includes your own ID $own_id." >&2
            echo "Assign a separate, unused subordinate range on the host, then restart Podman's rootless namespace." >&2
            return 1
          fi
        done < "$subid_file"
      }
      check_subid_range /etc/subuid "$host_uid"
      check_subid_range /etc/subgid "$(id -g)"
      : "''${XDG_RUNTIME_DIR:?Run from your logged-in desktop session}"
      # Serialize builds and launches without replacing an existing container.
      exec 9>"$XDG_RUNTIME_DIR/hm-max.lock"
      if ! flock -n 9; then
        echo "MAX is already running or its image is being built." >&2
        exit 0
      fi
      if [[ "''${1:-}" != --build ]]; then
        : "''${WAYLAND_DISPLAY:?MAX requires a Wayland session}"
        case "$WAYLAND_DISPLAY" in
          /*) wayland_socket=$WAYLAND_DISPLAY ;;
          *) wayland_socket=$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY ;;
        esac
        if [[ ! -S "$wayland_socket" || "$wayland_socket" == *,* ]]; then
          echo "Invalid Wayland socket: $wayland_socket" >&2
          exit 1
        fi
      fi
      if [[ "''${1:-}" == --build ]] || ! "$podman" image exists "$image"; then
        build_args=()
        if [[ "''${1:-}" == --build ]]; then
          build_args+=(--no-cache)
        fi
        "$podman" build "''${build_args[@]}" --format oci --tag "$image" ${buildContext}
      fi
      if [[ "''${1:-}" == --build ]]; then
        exit 0
      fi

      mkdir -p -- "$shared_dir"
      shared_dir=$(realpath -e -- "$shared_dir")
      host_home=$(realpath -e -- ${lib.escapeShellArg config.home.homeDirectory})
      # Reject an accidental HOME/ancestor mount, including through symlinks.
      if [[ "$shared_dir" == / || "$host_home/" == "$shared_dir/"* || "$shared_dir" == *,* ]]; then
        echo "Refusing to share the home directory, its ancestors, or a path containing a comma." >&2
        exit 1
      fi
      audio_args=()
      ${lib.optionalString cfg.audio.enable ''
        pulse_socket="$XDG_RUNTIME_DIR/pulse/native"
        if [[ ! -S "$pulse_socket" ]]; then
          echo "PulseAudio/PipeWire-Pulse socket is missing: $pulse_socket" >&2
          exit 1
        fi
        audio_args+=(
          --mount "type=bind,src=$pulse_socket,dst=/run/host-pulse,ro"
          --env PULSE_SERVER=unix:/run/host-pulse
        )
        ${lib.optionalString (cfg.audio.latencyMsec != null) ''
          audio_args+=(--env PULSE_LATENCY_MSEC=${toString cfg.audio.latencyMsec})
        ''}
      ''}
      device_args=()
      ${lib.optionalString (cfg.camera.devices != [ ] || cfg.graphics.renderDevice != null) ''
        device_args+=(--runtime=${lib.getExe pkgs.crun} --group-add=keep-groups)
      ''}
      ${lib.optionalString (cfg.graphics.renderDevice != null) ''
        render_device=${lib.escapeShellArg cfg.graphics.renderDevice}
        if [[ ! -c "$render_device" || ! -r "$render_device" || ! -w "$render_device" ]]; then
          echo "GPU render device is missing or not accessible: $render_device" >&2
          exit 1
        fi
        device_args+=(--device "$render_device:$render_device:rw" --env MAX_GPU=1)
      ''}
      camera_args=()
      ${lib.optionalString (cfg.camera.devices != [ ]) ''
        # crun preserves supplementary groups (e.g. video) in rootless mode.
        camera_devices=( ${lib.escapeShellArgs cfg.camera.devices} )
        for camera in "''${camera_devices[@]}"; do
          if [[ ! -c "$camera" || ! -r "$camera" || ! -w "$camera" ]]; then
            echo "Camera is missing or not accessible to your user: $camera" >&2
            exit 1
          fi
          camera_args+=(--device "$camera:$camera:rw")
        done
      ''}
      run_command=( "$podman" )
      ${lib.optionalString cfg.clipboard.enable ''
        : "''${DISPLAY:?Automatic clipboard exchange requires the host GNOME Xwayland DISPLAY}"
        run_command=( ${clipboardPython}/bin/python3 ${./max/clipboard.py} supervise "$podman" )
      ''}
      ${lib.optionalString cfg.notifications.enable ''
        run_command=( ${pkgs.python3}/bin/python3 ${./max/notification-launch.py}
          ${lib.getExe pkgs.xdg-dbus-proxy} "''${run_command[@]}" )
      ''}
      exec "''${run_command[@]}" run --rm --name hm-max \
        --pull=never \
        --userns=keep-id:uid=10001,gid=10001 --user 10001:10001 \
        --cap-drop=ALL --security-opt=no-new-privileges \
        --read-only --pid=private --ipc=private \
        --http-proxy=false \
        --network=pasta:--map-guest-addr,none,--map-host-loopback,none \
        --pids-limit=512 --shm-size=256m \
        --tmpfs /tmp:rw,nosuid,nodev,size=512m,mode=1777 \
        --volume hm-max-profile:/home/max:U \
        --mount "type=bind,src=$shared_dir,dst=/shared,rw,bind-nonrecursive" \
        --mount "type=bind,src=$wayland_socket,dst=/run/host-wayland,ro" \
        --env WAYLAND_DISPLAY=/run/host-wayland \
        "''${audio_args[@]}" \
        "''${device_args[@]}" \
        "''${camera_args[@]}" \
        "$image"
    '';
  };
in
{
  options.programs.max-container = {
    enable = lib.mkEnableOption "MAX in a rootless OCI container with a private nested desktop";
    sharedDirectory = lib.mkOption {
      type = lib.types.str;
      default = "${config.home.homeDirectory}/share-MAX";
      description = "The single host data directory mounted read-write at /shared.";
    };
    podmanExecutable = lib.mkOption {
      type = lib.types.str;
      default = "podman";
      example = "/usr/bin/podman";
      description = ''
        Host Podman executable. The host must provide working rootless Podman,
        pasta, newuidmap/newgidmap and subordinate UID/GID ranges.
        Home Manager cannot provision the privileged parts of that setup.
      '';
    };
    audio.enable = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Share the PulseAudio (or PipeWire-Pulse) socket. This permits recording
        as well as playback; it is not a playback-only permission.
      '';
    };
    audio.latencyMsec = lib.mkOption {
      type = lib.types.nullOr lib.types.ints.positive;
      default = null;
      example = 120;
      description = ''
        Requested libpulse buffering latency in milliseconds for MAX.
        Larger values may help with underruns but increase audio delay.
        Null keeps the application's defaults. Applies to playback and capture.
      '';
    };
    graphics.renderDevice = lib.mkOption {
      type = lib.types.nullOr (lib.types.strMatching "/dev/dri/renderD[0-9]+");
      default = null;
      example = "/dev/dri/renderD128";
      description = ''
        GPU render node for Mesa hardware acceleration. Null uses software
        rendering. Requires read/write access to the node on the host.
        The module supplies crun through Home Manager.
        Intended for Intel/AMD Mesa drivers, not proprietary NVIDIA drivers.
      '';
    };
    clipboard.enable = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Automatically exchange UTF-8 text and PNG images (up to 16 MiB) with
        the host clipboard only while MAX's nested desktop has host window
        focus. Requires GNOME Shell 45+ with the supplied focus extension
        enabled, and host Xwayland with DISPLAY and its authentication
        environment. Unavailable focus information disables exchange.
        The private clipboard is cleared on focus loss. Already read contents
        cannot be revoked. Files and PRIMARY selection are not transferred.
      '';
    };
    notifications.enable = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Forward desktop notifications to the host through an interface-filtered
        D-Bus proxy. The container keeps its private session bus and keyring.
        Notification actions and dismissal signals are relayed back to MAX;
        raising its nested window depends on the desktop's focus policy.
      '';
    };
    camera.devices = lib.mkOption {
      type = lib.types.listOf (lib.types.strMatching "/dev/video[0-9]+");
      default = [ ];
      example = [ "/dev/video0" ];
      description = ''
        V4L2 camera devices to pass through read-write. Empty disables camera
        access. The module supplies crun; supplementary groups are retained
        so rootless containers can use devices accessible through e.g. video.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = pkgs.stdenv.hostPlatform.system == "x86_64-linux";
        message = "MAX's pinned Linux package requires x86_64-linux.";
      }
      {
        assertion = lib.hasPrefix "/" cfg.sharedDirectory
          && cfg.sharedDirectory != "/"
          && cfg.sharedDirectory != config.home.homeDirectory
          && !(lib.hasInfix "," cfg.sharedDirectory)
          && !(lib.hasInfix "\n" cfg.sharedDirectory);
        message = "MAX needs a dedicated absolute sharedDirectory, without commas or newlines.";
      }
    ];
    home.packages = [ launcher ]
      ++ lib.optional (cfg.graphics.renderDevice != null || cfg.camera.devices != [ ]) pkgs.crun;
    xdg.dataFile."gnome-shell/extensions/max-clipboard-focus@home-manager" = lib.mkIf cfg.clipboard.enable {
      source = ./max/focus-extension;
      recursive = true;
    };
    # Enable with gnome-extensions after login; do not overwrite the user's
    # enabled-extensions list (which may include extensions outside this config).
    xdg.desktopEntries.max-container = {
      name = "MAX (container)";
      comment = "MAX with a private desktop and one shared folder";
      exec = "${launcher}/bin/max-container";
      icon = "internet-chat";
      terminal = false;
      categories = [ "Network" "InstantMessaging" ];
      startupNotify = false;
      settings = lib.optionalAttrs cfg.notifications.enable {
        "X-GNOME-UsesNotifications" = "true";
      };
    };
  };
}
