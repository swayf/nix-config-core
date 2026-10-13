# Caller-owned NixOS-WSL/HM pins and home modules; no fleet or checkout imports.
{ config, lib, pkgs, wslModule, homeManagerModule, nixpkgsInput,
  wslHomeModules, ... }:
let
  cfg = config.workstation.wsl;
  username = config.wsl.defaultUser;
  rebuild = pkgs.writeShellApplication {
    name = "wsl-host";
    # sudo must resolve through the NixOS wrapper, not an unwrapped store binary.
    runtimeInputs = [ pkgs.nix pkgs.nixos-rebuild ];
    text = ''
      checkout=${lib.escapeShellArg cfg.checkoutPath}
      checkout="''${WSL_FLAKE:-$checkout}"
      host=${lib.escapeShellArg cfg.flakeOutput}
      (( $# == 1 )) || { echo 'Usage: wsl-host {build|switch|rollback|status}' >&2; exit 2; }
      case "$1" in
        build)
          exec nix build --no-link --no-write-lock-file \
            "path:$checkout#nixosConfigurations.$host.config.system.build.toplevel"
          ;;
        switch)
          exec sudo nixos-rebuild switch --flake "path:$checkout#$host" --no-write-lock-file
          ;;
        rollback) exec sudo nixos-rebuild switch --rollback ;;
        status) exec nixos-version ;;
        *) echo 'Usage: wsl-host {build|switch|rollback|status}' >&2; exit 2 ;;
      esac
    '';
  };
in {
  imports = [ wslModule homeManagerModule ];
  options.workstation.wsl = {
    containers.enable = lib.mkEnableOption "gcnt/agents with machine-specific Mihomo credentials";
    secretsFile = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      description = "Encrypted per-device SOPS YAML; never the legacy personal secret file.";
    };
    checkoutPath = lib.mkOption {
      type = lib.types.str;
      default = "${config.users.users.${username}.home}/home-manager";
      description = "Caller-owned flake directory used by wsl-host; WSL_FLAKE overrides at runtime.";
    };
    flakeOutput = lib.mkOption {
      type = lib.types.str;
      default = config.networking.hostName;
      description = "Selected nixosConfigurations output used by wsl-host.";
    };
  };
  config = {
    assertions = [
      {
        assertion = !cfg.containers.enable || cfg.secretsFile != null;
        message = "Provision workstation.wsl.secretsFile before enabling WSL containers.";
      }
      {
        assertion = lib.hasPrefix "/" cfg.checkoutPath && builtins.match "[a-z][a-z0-9-]*" cfg.flakeOutput != null;
        message = "wsl-host requires an absolute checkoutPath and a valid flakeOutput name.";
      }
    ];
    warnings = lib.optional (!cfg.containers.enable)
      "WSL: gcnt/agents are disabled until the device SOPS file and age identity are provisioned.";
    wsl.enable = true;
    wsl.wslConf.boot.systemd = true;
    nix.settings.experimental-features = [ "nix-command" "flakes" ];
    nix.registry.nixpkgs.flake = nixpkgsInput;
    nixpkgs.config.allowUnfree = true;
    users.groups.wsl-containers = { };
    users.users.${username} = {
      linger = true;
      extraGroups = [ "wsl-containers" ];
      subUidRanges = [ { startUid = 100000; count = 65536; } ];
      subGidRanges = [ { startGid = 100000; count = 65536; } ];
    };
    virtualisation.podman = {
      enable = true;
      defaultNetwork.settings.dns_enabled = true;
    };
    virtualisation.containers.containersConf.settings.engine.runtime = "crun";
    # Permission-only rules: never fabricate missing kernel device nodes.
    systemd.tmpfiles.rules = [
      "z /dev/net/tun 0660 root wsl-containers -"
      "z /dev/fuse 0660 root wsl-containers -"
    ];
    environment.systemPackages = [ pkgs.git pkgs.rsync pkgs.sops pkgs.age rebuild ];
    home-manager = {
      useGlobalPkgs = true;
      useUserPackages = true;
      users.${username} = {
        imports = wslHomeModules;
        wslProfile.containers.enable = cfg.containers.enable;
        secrets.file = cfg.secretsFile;
      };
    };
  };
}
