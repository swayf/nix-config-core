{ nixpkgs, homeManager, sopsModule, ruleSeed, modules }:
let
  pkgs = import nixpkgs { system = "x86_64-linux"; config.allowUnfree = true; };
  lib = pkgs.lib;
  mk = enabled: file: homeManager.lib.homeManagerConfiguration {
    inherit pkgs;
    extraSpecialArgs = { inherit sopsModule; mihomoRuleSeed = ruleSeed; };
    modules = [ modules.wsl-gui-device {
      home = { username = "alice"; homeDirectory = "/srv/alice"; stateVersion = "26.05"; };
      platform.isNixOS = true;
      wslProfile.containers.enable = enabled;
      secrets.file = file;
    } ];
  };
  enabled = mk true ./fixtures/gui-device.yaml;
  disabled = mk false null;
  missing = mk true null;
  c = enabled.config;
  failures = h: map (a: a.message) (lib.filter (a: !a.assertion) h.config.assertions);
  result = {
    callerIdentity = c.home.username == "alice" && c.home.homeDirectory == "/srv/alice";
    gui = c.programs.gui-container.enable && !c.programs.gui-container.graphics.autoDetect
      && c.programs.gui-container.graphics.renderDevice == null && !c.programs.gui-container.audio.enable;
    proxy = c.services.mihomo.us.enable && !c.services.mihomo.direct.enable
      && c.services.mihomo.us.listen == "127.0.0.1";
    device = builtins.attrNames c.sops.secrets == [ "mhnodes" ]
      && c.sops.secrets.mhnodes.sopsFile == ./fixtures/gui-device.yaml
      && c.sops.secrets.mhnodes.path == "/srv/alice/.local/state/mihomo/nodes.yaml"
      && c.sops.age.keyFile == "/srv/alice/.config/sops/age/keys.txt";
    runtime = c.systemd.user.services.mihomo.Unit.Requires == [ "sops-nix.service" ]
      && lib.hasInfix "empty-fallback: REJECT" c.sops.templates."mihomo-config.yaml".content;
    isolation = !(c.services ? agents-container) && !(c.programs ? hm)
      && !c.programs.home-manager.enable && !(c.programs ? wsl-remote);
    secretFree = !disabled.config.programs.gui-container.enable
      && builtins.attrNames disabled.config.sops.secrets == [ ]
      && !(disabled.config.systemd.user.services ? mihomo);
    failClosed = !(builtins.tryEval missing.activationPackage.drvPath).success
      && failures enabled == [ ] && failures disabled == [ ];
    generation = lib.hasSuffix ".drv" enabled.activationPackage.drvPath;
  };
in {
  inherit result;
  activation = enabled.activationPackage;
  image = c.programs.gui-container.image;
  gateway = c.programs.gui-container.gatewayImage;
  check = assert lib.all (x: x) (builtins.attrValues result);
    pkgs.runCommand "gui-proxy-contracts" { } ''
      echo ${lib.escapeShellArg (builtins.toJSON result)} > $out
    '';
}
