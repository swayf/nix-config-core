{ config, lib, ... }: {
  options.desktop = {
    environment = lib.mkOption {
      type = lib.types.enum [ "gnome" "kde" ];
      description = "Explicit Linux desktop capability profile.";
    };
    homeOutput = lib.mkOption {
      type = lib.types.str;
      default = "${config.home.username}-${config.desktop.environment}";
      description = "Caller-owned Home Manager output used by profile helpers.";
    };
  };
}
