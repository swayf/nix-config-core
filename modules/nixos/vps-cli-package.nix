{ lib, callPackage, writeShellApplication, python3, nix, git, openssh, sshpass, openssl, sops, age, ssh-to-age, wgcf, curl, rendererSource ? ./xray-render.py, sourcePolicy, coreRevision, coreNarHash }:
let
  installerTools = callPackage ./vps-installer-tools.nix { };
  # Ubuntu has no Nix loader/store: ship a pure-Go, static Linux binary over SSH.
  portableWgcf = wgcf.overrideAttrs (old: {
    env = (old.env or { }) // { CGO_ENABLED = "0"; };
  });
  source = builtins.path {
    path = ../../scripts;
    name = "vps-tools";
    filter = path: type: type == "directory" || builtins.elem (baseNameOf path) [
      "vps.py" "vps-vless-ops.py" "vps-probe.py" "vps-warp.py" "vps-credentials.py" "vps-wgcf.py"
      "vps-inputs.py" "vps-state.py" "deployment-lib.py" "vps-policy.py"
    ];
  };
in writeShellApplication {
  name = "vps-vless";
  runtimeInputs = [ python3 nix git openssh sshpass openssl sops age ssh-to-age portableWgcf curl ];
  text = ''
    export VPS_WGCF_BINARY="${portableWgcf}/bin/wgcf"
    export VPS_INSTALLER_TOOLS="${installerTools}"
    export VPS_XRAY_RENDERER="${rendererSource}"
    export VPS_CLI_POLICY=${sourcePolicy}
    export VPS_CORE_REV=${lib.escapeShellArg coreRevision}
    export VPS_CORE_NAR_HASH=${lib.escapeShellArg coreNarHash}
    exec python3 ${source}/vps.py "$@"
  '';
  meta.description = "Native NixOS VLESS/REALITY + WARP VPS provisioning and operations";
}
