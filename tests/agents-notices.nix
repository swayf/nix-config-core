{ pkgs }:
let
  helpers = pkgs.lib.fileset.toSource {
    root = ../modules/home-manager/agents-container;
    fileset = pkgs.lib.fileset.unions [
      ../modules/home-manager/agents-container/notice-bundle.py
      ../modules/home-manager/agents-container/test-notice-bundle.py
    ];
  };
in builtins.derivation {
  name = "agents-notice-transport-contracts";
  system = pkgs.stdenv.hostPlatform.system;
  builder = "${pkgs.python3}/bin/python3";
  PYTHONDONTWRITEBYTECODE = "1";
  args = [ "-c" ''
    import os, pathlib, subprocess
    subprocess.run(["${pkgs.python3}/bin/python3", "${helpers}/test-notice-bundle.py"], check=True)
    pathlib.Path(os.environ["out"]).touch()
  '' ];
}
