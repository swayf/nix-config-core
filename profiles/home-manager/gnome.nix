# The caller supplies GNOME application modules; no host identity is inferred.
{ lib, pkgs, ... }: {
  imports = [ ./desktop-common.nix ];
  desktop.environment = "gnome";
  programs.max-container.clipboard.enable = lib.mkDefault true;
  home.packages = [ pkgs.totem pkgs.baobab ];
}
