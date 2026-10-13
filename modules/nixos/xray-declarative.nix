{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.xray.declarative;
  publicConfig = pkgs.writeText "xray-public.json" (
    builtins.toJSON (
      cfg.settings
      // {
        inherit (cfg) statsPort;
      }
    )
  );
  materialPath = cfg.materialFile;
  warpPath = cfg.warp.materialFile;
  warpProbe = pkgs.writeShellApplication {
    name = "xray-warp-probe";
    runtimeInputs = [ pkgs.python3 pkgs.curl config.services.xray.package ];
    text = ''
      exec python3 ${../../scripts/xray-warp-probe.py} --renderer ${./xray-render.py}
    '';
  };
  render = pkgs.writeShellScript "xray-render" ''
    set -eu
    export PATH=${
      lib.makeBinPath [
        pkgs.openssl
        pkgs.coreutils
      ]
    }
    ${pkgs.python3}/bin/python3 ${./xray-render.py} \
      --settings ${publicConfig} --material "$CREDENTIALS_DIRECTORY/material" \
      --output /run/xray-config/config.json ${lib.optionalString cfg.warp.enable ''--warp "$CREDENTIALS_DIRECTORY/warp"''}
    # Xray may echo malformed fields on error; do not put test output in journal.
    if ! ${config.services.xray.package}/bin/xray run -test -config /run/xray-config/config.json >/dev/null 2>&1; then
      echo 'Xray configuration validation failed' >&2
      rm -f /run/xray-config/config.json
      exit 1
    fi
  '';
in
{
  options.services.xray.declarative = {
    enable = lib.mkEnableOption "native, credential-preserving VLESS/REALITY Xray";
    settings = lib.mkOption {
      # Resolve overrides per field (including client-removal specialisations).
      # Raw types.attrs would serialize nested mkForce records into the JSON.
      type = lib.types.attrsOf lib.types.anything;
      description = "Public serverName, clients (names), port, tag, optional target/listen/outbounds/routing. Never place credentials here.";
    };
    statsPort = lib.mkOption {
      type = lib.types.port;
      default = 10085;
    };
    warp = {
      enable = lib.mkEnableOption "encrypted userspace WARP outbound and isolated probe";
      materialFile = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "Runtime WireGuard JSON outside the store; supplied by the caller or explicit SOPS adapter.";
      };
    };
    materialFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Runtime material JSON outside the store; supplied by the caller or explicit SOPS adapter.";
    };
  };
  config = lib.mkIf cfg.enable (
    lib.mkMerge [
      {
        assertions = [
          {
            assertion = !cfg.warp.enable || cfg.warp.materialFile != null;
            message = "WARP requires runtime warp.materialFile.";
          }
          {
            assertion = cfg.warp.materialFile == null ||
              (lib.hasPrefix "/" cfg.warp.materialFile && !lib.hasPrefix "/nix/store/" cfg.warp.materialFile);
            message = "WARP materialFile must be an absolute runtime path outside the Nix store.";
          }
          {
            assertion = lib.all (o: (o.protocol or "") != "wireguard" ||
              ((o.tag or "") == "warp" && (o.settings or {}) == {})) (cfg.settings.outbounds or []);
            message = "WireGuard settings must come from a runtime credential; use an empty warp outbound placeholder.";
          }
          {
            assertion = cfg.warp.enable == lib.any (o: (o.tag or "") == "warp") (cfg.settings.outbounds or []);
            message = "WARP enable and the warp outbound placeholder must agree.";
          }
          {
            assertion = cfg.materialFile != null;
            message = "Xray requires runtime materialFile (or the explicit SOPS adapter).";
          }
          {
            assertion =
              cfg.materialFile == null
              || (lib.hasPrefix "/" cfg.materialFile && !lib.hasPrefix "/nix/store/" cfg.materialFile);
            message = "Xray materialFile must be an absolute runtime path outside the Nix store.";
          }
          {
            assertion = cfg.settings.port != cfg.statsPort && cfg.settings.tag != "stats-api";
            message = "Xray public inbound must not conflict with the localhost StatsService.";
          }
        ];
        environment.systemPackages = lib.optional cfg.warp.enable warpProbe;
        services.xray = {
          enable = true;
          settingsFile = "/run/xray-config/config.json";
        };
        networking.firewall.allowedTCPPorts = [ cfg.settings.port ];
        systemd.services.xray-config = {
          description = "Validate and materialize Xray credentials outside the store";
          before = [ "xray.service" ];
          # No RemainAfterExit: every Xray start/restart re-renders current material.
          serviceConfig = {
            Type = "oneshot";
            ExecStart = render;
            LoadCredential = [ "material:${materialPath}" ] ++ lib.optional cfg.warp.enable "warp:${warpPath}";
            RuntimeDirectory = "xray-config";
            RuntimeDirectoryMode = "0700";
            RuntimeDirectoryPreserve = true;
            UMask = "0077";
            ProtectSystem = "strict";
            ProtectHome = true;
            PrivateTmp = true;
            PrivateDevices = true;
            NoNewPrivileges = true;
            ProtectKernelTunables = true;
            ProtectKernelModules = true;
            ProtectControlGroups = true;
            RestrictAddressFamilies = [
              "AF_UNIX"
              "AF_INET"
              "AF_INET6"
            ];
            LimitCORE = 0;
          };
        };
        systemd.services.xray = {
          requires = [ "xray-config.service" ];
          after = [ "xray-config.service" ];
          restartTriggers = [
            publicConfig
            render
          ];
          serviceConfig = {
            Restart = "on-failure";
            RestartSec = "5s";
            CapabilityBoundingSet = lib.mkForce [ "CAP_NET_BIND_SERVICE" ];
            AmbientCapabilities = lib.mkForce [ "CAP_NET_BIND_SERVICE" ];
            UMask = "0077";
            ProtectSystem = "strict";
            ProtectHome = true;
            PrivateTmp = true;
            PrivateDevices = true;
            ProtectKernelTunables = true;
            ProtectKernelModules = true;
            ProtectKernelLogs = true;
            ProtectControlGroups = true;
            ProtectClock = true;
            RestrictAddressFamilies = [
              "AF_UNIX"
              "AF_INET"
              "AF_INET6"
            ];
            RestrictSUIDSGID = true;
            LockPersonality = true;
            LimitCORE = 0;
          };
        };
      }
    ]
  );
}
