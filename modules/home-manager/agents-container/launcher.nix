# Render only validated technical names; no lifecycle/policy defaults here.
{ lib, workspaceName, gatewayName, projectDirectoryName, shellCommand, updateInput ? null,
  commandName ? "agents", proxyLabel ? "US" }:
assert builtins.match "[a-z][a-z0-9-]*" commandName != null;
assert builtins.match "[A-Za-z0-9_-]+" proxyLabel != null;
assert lib.all (name: builtins.match "[A-Za-z0-9_-]+" name != null)
  [ workspaceName gatewayName projectDirectoryName ];
assert builtins.match "[A-Za-z0-9_./-]+" shellCommand != null;
assert updateInput == null || builtins.match "[A-Za-z0-9_-]+" updateInput != null;
lib.replaceStrings
  [ "@workspaceName@" "@gatewayName@" "@projectDirectoryName@" "@shellCommand@" "@updateCommand@"
    "@commandName@" "@proxyLabel@" ]
  [ workspaceName gatewayName projectDirectoryName shellCommand
    (if updateInput == null then
      "agents_no_update() { echo 'No update input configured; update the caller lock explicitly.' >&2; return 2; }; agents_no_update"
    else ''nix flake update --flake "path:$flake" ${updateInput}'')
    commandName proxyLabel
  ]
  (builtins.readFile ./launcher.sh)
