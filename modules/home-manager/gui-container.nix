{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.gui-container;
  image = import ./gui-container/image.nix {
    inherit pkgs lib;
    inherit (cfg) packages browserProfiles;
    gpu = cfg.graphics.autoDetect || cfg.graphics.renderDevice != null;
  };
  gateway = import ./container-gateway.nix {
    inherit pkgs lib;
    port = config.services.mihomo.us.port;
    # Deliberately no directPort, even when host DIRECT is enabled for agents.
    name = "localhost/hm-gui-gateway";
    # pasta mirrors host interfaces: do not collide with a host VPN's tun0.
    tunDevice = "gui-tun";
  };
  launcher = pkgs.writeShellApplication {
    name = "gui-container";
    runtimeInputs = [
      pkgs.coreutils
      pkgs.util-linux
      pkgs.curl
      pkgs.nix
    ];
    text = ''
      export GUI_FLAKE_OUTPUT=${lib.escapeShellArg cfg.flakeOutput}
      export GUI_PODMAN=${lib.escapeShellArg cfg.podmanExecutable}
      export GUI_STATE=${lib.escapeShellArg cfg.stateDirectory}
      export GUI_SHARED=${lib.escapeShellArg cfg.sharedDirectory}
      export GUI_IMAGE=${image}
      export GUI_IMAGE_REF=${image.imageName}:${image.imageTag}
      export GUI_GATEWAY_IMAGE=${gateway}
      export GUI_GATEWAY_REF=${gateway.imageName}:${gateway.imageTag}
      export GUI_PROXY=${lib.escapeShellArg config.services.mihomo.us.httpProxy}
      export GUI_GPU=${
        lib.escapeShellArg (if cfg.graphics.renderDevice == null then "" else cfg.graphics.renderDevice)
      }
      export GUI_GPU_AUTO=${if cfg.graphics.autoDetect then "1" else "0"}
      export GUI_AUDIO=${if cfg.audio.enable then "1" else "0"}
      export GUI_WAYLAND_SOCKET=${
        lib.escapeShellArg (if cfg.waylandSocket == null then "" else cfg.waylandSocket)
      }
      export GUI_SPEC=${
        builtins.hashString "sha256" (
          builtins.toJSON {
            inherit (cfg)
              stateDirectory
              sharedDirectory
              graphics
              audio
              waylandSocket
              ;
            launcher = builtins.readFile ./gui-container/launcher.sh;
          }
        )
      }
      ${builtins.readFile ./gui-container/launcher.sh}
    '';
  };
  gcnt = pkgs.writeShellApplication {
    name = "gcnt";
    text = ''
      exec ${launcher}/bin/gui-container "$@"
    '';
  };
  directoryOption =
    default: description:
    lib.mkOption {
      type = lib.types.str;
      inherit default description;
    };
in
{
  imports = [
    (lib.mkRemovedOptionModule [ "programs" "gui-container" "width" ] ''
      Applications now use host Wayland/WSLg windows. Remove width and resize each window on the host.
    '')
    (lib.mkRemovedOptionModule [ "programs" "gui-container" "height" ] ''
      Applications now use host Wayland/WSLg windows. Remove height and resize each window on the host.
    '')
    (lib.mkRemovedOptionModule [ "programs" "gui-container" "browser" "autostart" ] ''
      Browsers no longer start automatically. Remove this option and use
      gcnt firefox, gcnt brave, gcnt chrome or the container shell to launch them.
    '')
  ];
  options.programs.gui-container = {
    enable = lib.mkEnableOption "container applications on host Wayland/WSLg with fail-closed Mihomo US egress";
    flakeOutput = lib.mkOption {
      type = lib.types.str;
      default = "gcnt";
      description = "Flake package output suggested after update (gcnt-wsl for WSL).";
    };
    podmanExecutable = lib.mkOption {
      type = lib.types.str;
      default =
        if config.platform.isNixOS then "/run/current-system/sw/bin/podman" else "/usr/bin/podman";
      description = "Host rootless Podman with crun, pasta and subordinate UID/GID mappings.";
    };
    waylandSocket = lib.mkOption {
      type = lib.types.nullOr (lib.types.strMatching "/.+");
      default = null;
      example = "/mnt/wslg/runtime-dir/wayland-0";
      description = ''
        Explicit absolute host Wayland socket. Null resolves WAYLAND_DISPLAY
        against XDG_RUNTIME_DIR as before. WSLg can use a separate socket path
        without replacing the systemd/Podman runtime directory. Only this socket
        is mounted, not its parent directory. GCNT_WAYLAND_SOCKET overrides the
        declaration at runtime; an empty value selects WAYLAND_DISPLAY instead
        (for waypipe). Changing socket recreates the GUI container and closes
        its existing windows, but preserves its private HOME.
      '';
    };
    stateDirectory = directoryOption "${config.xdg.dataHome}/gui-container" "Private persistent home/profile directory; not the host home.";
    sharedDirectory = directoryOption "${config.home.homeDirectory}/share-GUI" "Dedicated host directory exposed read-write at /shared.";
    packages = lib.mkOption {
      type = lib.types.listOf lib.types.package;
      default = [ ];
      example = lib.literalExpression "[ pkgs.gimp pkgs.gedit ]";
      description = "Extra programs in the immutable image, in addition to Firefox, Brave, Google Chrome, Thunar and foot. Rebuild Home Manager to update; no host Nix store or daemon is shared.";
    };
    browserProfiles = lib.mkOption {
      default = { };
      description = ''
        Container-only browser profiles and host desktop launchers. Attribute names
        identify persistent directories and gcnt-profile-NAME commands; do not
        rename them to change a launcher label. Default Firefox/Brave/Chrome launchers
        and their existing default profiles remain available.
      '';
      type = lib.types.attrsOf (
        lib.types.submodule (
          { config, name, ... }: {
            options = {
              browser = lib.mkOption {
                type = lib.types.enum [
                  "firefox"
                  "brave"
                  "chrome"
                ];
                description = "Browser to launch inside the container.";
              };
              label = lib.mkOption {
                type = lib.types.str;
                default = name;
                description = "Host desktop launcher label (UTF-8 supported).";
              };
              order = lib.mkOption {
                type = lib.types.int;
                default = 100;
                description = "Legacy panel order, retained for compatibility. Host application menus choose their own order.";
              };
              icon = lib.mkOption {
                type = lib.types.nullOr lib.types.str;
                default = null;
                description = "Host icon theme name or host-visible image path (prefer a Nix store path) for the desktop launcher.";
              };
              color = lib.mkOption {
                type = lib.types.strMatching "#[0-9a-fA-F]{6}";
                default = if config.browser == "firefox" then "#ff9e64" else "#7dcfff";
                description = "Legacy panel accent color, retained for compatibility; unused by host desktop launchers.";
              };
              extraArgs = lib.mkOption {
                type = lib.types.listOf lib.types.str;
                default = [ ];
                description = "Additional browser arguments, e.g. URLs or Chromium feature flags.";
              };
              firefoxSettings = lib.mkOption {
                type = lib.types.attrsOf (
                  lib.types.oneOf [
                    lib.types.bool
                    lib.types.int
                    lib.types.str
                  ]
                );
                default = { };
                description = "Firefox preferences written to this profile's user.js on launch. Not supported for Brave or Chrome.";
              };
            };
          }
        )
      );
    };
    graphics.autoDetect = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Automatically expose the first accessible Intel/AMD Mesa render node when renderDevice is null; fall back to software rendering if none is available. No proprietary NVIDIA or WSL /dev/dxg support.";
    };
    graphics.renderDevice = lib.mkOption {
      type = lib.types.nullOr (lib.types.strMatching "/dev/dri/renderD[0-9]+");
      default = null;
      description = "Explicit Intel/AMD Mesa render node, overriding automatic detection. Null with autoDetect disabled uses software rendering; no proprietary NVIDIA support.";
    };
    audio.enable = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Expose host PulseAudio/PipeWire-Pulse. Permits both playback and microphone capture.";
    };
    image = lib.mkOption {
      type = lib.types.package;
      readOnly = true;
      default = image;
    };
    gatewayImage = lib.mkOption {
      type = lib.types.package;
      readOnly = true;
      default = gateway;
    };
  };
  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = lib.all (name: builtins.match "[a-zA-Z0-9][a-zA-Z0-9_-]*" name != null) (
          builtins.attrNames cfg.browserProfiles
        );
        message = "gui-container browserProfiles names must contain only letters, digits, underscores and hyphens, starting with a letter or digit.";
      }
      {
        assertion = lib.all (profile: profile.browser == "firefox" || profile.firefoxSettings == { }) (
          builtins.attrValues cfg.browserProfiles
        );
        message = "gui-container browserProfiles.firefoxSettings is only supported for Firefox.";
      }
      {
        assertion = config.services.mihomo.us.enable && config.services.mihomo.us.listen == "127.0.0.1";
        message = "gui-container requires the Mihomo US listener on 127.0.0.1.";
      }
      {
        assertion =
          lib.all
            (
              path:
              lib.hasPrefix "/" path
              && path != "/"
              && path != config.home.homeDirectory
              && !(lib.hasInfix "," path)
              && !(lib.hasInfix "\n" path)
            )
            [
              cfg.stateDirectory
              cfg.sharedDirectory
            ]
          && cfg.stateDirectory != cfg.sharedDirectory;
        message = "gui-container needs distinct dedicated absolute state/shared directories, not HOME, without commas or newlines.";
      }
    ];
    home.packages = [
      launcher
      gcnt
    ];
    xdg.desktopEntries = import ./gui-container/host-desktop.nix {
      inherit gcnt lib pkgs;
      inherit (cfg) browserProfiles;
    };
  };
}
