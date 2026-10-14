{ pkgs }:
let
  names = [ "ask-user-question" "context-usage" "subagents" "web-access" ];
  resources = ../modules/programs/pi;
  source = pkgs.lib.fileset.toSource {
    root = ../.;
    fileset = pkgs.lib.fileset.unions (map (name: resources + "/${name}") [
      "install-ask-user-question.sh" "install-context-usage.sh"
      "install-subagents.py" "install-web-access.py"
      "tests/test-install-ask-user-question.py" "tests/test-install-context-usage.py"
      "tests/test-install-subagents.py" "tests/test-install-web-access.py"
    ]);
  };
in pkgs.runCommand "pi-installer-contracts" {
  nativeBuildInputs = [ pkgs.python3 pkgs.bash pkgs.coreutils ];
} ''
  export HOME="$TMPDIR/home" XDG_CONFIG_HOME="$TMPDIR/home/.config"
  export PYTHONDONTWRITEBYTECODE=1
  mkdir -p "$HOME"
  ${pkgs.lib.concatMapStringsSep "\n" (name:
    "python3 ${source}/modules/programs/pi/tests/test-install-${name}.py") names}
  touch $out
''
