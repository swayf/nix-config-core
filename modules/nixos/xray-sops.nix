# Optional bridge. The caller explicitly supplies its pinned SOPS NixOS module.
{ config, lib, sopsModule, ... }:
let
  cfg = config.services.xray.declarative;
  monitoring = config.services.xrayMonitoring;
  materialPath = config.sops.secrets.xray_material.path;
  warpPath = config.sops.secrets.xray_warp.path;
  tokenPath = config.sops.secrets.monitoring_telegram_token.path;
in
{
  imports = [ sopsModule ./xray-declarative.nix ./xray-monitoring.nix ];
  options = {
    services.xray.declarative = {
      secretFile = lib.mkOption {
        type = lib.types.nullOr lib.types.path;
        default = null;
        description = "Dedicated caller-owned SOPS YAML with material and optional warp keys.";
      };
      warp.useSops = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = "Use the warp key from secretFile. Disable explicitly for a separate runtime WARP file.";
      };
    };
    services.xrayMonitoring.telegram.secretFile = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      description = "Dedicated caller-owned SOPS YAML with bot_token; never a fleet default.";
    };
  };
  config = lib.mkMerge [
    (lib.mkIf (cfg.enable && cfg.secretFile != null) {
      services.xray.declarative.materialFile = lib.mkDefault materialPath;
      assertions = [{
        assertion = cfg.materialFile == materialPath;
        message = "Choose either Xray secretFile or a separate runtime materialFile, not both.";
      }];
      sops.useSystemdActivation = true;
      sops.age.sshKeyPaths = lib.mkDefault [ "/etc/ssh/ssh_host_ed25519_key" ];
      sops.gnupg.sshKeyPaths = lib.mkDefault [ ];
      sops.secrets.xray_material = {
        sopsFile = cfg.secretFile;
        key = "material";
        mode = "0400";
        restartUnits = [ "xray.service" ];
      };
      systemd.services.xray-config = {
        requires = [ "sops-install-secrets.service" ];
        after = [ "sops-install-secrets.service" ];
      };
    })
    (lib.mkIf (cfg.enable && cfg.secretFile != null && cfg.warp.enable && cfg.warp.useSops) {
      services.xray.declarative.warp.materialFile = lib.mkDefault warpPath;
      assertions = [{
        assertion = cfg.warp.materialFile == warpPath;
        message = "Disable warp.useSops explicitly to supply a separate runtime WARP file.";
      }];
      sops.secrets.xray_warp = {
        sopsFile = cfg.secretFile;
        key = "warp";
        mode = "0400";
        restartUnits = [ "xray.service" ];
      };
    })
    (lib.mkIf (monitoring.enable && monitoring.telegram.enable && monitoring.telegram.secretFile != null) {
      services.xrayMonitoring.telegram.botTokenFile = lib.mkDefault tokenPath;
      assertions = [{
        assertion = monitoring.telegram.botTokenFile == tokenPath;
        message = "Choose either Telegram secretFile or a separate runtime botTokenFile, not both.";
      }];
      sops.useSystemdActivation = true;
      sops.age.sshKeyPaths = lib.mkDefault [ "/etc/ssh/ssh_host_ed25519_key" ];
      sops.gnupg.sshKeyPaths = lib.mkDefault [ ];
      sops.secrets.monitoring_telegram_token = {
        sopsFile = monitoring.telegram.secretFile;
        key = "bot_token";
        mode = "0400";
        restartUnits = [ "alertmanager.service" ];
      };
      systemd.services.alertmanager = {
        requires = [ "sops-install-secrets.service" ];
        after = [ "sops-install-secrets.service" ];
      };
    })
  ];
}
