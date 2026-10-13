# Caller-supplied application modules; no desktop imports or personal identity.
{ config, lib, pkgs, ... }:
let enabled = config.wslProfile.containers.enable;
in {
  imports = [ ../../modules/home-manager/platform.nix ];
  options.wslProfile.containers.enable = lib.mkOption {
    type = lib.types.bool;
    default = true;
    description = "Enable containers and required US egress after provisioning.";
  };
  config = {
    home.packages = [ pkgs.git pkgs.sops pkgs.age ];
    programs.bash.enable = true;
    xdg.enable = true;
    targets.genericLinux.enable = !config.platform.isNixOS;
    targets.genericLinux.gpu.enable = false;
    services.mihomo = {
      us.enable = enabled;
      direct.enable = false;
    };
    services.agents-container = {
      enable = enabled;
      flakeOutput = lib.mkDefault "agents-wsl";
      kvm.enable = false;
      gpuDevices = [ ];
      nixAccessTokensFile = null;
      clipboard.waylandSocket = "/mnt/wslg/runtime-dir/wayland-0";
      sound.enable = false;
    };
    programs.gui-container = {
      enable = enabled;
      flakeOutput = lib.mkDefault "gcnt-wsl";
      waylandSocket = "/mnt/wslg/runtime-dir/wayland-0";
      graphics.autoDetect = false;
      graphics.renderDevice = null;
      audio.enable = false;
    };
  };
}
