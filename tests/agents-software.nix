# Native eval contracts: no nested Nix CLI, builds or writable HOME required.
{ pkgs, factory ? import ../modules/home-manager/agents-container/software.nix }:
let
  inherit (pkgs) lib;
  args = { inherit pkgs; packages = []; environment = {}; resources = {}; homeInit = ""; };
  empty = factory args;
  project = s: {
    packages = map (p: p.drvPath) s.packages;
    inherit (s) environment etcFiles homeInit;
  };
  rejects = value: !(builtins.tryEval (builtins.deepSeq (project value) true)).success;
  selected = [ pkgs.hello pkgs.gnugrep pkgs.hello ];
  context = "${pkgs.hello}/share/fixture";
  example = factory (args // {
    packages = selected;
    environment.FIXTURE = context;
    resources = {
      "agents/fixture/settings.json" = { text = "{\"synthetic\":true}\n"; };
      "agents/fixture/context" = { text = context; };
      "agents/fixture/source.nix" = { source = ./agents-software.nix; };
    };
    homeInit = context;
  });
  result = {
    exactRuntimeShape = builtins.attrNames empty == [ "environment" "etcFiles" "homeInit" "packages" ];
    noDefaults = project empty == { packages = []; environment = {}; etcFiles = {}; homeInit = ""; };
    allArgumentsRequired = builtins.functionArgs factory == {
      pkgs = false; packages = false; environment = false; resources = false; homeInit = false;
    };
    callerLibraryOnly = project (factory (args // { pkgs = { inherit lib; }; })) == project empty;
    selectedPackages = (project example).packages == map (p: p.drvPath) selected;
    explicitResources = builtins.attrNames example.etcFiles == [
      "agents/fixture/context" "agents/fixture/settings.json" "agents/fixture/source.nix"
    ] && example.etcFiles."agents/fixture/settings.json" == "{\"synthetic\":true}\n"
      && example.etcFiles."agents/fixture/source.nix" == builtins.readFile ./agents-software.nix;
    stringContexts = lib.all (text: builtins.getContext text == builtins.getContext context)
      [ example.environment.FIXTURE example.etcFiles."agents/fixture/context" example.homeInit ];
    invalidResources = lib.all (resources: rejects (factory (args // { inherit resources; }))) [
      null [] { "agents/x" = {}; } { "agents/x" = { text = null; }; }
      { "agents/x" = { source = "/string/path"; }; }
      { "agents/x" = { text = "fixture"; source = ./.; }; }
      { "agents/x" = { text = "fixture"; mode = "0600"; }; }
    ];
    invalidNames = lib.all (name:
      rejects (factory (args // { resources.${name} = { text = "fixture"; }; }))
    ) [ "" "agents" "/agents/x" "agents/" "agents//x" "agents/./x" "agents/../x"
      "other/x" "agents/a b" "agents/$(false)" "agents/a\"b" "agents/a\nb" ];
    overlappingFiles = rejects (factory (args // { resources = {
      "agents/a" = { text = "fixture"; }; "agents/a/b" = { text = "nested"; };
    }; }));
    invalidPackages = lib.all (packages: rejects (factory (args // { inherit packages; })))
      [ null [ "not-a-package" ] ];
    invalidEnvironment = lib.all (environment: rejects (factory (args // { inherit environment; })))
      [ [] { BAD = 1; } { "BAD-NAME" = "fixture"; } ];
    invalidHomeInit = rejects (factory (args // { homeInit = null; }));
  };
in {
  inherit result;
  check = assert lib.all (value: value) (builtins.attrValues result);
    pkgs.writeText "agents-software-contracts.json" (builtins.toJSON result);
}
