# Home Manager passes osConfig = null for standalone evaluation.
{ lib, osConfig, ... }: {
  options.platform.isNixOS = lib.mkOption {
    type = lib.types.bool;
    default = osConfig != null;
    defaultText = lib.literalExpression "osConfig != null";
    description = "Whether NixOS owns the session environment and GPU driver links.";
  };
}
