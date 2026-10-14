# Optional caller-pinned HM contract. No extra core inputs or private source.
{ nixpkgs, homeManager, module }:
let
  pkgs = import nixpkgs { system = "x86_64-linux"; };
  lib = pkgs.lib;
  extension = pkgs.writeText "pi-synthetic-extension.ts" "export default function () {}\n";
  seed = pkgs.writeText "pi-synthetic-presets.json" "[]\n";
  routing = pkgs.writeText "pi-synthetic-routing.json" ''{"searchRouting":{"providers":["fixture-search"]}}'';
  rubric = pkgs.writeText "pi-synthetic-rubric.md" "---\nname: packet-reviewer\n---\nSynthetic fixture.\n";
  directory = pkgs.runCommand "pi-synthetic-resources" { } ''
    mkdir -p $out
    for file in index.ts core.mjs core.test.mjs SKILL.md README.md; do
      printf 'synthetic resource\n' > "$out/$file"
    done
  '';
  resources = {
    packetReviewer = rubric;
    modelPresets = seed;
    webSearch = routing;
    brainstormingSkill = directory;
    extensions = {
      bell = extension;
      statusline = extension;
      compactPrompts = extension;
      compactTools = extension;
      modelPresets = extension;
      codexLimits = extension;
      clear = extension;
      lazyCapabilities = directory;
      wikiAsk = directory;
      contextHandoff = directory;
    };
  };
  make = home: settings: homeManager.lib.homeManagerConfiguration {
    inherit pkgs;
    modules = [ module {
      home = { username = "alice"; homeDirectory = home; stateVersion = "26.05"; };
      programs.piWorkspace = settings;
    } ];
  };
  minimal = make "/srv/alice" { enable = true; };
  full = make "/var/lib/pi-fixture" { enable = true; inherit resources; };
  spaced = make "/var/lib/pi fixture" { enable = true; inherit resources; };
  disabled = make "/srv/alice" { enable = false; inherit resources; };
  incomplete = make "/srv/alice" { enable = true; resources.modelPresets = seed; };
  incompleteExtension = make "/srv/alice" { enable = true; resources.extensions.modelPresets = extension; };
  failures = h: lib.filter (a: !a.assertion) h.config.assertions;
  activation = h: lib.filterAttrs (n: _: lib.hasPrefix "pi" n) h.config.home.activation;
  files = h: lib.filterAttrs (n: _: lib.hasPrefix ".pi/" n) h.config.home.file;
  containsPath = source: lib.hasInfix (builtins.unsafeDiscardStringContext (toString source));
  result = {
    assertions = failures minimal == [ ] && failures full == [ ] && failures disabled == [ ];
    spacedHomeEval = failures spaced == [ ] && builtins.length (builtins.attrNames (activation spaced)) == 15;
    minimal = builtins.attrNames (activation minimal) == [
      "piAskUserQuestion" "piContextUsage" "piSharedModels" "piSubagents" "piWebAccess"
    ] && files minimal == { };
    full = builtins.length (builtins.attrNames (activation full)) == 15
      && builtins.length (builtins.attrNames (files full)) == 8;
    disabled = activation disabled == { } && files disabled == { };
    dag = full.config.home.activation.piSubagents.after == [ "writeBoundary" ]
      && full.config.home.activation.piSubagents.before == [ "linkGeneration" ]
      && full.config.home.activation.piPacketReviewer.after == [ "piSubagents" ]
      && full.config.home.activation.piWebAccess.after == [ "piSharedModels" ];
    missingPolicy = lib.hasInfix "pi-empty-web-search.json" minimal.config.home.activation.piWebAccess.data
      && !containsPath routing minimal.config.home.activation.piWebAccess.data;
    explicitPolicy = containsPath routing full.config.home.activation.piWebAccess.data
      && containsPath rubric full.config.home.activation.piPacketReviewer.data;
    incompletePolicyRejected = !(builtins.tryEval incomplete.activationPackage.drvPath).success
      && !(builtins.tryEval incompleteExtension.activationPackage.drvPath).success;
    generations = lib.hasSuffix ".drv" minimal.activationPackage.drvPath
      && lib.hasSuffix ".drv" full.activationPackage.drvPath;
  };
in {
  inherit result;
  activation = minimal.activationPackage;
  fullActivation = full.activationPackage;
  check = assert lib.all (value: value) (builtins.attrValues result);
    pkgs.writeText "pi-home-contracts.json" (builtins.toJSON result);
}
