# VPS policy/profile; host identity, hardware and credential sources belong to the caller.
{ config, lib, pkgs, ... }:
let
  cfg = config.services.vps;
  xray = config.services.xray.declarative;
  # Beat upstream mkDefault values without requiring callers to use mkForce.
  profileDefault = lib.mkOverride 900;
  public = lib.recursiveUpdate {
    port = 443;
    tag = "nix-${config.networking.hostName}-vless";
    outbounds = [
      { tag = "direct"; protocol = "freedom"; }
      { tag = "blocked"; protocol = "blackhole"; }
    ] ++ lib.optional xray.warp.enable { tag = "warp"; protocol = "wireguard"; };
    sniffing = { enabled = true; destOverride = [ "http" "tls" "quic" ]; routeOnly = true; };
    routing = {
      domainStrategy = "IPIfNonMatch";
      rules = [
        { type = "field"; ip = [ "geoip:private" ]; outboundTag = "blocked"; }
        { type = "field"; protocol = [ "bittorrent" ]; outboundTag = "blocked"; }
      ] ++ lib.optional (xray.warp.enable && cfg.warpDomains != []) {
        type = "field"; domain = map (d: "domain:${d}") cfg.warpDomains; outboundTag = "warp";
      };
    };
  } cfg.settings;
in
{
  imports = [
    ../../modules/nixos/xray-declarative.nix
    ../../modules/nixos/xray-monitoring.nix
    # Hardening currently returns only config; scope that config to profile enable.
    ({ config, lib, ... }: {
      config = lib.mkIf config.services.vps.enable
        (import ../../modules/nixos/vps-hardening.nix { inherit config; });
    })
  ];
  options.services.vps = {
    enable = lib.mkEnableOption "key-only native Xray VPS baseline";
    adminUser = lib.mkOption {
      type = lib.types.strMatching "[a-z_][a-z0-9_-]{0,31}";
      default = "admin";
      description = "Administrator login; callers migrating an existing host must set its current name explicitly.";
    };
    adminKeys = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [];
      description = "Caller-owned administrator SSH public keys.";
    };
    sshPort = lib.mkOption { type = lib.types.port; default = 22; };
    packages = lib.mkOption {
      type = lib.types.listOf lib.types.package;
      default = [ pkgs.curl pkgs.openssl pkgs.git ];
      description = "Convenience packages added to the system; set [] to omit them without removing NixOS-required packages.";
    };
    settings = lib.mkOption {
      type = lib.types.attrsOf lib.types.anything;
      description = "Public Xray settings; profile defaults may be overridden, never include credentials.";
    };
    warpDomains = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ "ipinfo.io" ];
      description = "DNS names routed through WARP when enabled.";
    };
  };
  options.systemd.network.networks = lib.mkOption {
    type = lib.types.attrsOf (lib.types.submodule {
      config = lib.mkIf cfg.enable {
        networkConfig = {
          IPv6AcceptRA = lib.mkForce false;
          LinkLocalAddressing = lib.mkForce "no";
        };
      };
    });
  };
  config = lib.mkIf cfg.enable {
    assertions = [
      { assertion = !builtins.elem cfg.adminUser [ "root" "nobody" ]; message = "VPS administrator must not be root or nobody."; }
      { assertion = cfg.adminKeys != []; message = "VPS requires at least one administrator SSH public key."; }
      { assertion = cfg.sshPort != xray.settings.port; message = "SSH and VLESS ports must differ."; }
      { assertion = lib.all (d: builtins.match "[a-zA-Z0-9][a-zA-Z0-9.-]*" d != null) cfg.warpDomains;
        message = "WARP routes require explicit DNS names, not arbitrary routing expressions."; }
    ];
    # Operational tuning uses standard NixOS options, not duplicate VPS knobs.
    time.timeZone = lib.mkDefault "UTC";
    networking = {
      useDHCP = lib.mkDefault false;
      useNetworkd = lib.mkDefault true;
      firewall.enable = lib.mkDefault true;
      enableIPv6 = lib.mkForce false;
    };
    boot.kernelParams = [ "ipv6.disable=1" ];
    boot.kernel.sysctl = {
      "net.ipv6.conf.all.disable_ipv6" = lib.mkForce 1;
      "net.ipv6.conf.default.disable_ipv6" = lib.mkForce 1;
    };
    services.resolved = {
      enable = lib.mkDefault true;
      settings.Resolve = { LLMNR = lib.mkDefault false; MulticastDNS = lib.mkDefault false; };
    };
    services.openssh = {
      enable = lib.mkDefault true;
      ports = [ cfg.sshPort ];
      openFirewall = lib.mkDefault true;
      hostKeys = lib.mkDefault [ { path = "/etc/ssh/ssh_host_ed25519_key"; type = "ed25519"; } ];
      settings = {
        PasswordAuthentication = lib.mkForce false;
        KbdInteractiveAuthentication = lib.mkForce false;
        PermitRootLogin = lib.mkForce "no";
        AllowUsers = lib.mkForce [ cfg.adminUser ];
      };
    };
    users.users.${cfg.adminUser} = {
      isNormalUser = true;
      extraGroups = [ "wheel" ];
      openssh.authorizedKeys.keys = cfg.adminKeys;
    };
    security.sudo.wheelNeedsPassword = lib.mkDefault false;
    nix.settings = {
      experimental-features = profileDefault [ "nix-command" "flakes" ];
      trusted-users = profileDefault [ "root" ];
      max-jobs = lib.mkDefault 1;
      cores = lib.mkDefault 2;
    };
    nix.gc = {
      automatic = lib.mkDefault true;
      dates = lib.mkDefault "weekly";
      options = lib.mkDefault "--delete-older-than 14d";
    };
    services.journald.extraConfig = profileDefault "SystemMaxUse=200M";
    environment.systemPackages = cfg.packages;
    services.xray.declarative = {
      enable = lib.mkDefault true;
      warp.enable = lib.mkDefault true;
      settings = lib.mkDefault public;
    };
    services.xrayMonitoring.enable = lib.mkDefault true;
    # Runtime material/WARP/Telegram paths use the canonical child options.
    # SOPS is optional and must be imported explicitly by the caller.
  };
}
