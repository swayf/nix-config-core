# Standalone HM; the caller supplies its helper module and pinned nixpkgs flake.
{ config, lib, pkgs, nixpkgsInput, ... }: {
  imports = [ ./wsl-common.nix ];
  programs.home-manager.enable = true;
  programs.hm = {
    profile = lib.mkDefault "${config.home.username}-wsl";
    flakePath = lib.mkDefault "${config.home.homeDirectory}/home-manager";
  };
  nix.package = pkgs.nix;
  nix.registry.nixpkgs.flake = nixpkgsInput;
  nix.keepOldNixPath = false;
  nix.nixPath = [ "nixpkgs=flake:nixpkgs" ];
  nix.settings = {
    experimental-features = [ "nix-command" "flakes" ];
    nix-path = [ "nixpkgs=flake:nixpkgs" ];
  };
}
