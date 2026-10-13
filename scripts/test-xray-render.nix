{ pkgs, renderer }:
let
  source = pkgs.lib.fileset.toSource {
    root = ../.;
    fileset = pkgs.lib.fileset.unions [
      ./test-xray-render.py
      ../modules/nixos/xray-render.py
    ];
  };
in
pkgs.runCommand "xray-render-tests"
  {
    nativeBuildInputs = [ pkgs.python3 pkgs.openssl ];
    XRAY_RENDER_COMMAND = "${renderer}/bin/xray-render";
  }
  ''
    PYTHONDONTWRITEBYTECODE=1 python3 ${source}/scripts/test-xray-render.py
    touch $out
  ''
