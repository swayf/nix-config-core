# Installation mechanism only: all extension code, prompts and seeds are caller-owned.
{ config, lib, pkgs, ... }:
let
  cfg = config.programs.piWorkspace;
  resources = cfg.resources;
  extensions = resources.extensions;
  installers = import ../programs/pi/installers.nix;
  packages = import ../programs/pi/packages.nix { inherit pkgs; };
  pathOption = name: lib.mkOption {
    type = lib.types.nullOr lib.types.path;
    default = null;
    description = "Caller-owned ${name} resource. Null omits this resource's installation.";
  };
  afterLinks = lib.hm.dag.entryAfter [ "linkGeneration" ];
  shellInstall = name: source: ''
    run ${pkgs.bash}/bin/bash ${installers.${name}} ${source}
  '';
  # Keep package registration when no routing policy is supplied, with no private lookup.
  webDefaults = if resources.webSearch == null
    then pkgs.writeText "pi-empty-web-search.json" "{}\n"
    else resources.webSearch;
in {
  options.programs.piWorkspace = {
    enable = lib.mkEnableOption "caller-pinned Pi workspace installation";
    resources = {
      packetReviewer = pathOption "packet reviewer rubric";
      modelPresets = pathOption "model presets seed";
      webSearch = pathOption "web routing defaults (null uses an empty overlay)";
      brainstormingSkill = pathOption "brainstorming skill";
      extensions = lib.genAttrs [
        "bell" "statusline" "compactPrompts" "compactTools" "modelPresets"
        "lazyCapabilities" "wikiAsk" "contextHandoff" "codexLimits" "clear"
      ] pathOption;
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [ {
      assertion = (resources.modelPresets == null) == (extensions.modelPresets == null);
      message = "Pi model presets require both the caller extension and its seed, or neither.";
    } ];

    home.activation = {
      piSharedModels = afterLinks ''
        run ${pkgs.python3}/bin/python3 ${installers.sharedModels}
      '';
      piAskUserQuestion = afterLinks (shellInstall "askUserQuestion" packages.askUserQuestion);
      # Before old link removal, after the HM write boundary.
      piSubagents = lib.hm.dag.entryBetween [ "linkGeneration" ] [ "writeBoundary" ] ''
        run ${pkgs.python3}/bin/python3 ${installers.subagents} ${packages.subagents}
      '';
      piContextUsage = afterLinks (shellInstall "contextUsage" packages.contextUsage);
      piWebAccess = lib.hm.dag.entryAfter [ "piSharedModels" ] ''
        run ${pkgs.python3}/bin/python3 ${installers.webAccess} ${packages.webAccess} ${webDefaults}
      '';
    }
    // lib.optionalAttrs (resources.packetReviewer != null) {
      piPacketReviewer = lib.hm.dag.entryAfter [ "piSubagents" ]
        (shellInstall "packetReviewer" resources.packetReviewer);
    }
    // lib.optionalAttrs (extensions.bell != null) {
      piBell = afterLinks ''
        run ${pkgs.bash}/bin/bash ${installers.bell} ${extensions.bell}
        run ${pkgs.python3}/bin/python3 ${installers.disableBell}
      '';
    }
    // lib.optionalAttrs (extensions.statusline != null) {
      piStatusline = afterLinks (shellInstall "statusline" extensions.statusline);
    }
    // lib.optionalAttrs (extensions.compactPrompts != null) {
      piCompactPrompts = afterLinks (shellInstall "compactPrompts" extensions.compactPrompts);
    }
    // lib.optionalAttrs (extensions.compactTools != null) {
      piCompactTools = afterLinks (shellInstall "compactTools" extensions.compactTools);
    }
    // lib.optionalAttrs (resources.modelPresets != null && extensions.modelPresets != null) {
      piModelPresets = afterLinks ''
        run ${pkgs.bash}/bin/bash ${installers.modelPresets} ${extensions.modelPresets} ${resources.modelPresets}
      '';
    }
    // lib.optionalAttrs (resources.brainstormingSkill != null) {
      piBrainstorming = afterLinks (shellInstall "brainstorming" resources.brainstormingSkill);
    }
    // lib.optionalAttrs (extensions.lazyCapabilities != null) {
      piLazyCapabilities = afterLinks ''
        run ${pkgs.bash}/bin/bash ${installers.lazyCapabilities} ${extensions.lazyCapabilities} ${pkgs.python3}/bin/python3
      '';
    }
    // lib.optionalAttrs (extensions.wikiAsk != null) {
      piWikiAsk = afterLinks (shellInstall "wikiAsk" extensions.wikiAsk);
    }
    // lib.optionalAttrs (extensions.contextHandoff != null) {
      piContextHandoff = afterLinks (shellInstall "contextHandoff" extensions.contextHandoff);
    };

    home.file = lib.listToAttrs (lib.concatMap (agentDir:
      lib.concatMap (name: lib.optional (extensions.${name} != null) {
        name = "${agentDir}/extensions/${if name == "codexLimits" then "codex-limits" else name}.ts";
        value.source = extensions.${name};
      }) [ "codexLimits" "clear" ]
    ) [ ".pi/agent" ".pi/profiles/general" ".pi/profiles/code" ".pi/profiles/wiki" ]);
  };
}
