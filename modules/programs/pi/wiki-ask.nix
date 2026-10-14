# Caller-owned fallback only; a project's explicit model still takes precedence.
{ pkgs, defaultModel }:
assert builtins.isString defaultModel && builtins.match "[^[:space:]/]+/[^[:space:]]+" defaultModel != null;
let
  # Interpolate the path so generated entrypoints reference a store source,
  # never the caller's checkout after it is removed.
  source = "${./extensions/wiki-ask}";
  entry = pkgs.writeText "pi-wiki-ask-index.ts" ''
    import { registerWikiAsk } from ${builtins.toJSON (toString (source + "/index.ts"))};
    import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
    export default function (pi: ExtensionAPI) {
      registerWikiAsk(pi, { defaultModel: ${builtins.toJSON defaultModel} });
    }
  '';
in pkgs.linkFarm "pi-wiki-ask" (
  [ { name = "index.ts"; path = entry; } ]
  ++ map (name: { inherit name; path = source + "/${name}"; })
    [ "core.mjs" "core.test.mjs" "SKILL.md" "README.md" ]
)
