# The caller supplies application modules, identity, stateVersion and hardware.
{ config, lib, ... }: {
  imports = [ ../../modules/home-manager/platform.nix ../../modules/home-manager/desktop.nix ];
  programs.home-manager.enable = lib.mkDefault true;
  services.agents-container = {
    enable = lib.mkDefault true;
    sound.enable = lib.mkDefault true;
  };
  programs.gui-container.enable = lib.mkDefault true;
  programs.max-container = {
    enable = lib.mkDefault true;
    sharedDirectory = lib.mkDefault "${config.home.homeDirectory}/share-MAX";
    podmanExecutable = lib.mkDefault (
      if config.platform.isNixOS then "/run/current-system/sw/bin/podman" else "/usr/bin/podman"
    );
    audio.enable = lib.mkDefault true;
    audio.latencyMsec = lib.mkDefault 120;
    notifications.enable = lib.mkDefault true;
    camera.devices = lib.mkDefault [ ];
  };
  targets.genericLinux.enable = lib.mkDefault (!config.platform.isNixOS);
  targets.genericLinux.gpu.enable = lib.mkDefault (!config.platform.isNixOS);
  programs.chromium.enable = lib.mkDefault true;
  programs.rdp = {
    enable = lib.mkDefault true;
    commonArgs = lib.mkDefault [
      "+clipboard"
      "/dynamic-resolution"
      "/cert:tofu"
      "/floatbar:sticky:off,default:visible,show:fullscreen"
    ];
  };
  programs.yandex-browser = {
    enable = lib.mkDefault true;
    beta.enable = lib.mkDefault true;
    profileDir = lib.mkDefault "${config.home.homeDirectory}/.local/share/yandex-browser";
    beta.profileDir = lib.mkDefault "${config.home.homeDirectory}/.config/yandex-browser-beta";
    commandLineArgs = lib.mkDefault [ "--proxy-server=${config.services.mihomo.httpProxy}" ];
  };
}
