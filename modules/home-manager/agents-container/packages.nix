# Mechanisms only. Caller owns fetched payload pins, patches, lock and package set.
# No checkout lookup, payload substitution, activation or redistribution approval.
{ pkgs, sources, herdrResources, zcodeLock }:
assert builtins.isAttrs sources && pkgs.lib.all (name: builtins.hasAttr name sources) [
  "plusSrc" "plusManifest" "navigatorSrc" "reviewrSrc" "reviewrBinary"
  "fileViewerSrc" "fileViewerBinary" "annotateSrc" "annotateBinary"
  "annotateDocumentBinary" "browserSrc" "browserPluginSrc" "codeSrc"
  "codeServerSrc" "wikiSrc" "zcodeSrc" "microsandboxSrc" "microsandboxAgentd"
];
assert builtins.isAttrs herdrResources && pkgs.lib.all (name:
  builtins.hasAttr name herdrResources && builtins.isPath herdrResources.${name}
) [ "herdr-pixel-mouse.patch" "herdr-sound-bridge.patch" "herdr-agent-groups.patch"
  "herdr-sidebar-status.patch" "herdr-sound-bridge.rs" "herdr-agent-groups.rs" ];
assert builtins.isPath zcodeLock;
let
  call = name: args: pkgs.callPackage (./packages + "/${name}.nix") args;
  review = call "herdr-review-plugins" {
    payloads = {
      reviewr = { src = sources.reviewrSrc; binary = sources.reviewrBinary; };
      fileViewer = { src = sources.fileViewerSrc; binary = sources.fileViewerBinary; };
      annotate = {
        src = sources.annotateSrc;
        binary = sources.annotateBinary;
        documentBinary = sources.annotateDocumentBinary;
      };
    };
  };
in {
  herdr = call "herdr" { resources = herdrResources; };
  plus = call "herdr-plus" { src = sources.plusSrc; manifest = sources.plusManifest; };
  navigator = call "herdr-navigator" { src = sources.navigatorSrc; };
  inherit (review) reviewr fileViewer annotate;
  browser = call "terminal-browser" { src = sources.browserSrc; pluginSrc = sources.browserPluginSrc; };
  code = call "terminal-code" { src = sources.codeSrc; codeServerSrc = sources.codeServerSrc; };
  wiki = call "obsidian-wiki" { src = sources.wikiSrc; };
  zcode = call "zcode-app-cli" { src = sources.zcodeSrc; packageLock = zcodeLock; };
  microsandbox = call "microsandbox" { src = sources.microsandboxSrc; agentd = sources.microsandboxAgentd; };
}
