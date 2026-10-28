{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.agents-container;
  # Lexical only: never read runtime files or resolve SOPS symlinks at eval.
  # Keep components reversed so '..' can pop without traversing the filesystem.
  normalizeAbsolutePath = path: "/" + lib.concatStringsSep "/" (lib.reverseList (
    lib.foldl' (parts: part:
      if part == "" || part == "." then parts
      else if part == ".." then (if parts == [] then [] else builtins.tail parts)
      else [ part ] ++ parts
    ) [] (lib.splitString "/" path)
  ));
  runtimeSecretPath = path:
    path == null || (lib.hasPrefix "/" path && (let
      normalized = normalizeAbsolutePath path;
    in normalized != "/nix/store" && !lib.hasPrefix "/nix/store/" normalized));
  launcherText = import ./agents-container/launcher.nix {
    inherit lib;
    inherit (cfg) workspaceName gatewayName projectDirectoryName shellCommand updateInput;
  };
  work = import ./agents-container/image.nix {
    inherit pkgs lib;
    username = config.home.username;
    inherit (cfg) minimumFreePids imageName timeZone projectDirectoryName;
    webLinks = cfg.webLinks.enable;
    software = if cfg.software == null then {
      packages = []; environment = {}; etcFiles = {}; homeInit = "";
    } else cfg.software;
  };
  gateway = import ./container-gateway.nix {
    inherit pkgs lib;
    name = cfg.gatewayImageName;
    port = if cfg.proxy.port == null then 0 else cfg.proxy.port;
    inherit (cfg.proxy) directPort;
  };
  launcher = pkgs.writeShellApplication {
    name = "agents";
    runtimeInputs = [
      pkgs.coreutils
      pkgs.util-linux
      pkgs.nix
      pkgs.jq
      pkgs.curl
      pkgs.gawk
      pkgs.systemd
      pkgs.python3
    ];
    text = ''
      export AGENTS_SECRET_SOURCES=${lib.escapeShellArg (builtins.toJSON cfg.secretSources)}
      export AGENTS_NIX_ACCESS_TOKENS=${
        lib.escapeShellArg (if cfg.nixAccessTokensFile == null then "" else cfg.nixAccessTokensFile)
      }
      export AGENTS_CLIPBOARD=${if cfg.clipboard.enable then "1" else "0"}
      export AGENTS_SOUND=${if cfg.sound.enable then "1" else "0"}
      export AGENTS_WEB_LINKS=${if cfg.webLinks.enable then "1" else "0"}
      export AGENTS_KVM=${if cfg.kvm.enable then "1" else "0"}
      export AGENTS_FLAKE_OUTPUT=${lib.escapeShellArg cfg.flakeOutput}
      export AGENTS_PODMAN=${lib.escapeShellArg cfg.podmanExecutable}
      export AGENTS_STATE=${lib.escapeShellArg cfg.stateDirectory}
      export AGENTS_PROJECTS=${lib.escapeShellArg cfg.sharedDirectory}
      export AGENTS_HOME=${lib.escapeShellArg "/home/${config.home.username}"}
      export AGENTS_USER=${lib.escapeShellArg config.home.username}
      export AGENTS_IMAGE=${work.image}
      export AGENTS_IMAGE_REF=${work.image.imageName}:${work.image.imageTag}
      export AGENTS_GATEWAY_IMAGE=${gateway}
      export AGENTS_GATEWAY_REF=${gateway.imageName}:${gateway.imageTag}
      export AGENTS_ROOT=${work.root}
      export AGENTS_INIT=${work.init}
      export AGENTS_DEVICES=${lib.escapeShellArg (lib.concatStringsSep ":" cfg.gpuDevices)}
      export AGENTS_PROXY=${lib.escapeShellArg (if cfg.proxy.httpProxy == null then "" else cfg.proxy.httpProxy)}
      export AGENTS_SPEC=${cfg.runtimeSpec}
      export AGENTS_SECRET_HELPER=${./agents-container/secret-environment.py}
      ${launcherText}
    '';
  };
