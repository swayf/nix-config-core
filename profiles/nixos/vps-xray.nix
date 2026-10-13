# Partial VPS capability profile, not a host or installer.
# Caller owns user/access, hardware, package set, runtime material and stateVersion.
{ lib, ... }:
{
  imports = [
    ../../modules/nixos/vps-hardening.nix
    ../../modules/nixos/xray-declarative.nix
    ../../modules/nixos/xray-monitoring.nix
  ];
  services.xray.declarative.enable = lib.mkDefault true;
  services.xrayMonitoring.enable = lib.mkDefault true;
}
