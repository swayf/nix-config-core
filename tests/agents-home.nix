# Caller-pinned HM contracts and standalone synthetic build targets.
{ nixpkgs, homeManager, module }:
let
  pkgs = import nixpkgs { system = "x86_64-linux"; };
  lib = pkgs.lib;
  software = {
    packages = [ pkgs.hello ];
    environment.FIXTURE_ONLY = "synthetic";
    etcFiles."agents/fixture.txt" = "synthetic only\n";
    homeInit = "test -d \"$HOME\"";
  };
  proxy = { port = 2081; httpProxy = "http://127.0.0.1:2081"; service = "fixture-egress.service"; };
  make = homeDirectory: settings: homeManager.lib.homeManagerConfiguration {
    inherit pkgs;
    modules = [ module {
      home = { username = "alice"; inherit homeDirectory; stateVersion = "26.05"; };
      services.agents-container = settings;
    } ];
  };
  settings = { enable = true; inherit software proxy; };
  minimal = make "/srv/alice" settings;
  alternate = make "/var/lib/fixture" (settings // {
    secretSources = { FIXTURE_KEY = "/run/fixture/key"; MISSING_KEY = null; };
    clipboard = { enable = true; waylandSocket = "/run/fixture/wayland-0"; };
    sound.enable = true;
  });
  disabled = make "/srv/alice" {};
  missingSoftware = make "/srv/alice" { enable = true; inherit proxy; };
  missingProxy = make "/srv/alice" { enable = true; inherit software; };
  invalidSource = make "/srv/alice" (settings // { secretSources.BAD = "relative"; });
  storeSource = make "/srv/alice" (settings // { secretSources.BAD = "/nix/store/fixture-secret"; });
  sourceHome = source: make "/srv/alice" (settings // { secretSources.FIXTURE_KEY = source; });
  storeAliases = [
    "/nix/store" "/nix/store/" "//nix/store/fixture-secret"
    "/nix/./store/fixture-secret" "/nix/../nix/store/fixture-secret"
    "/run/../nix/store/fixture-secret" "/../../nix/store/fixture-secret"
    "/nix//store///fixture-secret" "/nix/store/dir/../fixture-secret"
    "/nix/store/." "/nix/store/dir/.." "///nix/./store//"
  ];
  runtimeAliases = [
    null "/run/fixture/key" "//run//fixture/./key"
    "/run/fixture/dir/../key" "/../../run/fixture/key"
    "/nix/storehouse/key" "/nix/store-other/key" "/nix/store/../runtime/key"
  ];
  changedSound = make "/srv/alice" (settings // { sound.events.changed = null; });
  invalidName = make "/srv/alice" (settings // { secretSources."INVALID-NAME" = null; });
  invalidListen = make "/srv/alice" (settings // { proxy = proxy // { listen = "0.0.0.0"; }; });
  invalidWayland = make "/srv/alice" (settings // { clipboard.waylandSocket = "relative"; });
  failed = h: lib.filter (a: !a.assertion) h.config.assertions;
  rejects = h: !(builtins.tryEval h.activationPackage.drvPath).success;
  agentsServices = h: lib.filterAttrs (n: _: lib.hasPrefix "agents-" n) h.config.systemd.user.services;
  launcher = h: lib.findFirst (p: p.name == "agents")
    (throw "agents launcher missing from home.packages") h.config.home.packages;
  result = {
    assertions = failed minimal == [] && failed alternate == [] && failed disabled == [];
    explicitSoftware = rejects missingSoftware;
    explicitProxy = rejects missingProxy;
    runtimePathsOnly = rejects invalidSource && rejects storeSource;
    lexicalStorePaths = lib.all (source: rejects (sourceHome source)) storeAliases;
    lexicalRuntimePaths = lib.all (source:
      (builtins.tryEval (sourceHome source).activationPackage.drvPath).success
      && (sourceHome source).config.services.agents-container.secretSources.FIXTURE_KEY == source
    ) runtimeAliases;
    soundMapDoesNotReplaceWorkspace = minimal.config.services.agents-container.runtimeSpec
      == changedSound.config.services.agents-container.runtimeSpec;
    environmentNames = rejects invalidName;
    loopbackOnly = rejects invalidListen;
    absoluteWayland = rejects invalidWayland;
    noDesktopDefaults = !minimal.config.services.agents-container.kvm.enable
      && minimal.config.services.agents-container.gpuDevices == []
      && !minimal.config.services.agents-container.sound.enable
      && !minimal.config.services.agents-container.clipboard.enable
      && minimal.config.services.agents-container.nixAccessTokensFile == null
      && minimal.config.services.agents-container.proxy.directPort == null;
    noImplicitUpdater = minimal.config.services.agents-container.updateInput == null
      && minimal.config.services.agents-container.shellCommand == "bash";
    disabledHasNoServices = agentsServices disabled == {};
    noAutoStart = !(minimal.config.systemd.user.services.agents-container ? Install);
    brokers = builtins.attrNames (agentsServices alternate) == [ "agents-clipboard" "agents-container" "agents-sound" ];
    hostSoundMapEmpty = alternate.config.services.agents-container.sound.events == {};
    generations = lib.hasSuffix ".drv" minimal.activationPackage.drvPath
      && lib.hasSuffix ".drv" alternate.activationPackage.drvPath;
  };
in {
  inherit result;
  image = minimal.config.services.agents-container.image;
  gateway = minimal.config.services.agents-container.gatewayImage;
  launcher = launcher minimal;
  brokerLauncher = launcher alternate;
  check = assert lib.all (value: value) (builtins.attrValues result);
    pkgs.writeText "agents-home-contracts.json" (builtins.toJSON result);
}
