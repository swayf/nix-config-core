{ pkgs, exporter }:
let
  source = pkgs.lib.fileset.toSource {
    root = ../.;
    fileset = pkgs.lib.fileset.unions [
      ./test-xray-runtime.py
      ./xray-test-fixture.py
      ../modules/nixos/xray-render.py
    ];
  };
in
pkgs.runCommand "xray-runtime-tests"
  {
    nativeBuildInputs = [
      pkgs.python3
      pkgs.openssl
      pkgs.xray
      pkgs.curl
      exporter
    ];
  }
  ''
    # No inherited proxy, identity or checkout environment; only pinned tools/assets.
    env -i PATH="$PATH" HOME="$TMPDIR" PYTHONDONTWRITEBYTECODE=1 \
      python3 ${source}/scripts/test-xray-runtime.py
    touch $out
  ''