in
{
  options.services.agents-container = {
    enable = lib.mkEnableOption "the rootless agents workspace with explicit egress policy";
    software = lib.mkOption {
      type = lib.types.nullOr (lib.types.submodule {
        options = {
          packages = lib.mkOption { type = lib.types.listOf lib.types.package; };
          environment = lib.mkOption { type = lib.types.attrsOf lib.types.str; };
          etcFiles = lib.mkOption { type = lib.types.attrsOf lib.types.lines; };
          homeInit = lib.mkOption { type = lib.types.lines; };
        };
      });
      default = null;
      description = "Required caller software: packages, environment, etcFiles and homeInit. No implicit imports.";
    };
    imageName = lib.mkOption { type = lib.types.strMatching "[a-z0-9][a-z0-9./_-]*"; default = "localhost/agents"; };
    gatewayImageName = lib.mkOption { type = lib.types.strMatching "[a-z0-9][a-z0-9./_-]*"; default = "localhost/agents-gateway"; };
    workspaceName = lib.mkOption { type = lib.types.strMatching "[A-Za-z0-9_-]+"; default = "agents-workspace"; };
    gatewayName = lib.mkOption { type = lib.types.strMatching "[A-Za-z0-9_-]+"; default = "agents-workspace-gateway"; };
    nixDaemonName = lib.mkOption {
      type = lib.types.str;
      readOnly = true;
      default = "${cfg.workspaceName}-nix";
      description = "Private daemon sidecar name; the workspace has a read-only store.";
    };
    projectDirectoryName = lib.mkOption { type = lib.types.strMatching "[A-Za-z0-9_-]+"; default = "projects"; };
    timeZone = lib.mkOption { type = lib.types.str; default = "UTC"; };
    shellCommand = lib.mkOption { type = lib.types.strMatching "[A-Za-z0-9_./-]+"; default = "bash"; };
    updateInput = lib.mkOption { type = lib.types.nullOr (lib.types.strMatching "[A-Za-z0-9_-]+"); default = null; };
    proxy = {
      port = lib.mkOption { type = lib.types.nullOr lib.types.port; default = null; };
      directPort = lib.mkOption { type = lib.types.nullOr lib.types.port; default = null; };
      httpProxy = lib.mkOption { type = lib.types.nullOr lib.types.str; default = null; };
      listen = lib.mkOption { type = lib.types.str; default = "127.0.0.1"; };
      service = lib.mkOption { type = lib.types.nullOr lib.types.str; default = null; };
    };
    runtimeSpec = lib.mkOption {
      type = lib.types.strMatching "[0-9a-f]{64}";
      default = builtins.hashString "sha256" (builtins.toJSON {
        inherit (cfg) sharedDirectory stateDirectory gpuDevices kvm clipboard
          nixAccessTokensFile secretSources proxy;
        sound.enable = cfg.sound.enable;
        webLinks.enable = cfg.webLinks.enable;
        username = config.home.username;
        launcher = launcherText;
      });
      description = "Lifecycle compatibility hash; an adapter may preserve its earlier schema. Host-only sound maps are excluded.";
    };
    flakeOutput = lib.mkOption {
      type = lib.types.str;
      default = "agents";
      description = "Flake package output used in update/rebuild instructions.";
    };
    kvm.enable = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Require and expose /dev/kvm for hardware-accelerated virtual machines. Disable on WSL hosts without KVM.";
    };
    minimumFreePids = lib.mkOption {
      type = lib.types.ints.between 1 999999999999999999;
      # Observed Pi workers use about 17–19 threads each. Leave a conservative
      # margin for several workers/subprocesses, not a worst-case reservation.
      default = 256;
      description = ''
        Minimum free process/thread slots before a workspace session starts.
        The entrypoint refuses before secrets/home initialization when finite
        cgroup headroom is lower or cannot be verified. Init reaps exited
        orphans; this snapshot does not reserve resources or kill live workers.
      '';
    };
    podmanExecutable = lib.mkOption {
      type = lib.types.str;
      default = "podman";
      description = "Host Podman with working rootless UID/GID helpers and crun.";
    };
    sharedDirectory = lib.mkOption {
      type = lib.types.str;
      default = "${config.home.homeDirectory}/projects";
      description = "Host directory mounted under projectDirectoryName in the container HOME.";
    };
    stateDirectory = lib.mkOption {
      type = lib.types.str;
      default = "${config.xdg.dataHome}/agents-container";
      description = "Persistent private home and Nix store; never the host's store or daemon socket.";
    };
    nixAccessTokensFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = ''
        Host Nix access-tokens configuration (normally rendered by SOPS).
        Copied at runtime into a private directory mounted read-only in the
        workspace; never included in the image or Nix store. Null disables
        sharing. Agents can read the token, so use a least-privilege token.
      '';
    };
    secretSources = lib.mkOption {
      type = lib.types.attrsOf (lib.types.nullOr lib.types.str);
      default = {};
      description = ''
        Environment names mapped to absolute runtime file paths, never values
        or Nix paths. Null revokes an unavailable key. Files are read only by
        the host launcher, not by evaluation/build or Docker Env. Lexically
        normalized /nix/store and its descendants are rejected; runtime symlink
        targets are not checked by evaluation. Existing processes retain their
        inherited environment after rotation.
      '';
    };
    clipboard.enable = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Share the host clipboard in both directions via a clipboard-only Unix socket.";
    };
    clipboard.waylandSocket = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Absolute host Wayland socket for the clipboard broker; null uses the session environment. No display socket is mounted in the workspace.";
    };
    webLinks.enable = lib.mkEnableOption ''an HTTP(S)-only host browser bridge.
      Installs an xdg-open shim in the workspace and exposes only a dedicated
      Unix socket, never host display/D-Bus sockets. Requests from any workspace
      process can open the host user's default browser; local files, other URI
      schemes and embedded credentials are refused. Limited to one request per
      second and twenty per minute. Disabled by default on headless callers'';
    sound = {
      enable = lib.mkEnableOption "a playback-only host sound broker (no microphone/audio socket in the container)";
      events = lib.mkOption {
        type = lib.types.attrsOf (lib.types.nullOr lib.types.path);
        default = {};
        description = ''
          Host-only event-to-sound map. Set a Nix path to bundle a replacement
          in the store, an absolute host path to use an external file, or null
          to disable an event. Herdr sends done and request, never file paths.
        '';
      };
      configFile = lib.mkOption {
        type = lib.types.str;
        default = toString (pkgs.writeText "agents-sounds.json" (builtins.toJSON cfg.sound.events));
        defaultText = lib.literalExpression ''pkgs.writeText "agents-sounds.json" (builtins.toJSON config.services.agents-container.sound.events)'';
        description = ''
          Broker event map, declaratively generated from sound.events by default.
          Explicitly set ~/.config/agents/sounds.json to opt into a manually
          maintained map instead; it takes precedence over sound.events and is
          reloaded per request. Existing legacy maps are left untouched.
        '';
      };
    };
    gpuDevices = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [];
      description = "Intel/AMD render nodes to expose. All configured devices must exist.";
    };
    image = lib.mkOption {
      type = lib.types.package;
      readOnly = true;
      default = work.image;
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
        assertion = cfg.software != null;
        message = "agents-container requires explicit software injection.";
      }
      {
        assertion = cfg.proxy.port != null && cfg.proxy.httpProxy != null;
        message = "agents-container requires an explicit proxy port and httpProxy.";
      }
      {
        assertion = lib.all runtimeSecretPath (builtins.attrValues cfg.secretSources);
        message = "agents-container.secretSources must contain absolute runtime paths outside the store or null.";
      }
      {
        assertion = lib.all (name: builtins.match "[a-z][a-z0-9_-]{0,31}" name != null) (
          builtins.attrNames cfg.sound.events
        );
        message = "agents-container.sound.events keys must be valid broker event names.";
      }
      {
        assertion = lib.all (name: builtins.match "[A-Za-z_][A-Za-z0-9_]*" name != null) (
          builtins.attrNames cfg.secretSources
        );
        message = "agents-container.secretSources keys must be valid environment variable names.";
      }
      {
        assertion = cfg.proxy.listen == "127.0.0.1";
        message = "agents-container requires egress on 127.0.0.1 (pasta host-loopback mapping).";
      }
      {
        assertion = cfg.clipboard.waylandSocket == null || lib.hasPrefix "/" cfg.clipboard.waylandSocket;
        message = "agents-container.clipboard.waylandSocket must be an absolute path.";
      }
    ];
    home.packages = [ launcher ];
    systemd.user.services.agents-clipboard = lib.mkIf cfg.clipboard.enable {
      Unit.Description = "Clipboard-only bridge for the agents workspace";
      Service = {
        ExecStart = "${pkgs.python3}/bin/python3 ${./agents-container/clipboard.py} serve %t/agents-clipboard/socket";
        # wl-copy invokes cat to copy file-backed stdin, and xdg-mime for
        # untyped data; GNOME's MIME backend also needs gio.
        Environment = [
          "PATH=${
            lib.makeBinPath [
              pkgs.wl-clipboard
              pkgs.xclip
              pkgs.coreutils
              pkgs.glib
            ]
          }"
        ]
        ++ lib.optional (
          cfg.clipboard.waylandSocket != null
        ) "WAYLAND_DISPLAY=${cfg.clipboard.waylandSocket}";
        RuntimeDirectory = "agents-clipboard";
        RuntimeDirectoryMode = "0700";
        # Keep the bind-mounted directory valid across broker restarts.
        RuntimeDirectoryPreserve = "yes";
        UMask = "0077";
        Restart = "on-failure";
      };
    };
    systemd.user.services.agents-sound = lib.mkIf cfg.sound.enable {
      Unit.Description = "Playback-only sound bridge for Herdr and the agents workspace";
      Service = {
        ExecStart = lib.escapeShellArgs [
          "${pkgs.python3}/bin/python3"
          "${./agents-container/sound.py}"
          "%t/agents-sound/socket"
          cfg.sound.configFile
          "${pkgs.pulseaudio}/bin/paplay"
        ];
        RuntimeDirectory = "agents-sound";
        RuntimeDirectoryMode = "0700";
        RuntimeDirectoryPreserve = "yes";
        UMask = "0077";
        NoNewPrivileges = true;
        Restart = "on-failure";
      };
    };
    systemd.user.services.agents-web-links = lib.mkIf cfg.webLinks.enable {
      Unit.Description = "HTTP(S)-only host browser bridge for the agents workspace";
      Service = {
        ExecStart = lib.escapeShellArgs [
          "${pkgs.python3}/bin/python3" "-B"
          "${./agents-container/web-links.py}" "serve"
          "%t/agents-web-links/socket"
          "${pkgs.systemd}/bin/systemd-run"
          "${pkgs.xdg-utils}/bin/xdg-open"
        ];
        RuntimeDirectory = "agents-web-links";
        RuntimeDirectoryMode = "0700";
        RuntimeDirectoryPreserve = "yes";
        UMask = "0077";
        NoNewPrivileges = true;
        Restart = "on-failure";
      };
    };
    # Start explicitly with `systemctl --user start agents-container` or `agents
    # up`. An HM switch should not boot an agent workspace as a side effect.
    systemd.user.services.agents-container = {
      Unit = {
        Description = "Agents workspace with explicit egress policy";
        Wants = lib.optional (cfg.proxy.service != null) cfg.proxy.service;
        After = lib.optional (cfg.proxy.service != null) cfg.proxy.service;
      };
      Service = {
        Type = "oneshot";
        RemainAfterExit = true;
        ExecStart = "${launcher}/bin/agents up";
        ExecStop = "${launcher}/bin/agents down";
        TimeoutStartSec = 600;
        TimeoutStopSec = 60;
      };
    };
  };
}
