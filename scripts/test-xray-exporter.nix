{ pkgs, exporter }:
let
  source = pkgs.lib.fileset.toSource {
    root = ../.;
    fileset = ./test-xray-exporter.py;
  };
in
pkgs.runCommand "xray-exporter-smoke"
  {
    nativeBuildInputs = [ pkgs.python3 ];
    XRAY_EXPORTER_COMMAND = "${exporter}/bin/xray-exporter";
  }
  ''
    PYTHONDONTWRITEBYTECODE=1 python3 ${source}/scripts/test-xray-exporter.py
    touch $out
  ''
