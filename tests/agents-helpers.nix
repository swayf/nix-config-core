{ pkgs }:
let
  resources = ../modules/home-manager/agents-container;
  source = pkgs.lib.fileset.toSource {
    root = ../.;
    fileset = pkgs.lib.fileset.unions (map (name: resources + "/${name}") [
      "image.nix" "launcher.sh" "process-preflight.sh" "secret-environment.py"
      "clipboard.py" "sound.py" "sound-client.py" "test-launcher.py"
      "test-process-preflight.py" "test-secret-environment.py" "test-clipboard.py" "test-sound.py"
    ]);
  };
in pkgs.runCommand "agents-runtime-helper-contracts" {
  nativeBuildInputs = [ pkgs.python3 pkgs.bash pkgs.coreutils pkgs.util-linux ];
} ''
  export HOME="$TMPDIR/home" XDG_RUNTIME_DIR="$TMPDIR/runtime" PYTHONDONTWRITEBYTECODE=1
  mkdir -p "$HOME" "$XDG_RUNTIME_DIR"
  for suite in launcher process-preflight secret-environment clipboard sound; do
    python3 ${source}/modules/home-manager/agents-container/test-$suite.py
  done
  touch $out
''
