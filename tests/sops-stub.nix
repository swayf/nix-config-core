# Contract-only fixture: declares SOPS options, performs no decryption or activation.
{ lib, ... }:
{
  options.sops = {
    useSystemdActivation = lib.mkOption { type = lib.types.bool; default = false; };
    age.sshKeyPaths = lib.mkOption { type = lib.types.listOf lib.types.str; default = [ ]; };
    gnupg.sshKeyPaths = lib.mkOption { type = lib.types.listOf lib.types.str; default = [ ]; };
    secrets = lib.mkOption {
      default = { };
      type = lib.types.attrsOf (lib.types.submodule ({ name, ... }: {
        options = {
          path = lib.mkOption { type = lib.types.str; default = "/run/synthetic-secrets/${name}"; };
          sopsFile = lib.mkOption { type = lib.types.path; };
          key = lib.mkOption { type = lib.types.str; };
          mode = lib.mkOption { type = lib.types.str; };
          restartUnits = lib.mkOption { type = lib.types.listOf lib.types.str; };
        };
      }));
    };
  };
}
