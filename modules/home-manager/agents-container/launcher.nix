# Render only validated technical names; no lifecycle/policy defaults here.
{ lib, workspaceName, gatewayName, projectDirectoryName, shellCommand, updateInput ? null,
  commandName ? "agents", proxyLabel ? "US", hostNixDaemon ? false }:
assert builtins.match "[a-z][a-z0-9-]*" commandName != null;
assert builtins.match "[A-Za-z0-9_-]+" proxyLabel != null;
assert lib.all (name: builtins.match "[A-Za-z0-9_-]+" name != null)
  [ workspaceName gatewayName projectDirectoryName ];
assert builtins.match "[A-Za-z0-9_./-]+" shellCommand != null;
assert updateInput == null || builtins.match "[A-Za-z0-9_-]+" updateInput != null;
let
  # Keep lines between `#@if <mode>` and `#@end` only for the selected mode;
  # marker lines are always dropped. The private-store render therefore stays
  # byte-identical to the launcher before host-daemon mode, and so does its
  # lifecycle hash. Blocks do not nest.
  mode = if hostNixDaemon then "host-daemon" else "private-store";
  modes = [ "host-daemon" "private-store" ];
  selectMode = text:
    let
      step = acc: line:
        if lib.hasPrefix "#@if " line then
          let block = lib.removePrefix "#@if " line; in
          assert acc.block == null && builtins.elem block modes;
          acc // { inherit block; }
        else if line == "#@end" then
          assert acc.block != null;
          acc // { block = null; }
        else if acc.block == null || acc.block == mode then
          acc // { lines = acc.lines ++ [ line ]; }
        else acc;
      result = lib.foldl' step { block = null; lines = []; } (lib.splitString "\n" text);
    in
    assert result.block == null;
    lib.concatStringsSep "\n" result.lines;
in
lib.replaceStrings
  [ "@workspaceName@" "@gatewayName@" "@projectDirectoryName@" "@shellCommand@" "@updateCommand@"
    "@commandName@" "@proxyLabel@" ]
  [ workspaceName gatewayName projectDirectoryName shellCommand
    (if updateInput == null then
      "agents_no_update() { echo 'No update input configured; update the caller lock explicitly.' >&2; return 2; }; agents_no_update"
    else ''nix flake update --flake "path:$flake" ${updateInput}'')
    commandName proxyLabel
  ]
  (selectMode (builtins.readFile ./launcher.sh))
