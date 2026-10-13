# Profile contract only: real application behaviour is tested by the caller.
{ lib, ... }:
let attrs = lib.mkOption { type = lib.types.attrsOf lib.types.anything; default = { }; };
in {
  options = {
    services.agents-container = attrs;
    services.mihomo = attrs;
    programs.gui-container = attrs;
    programs.max-container = attrs;
    programs.rdp = attrs;
    programs.yandex-browser = attrs;
    programs.hm = attrs;
    programs.wsl-remote = attrs;
  };
  config.services.mihomo.httpProxy = "http://127.0.0.1:7890";
}
