# Real caller-pinned HM/SOPS/Plasma, synthetic identity and capability stubs.
{ nixpkgs, homeManager, sopsModule, plasmaModule, modules }:
let
  pkgs = nixpkgs.legacyPackages.x86_64-linux;
  lib = pkgs.lib;
  mk = profile: extra: homeManager.lib.homeManagerConfiguration {
    inherit pkgs;
    extraSpecialArgs = { nixpkgsInput = nixpkgs; };
    modules = [
      profile modules.secrets sopsModule ./home-capabilities-stub.nix
      {
        home.username = "alice";
        home.homeDirectory = "/srv/alice";
        home.stateVersion = "26.05";
      }
    ] ++ extra;
  };
  failures = h: map (a: a.message) (builtins.filter (a: !a.assertion) h.config.assertions);
  g = mk modules.gnome [ ];
  k = mk modules.kde [ plasmaModule ];
  w = mk modules.wsl-standalone [ { wslProfile.containers.enable = false; } ];
  integrated = mk modules.wsl-integrated [ {
    platform.isNixOS = true;
    wslProfile.hostName = "fixture";
    wslProfile.containers.enable = false;
  } ];
  consumer = { config, lib, hasSecret, secretFile, ... }: {
    home.sessionVariables = {
      HAS_NODE = if hasSecret "node" then "yes" else "no";
      HAS_API = if hasSecret "api" then "yes" else "no";
      HAS_OTHER = if hasSecret "other" then "yes" else "no";
      HAS_MISSING = if hasSecret "missing" then "yes" else "no";
    };
    sops.secrets = lib.optionalAttrs (hasSecret "node") {
      node.sopsFile = secretFile "node";
    } // lib.optionalAttrs (hasSecret "api") {
      api.sopsFile = secretFile "api";
    } // lib.optionalAttrs (hasSecret "other") {
      other.sopsFile = secretFile "other";
    };
  };
  device = mk modules.wsl-integrated [ consumer {
    platform.isNixOS = true;
    wslProfile.hostName = "fixture";
    secrets.file = ./fixtures/device.yaml;
    secrets.allowedKeys = [ "node" ];
  } ];
  legacy = mk modules.wsl-common [ consumer {
    wslProfile.containers.enable = false;
    secrets.files = { primary = ./fixtures/device.yaml; secondary = ./fixtures/other.yaml; };
    secrets.layout.secondary = [ "other" ];
    secrets.defaultGroup = "primary";
    secrets.editor = {
      enable = true;
      fileNames = { primary = "fixture/primary.yaml"; secondary = "fixture/secondary.yaml"; };
    };
    secrets.requestedKeys = [ "missing" "nested/token" ];
  } ];
  invalidDevice = mk modules.wsl-integrated [ { wslProfile.hostName = "fixture"; } ];
  invalidSources = mk modules.wsl-common [ {
    wslProfile.containers.enable = false;
    secrets.file = ./fixtures/device.yaml;
    secrets.files.primary = ./fixtures/other.yaml;
  } ];
  overridden = g.extendModules { modules = [ {
    home.homeDirectory = lib.mkForce "/srv/another";
    programs.max-container.audio.latencyMsec = 80;
  } ]; };
  result = {
    secretFree = failures w == [ ] && w.config.sops.secrets == { }
      && w.config.secrets.file == null && w.config.secrets.files == { };
    callerIdentity = g.config.home.username == "alice" && k.config.home.homeDirectory == "/srv/alice"
      && g.config.sops.age.keyFile == "/srv/alice/.config/sops/age/keys.txt";
    desktopIsolation = g.config.desktop.environment == "gnome" && k.config.desktop.environment == "kde"
      && g.config.programs.max-container.clipboard.enable && !k.config.programs.max-container.clipboard.enable
      && k.config.programs.plasma.enable && !(g.config.programs.plasma.enable or false)
      && k.config.dconf.settings == { };
    wslIsolation = !(w.config ? desktop) && !(integrated.config ? desktop)
      && !w.config.targets.genericLinux.gpu.enable && !integrated.config.targets.genericLinux.enable;
    entrypoints = w.config.programs.home-manager.enable && !integrated.config.programs.home-manager.enable
      && w.config.programs.hm.flakePath == "/srv/alice/home-manager"
      && integrated.config.programs.hm == { }
      && !integrated.config.programs.wsl-remote.server.enable
      && integrated.config.services.agents-container.flakeOutput == "agents-fixture";
    consumerSelection = device.config.home.sessionVariables.HAS_NODE == "yes"
      && device.config.home.sessionVariables.HAS_API == "no"
      && device.config.home.sessionVariables.HAS_OTHER == "no"
      && builtins.attrNames device.config.sops.secrets == [ "node" ];
    legacyLayout = legacy.config.home.sessionVariables.HAS_API == "yes"
      && legacy.config.home.sessionVariables.HAS_OTHER == "yes"
      && legacy.config.home.sessionVariables.HAS_MISSING == "no"
      && legacy.config.sops.secrets.other.sopsFile == ./fixtures/other.yaml
      && legacy.config.sops.secrets.node.sopsFile == ./fixtures/device.yaml;
    failClosed = !(builtins.tryEval invalidDevice.activationPackage.drvPath).success
      && !(builtins.tryEval invalidSources.activationPackage.drvPath).success;
    overrideDefaults = overridden.config.programs.max-container.sharedDirectory == "/srv/another/share-MAX"
      && overridden.config.programs.max-container.audio.latencyMsec == 80;
    assertions = lib.all (h: failures h == [ ]) [ g k w integrated device legacy overridden ];
    evaluatedGenerations = lib.all (h: lib.hasSuffix ".drv" h.activationPackage.drvPath) [ g k w integrated device legacy ];
  };
in assert lib.assertMsg (lib.all (v: v) (builtins.attrValues result))
  (builtins.toJSON result);
{
  inherit result;
  check = pkgs.runCommand "home-manager-profile-contract" { } ''
    printf '%s\n' ${lib.escapeShellArg (builtins.toJSON result)} > $out
  '';
  editor = lib.findFirst (p: (p.name or "") == "edit-secrets")
    (throw "Missing editor") legacy.config.home.packages;
}
