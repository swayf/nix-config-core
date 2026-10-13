# Bounded integrated GUI profile, without Agents or caller policy resources.
{ config, lib, sopsModule, mihomoRuleSeed, ... }:
let enabled = config.wslProfile.containers.enable;
in {
  imports = [
    sopsModule
    ../../modules/home-manager/platform.nix
    ../../modules/home-manager/secrets.nix
    ../../modules/home-manager/cli.nix
    ../../modules/home-manager/playwright.nix
    ../../modules/home-manager/mihomo.nix
    ../../modules/home-manager/gui-container.nix
    ../../modules/home-manager/gui-container/windows-shortcuts.nix
  ];
  options.wslProfile.containers.enable = lib.mkOption {
    type = lib.types.bool;
    default = false;
    description = "Enable GUI and US proxy with a provisioned per-device source.";
  };
  config = {
    assertions = [ {
      assertion = !enabled || config.secrets.file != null;
      message = "WSL GUI requires its own encrypted device source.";
    } ];
    secrets = { allowedKeys = [ "mhnodes" ]; requestedKeys = lib.optional enabled "mhnodes"; };
    services.mihomo = {
      ruleSeed = mihomoRuleSeed;
      us.enable = enabled;
      direct.enable = false;
    };
    programs.gui-container = {
      enable = enabled;
      waylandSocket = "/mnt/wslg/runtime-dir/wayland-0";
      graphics.autoDetect = false;
      graphics.renderDevice = null;
      audio.enable = false;
    };
    programs.bash.enable = true;
    programs.home-manager.enable = false;
    xdg.enable = true;
  };
}
