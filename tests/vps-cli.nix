{ pkgs }:
let
  source = pkgs.lib.fileset.toSource {
    root = ../.;
    fileset = pkgs.lib.fileset.unions [
      ../scripts/vps.py
      ../scripts/vps-vless-ops.py
      ../scripts/vps-probe.py
      ../scripts/vps-warp.py
      ../scripts/vps-wgcf.py
      ../scripts/vps-credentials.py
      ../scripts/vps-inputs.py
      ../scripts/vps-state.py
      ../scripts/vps-policy.py
      ../scripts/deployment-lib.py
      ../scripts/test-vps.py
      ../scripts/test-vps-vless-ops.py
      ../scripts/test-vps-warp.py
      ../scripts/vps-test-inputs.py
      ../modules/nixos/xray-render.py
      ./vps-cli-policy.json
      ./test-vps-cli-boundary.py
    ];
  };
in pkgs.runCommand "vps-cli-safety-tests" {
  nativeBuildInputs = [ pkgs.python3 pkgs.openssh pkgs.sops pkgs.age pkgs.openssl ];
} ''
  export PYTHONDONTWRITEBYTECODE=1
  export VPS_CORE_REV=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
  export VPS_CORE_NAR_HASH=sha256-synthetic-fixture
  export VPS_CLI_POLICY=${source}/tests/vps-cli-policy.json
  export VPS_XRAY_RENDERER=${source}/modules/nixos/xray-render.py
  export XDG_STATE_HOME="$TMPDIR/state"
  python3 ${source}/scripts/test-vps.py > safety.log 2>&1 || { tail -60 safety.log; exit 1; }
  python3 ${source}/scripts/test-vps-vless-ops.py
  python3 ${source}/scripts/test-vps-warp.py
  python3 ${source}/tests/test-vps-cli-boundary.py
  touch $out
''
