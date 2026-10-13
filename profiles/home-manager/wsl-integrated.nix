# Integrated HM updates with the system and never installs standalone switch CLI.
{ config, lib, ... }: {
  imports = [ ./wsl-common.nix ];
  options.wslProfile.hostName = lib.mkOption {
    type = lib.types.str;
    description = "Caller-owned host output suffix for integrated container packages.";
  };
  config = {
    programs.home-manager.enable = false;
    services.agents-container.flakeOutput = "agents-${config.wslProfile.hostName}";
    programs.gui-container.flakeOutput = "gcnt-${config.wslProfile.hostName}";
    programs.wsl-remote.server.enable = false;
    assertions = [ {
      assertion = !config.wslProfile.containers.enable || config.secrets.file != null;
      message = "NixOS-WSL containers require a machine-specific encrypted secrets.file.";
    } ];
  };
}
