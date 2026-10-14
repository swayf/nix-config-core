# Render only validated technical names; no lifecycle/policy defaults here.
{ lib, workspaceName, gatewayName, projectDirectoryName, shellCommand, updateInput ? null }:
assert lib.all (name: builtins.match "[A-Za-z0-9_-]+" name != null)
  [ workspaceName gatewayName projectDirectoryName ];
assert builtins.match "[A-Za-z0-9_./-]+" shellCommand != null;
assert updateInput == null || builtins.match "[A-Za-z0-9_-]+" updateInput != null;
lib.replaceStrings
  [ "@workspaceName@" "@gatewayName@" "@projectDirectoryName@" "@shellCommand@" "@updateCommand@" ]
  [ workspaceName gatewayName projectDirectoryName shellCommand
    (if updateInput == null then
      "agents_no_update() { echo 'No update input configured; update the caller lock explicitly.' >&2; return 2; }; agents_no_update"
    else ''nix flake update --flake "path:$flake" ${updateInput}'')
  ]
  (builtins.readFile ./launcher.sh)
