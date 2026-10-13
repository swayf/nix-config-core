{ nixpkgs, homeManager, nixosWsl, module }:
let
  lib = nixpkgs.lib;
  pkgs = nixpkgs.legacyPackages.x86_64-linux;
  homeStub = { lib, osConfig, ... }: {
    options = {
      wslProfile.containers.enable = lib.mkEnableOption "synthetic container capability";
      secrets.file = lib.mkOption { type = lib.types.nullOr lib.types.path; default = null; };
    };
    config.home = {
      username = osConfig.wsl.defaultUser;
      homeDirectory = osConfig.users.users.${osConfig.wsl.defaultUser}.home;
      stateVersion = osConfig.system.stateVersion;
    };
  };
  mk = extra: lib.nixosSystem {
    specialArgs = {
      wslModule = nixosWsl.nixosModules.default;
      homeManagerModule = homeManager.nixosModules.home-manager;
      nixpkgsInput = nixpkgs;
      wslHomeModules = [ homeStub ];
    };
    modules = [ module {
      nixpkgs.hostPlatform = "x86_64-linux";
      networking.hostName = "fixture-wsl";
      wsl.defaultUser = "alice";
      users.users.alice.home = "/srv/alice";
      system.stateVersion = "26.05";
    } extra ];
  };
  base = mk { };
  full = mk {
    workstation.wsl.containers.enable = true;
    workstation.wsl.secretsFile = ./fixtures/device.yaml;
  };
  missing = mk { workstation.wsl.containers.enable = true; };
  override = mk {
    workstation.wsl.checkoutPath = "/srv/deployment";
    workstation.wsl.flakeOutput = "selected-wsl";
  };
  invalid = mk { workstation.wsl.checkoutPath = "relative"; };
  failed = system: map (a: a.message) (lib.filter (a: !a.assertion) system.config.assertions);
  c = base.config;
  h = c.home-manager.users.alice;
  result = {
    callerIdentity = c.wsl.defaultUser == "alice" && h.home.homeDirectory == "/srv/alice";
    secretFree = !c.workstation.wsl.containers.enable && h.secrets.file == null;
    full = full.config.home-manager.users.alice.wslProfile.containers.enable
      && full.config.home-manager.users.alice.secrets.file == ./fixtures/device.yaml;
    failClosed = builtins.any (s: lib.hasInfix "secretsFile" s) (failed missing)
      && builtins.any (s: lib.hasInfix "checkoutPath" s) (failed invalid);
    helperDefaults = c.workstation.wsl.checkoutPath == "/srv/alice/home-manager"
      && c.workstation.wsl.flakeOutput == "fixture-wsl";
    helperOverrides = override.config.workstation.wsl.checkoutPath == "/srv/deployment"
      && override.config.workstation.wsl.flakeOutput == "selected-wsl";
    podman = c.virtualisation.podman.enable
      && c.virtualisation.containers.containersConf.settings.engine.runtime == "crun"
      && c.users.users.alice.linger;
    deviceRules = builtins.elem "z /dev/net/tun 0660 root wsl-containers -" c.systemd.tmpfiles.rules
      && builtins.elem "z /dev/fuse 0660 root wsl-containers -" c.systemd.tmpfiles.rules;
    isolated = !c.services.openssh.enable && c.networking.firewall.allowedTCPPorts == [ ]
      && !h.programs.home-manager.enable && !h.targets.genericLinux.enable;
    assertions = failed base == [ ] && failed full == [ ];
    toplevel = lib.hasSuffix ".drv" c.system.build.toplevel.drvPath;
  };
in {
  inherit result;
  system = base.config.system.build.toplevel;
  helper = lib.findFirst (p: (p.name or "") == "wsl-host") (throw "wsl-host missing") c.environment.systemPackages;
  check = assert lib.all (x: x) (builtins.attrValues result);
    pkgs.runCommand "wsl-system-contracts" { } ''
      echo ${lib.escapeShellArg (builtins.toJSON result)} > $out
    '';
}
