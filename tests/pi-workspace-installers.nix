{ pkgs }:
let
  resources = ../modules/programs/pi;
  detailed = [ "disable-bell" "install-bell" "install-statusline"
    "install-compact-prompts" "install-compact-tools" "install-context-handoff"
    "install-lazy-capabilities" "install-wiki-ask" ];
  source = pkgs.lib.fileset.toSource {
    root = ../.;
    fileset = pkgs.lib.fileset.unions (map (name: resources + "/${name}") [
      "shared-models.py" "disable-bell.py" "install-web-access.py"
      "install-packet-reviewer.sh" "install-bell.sh" "install-statusline.sh"
      "install-compact-prompts.sh" "install-compact-tools.sh" "install-model-presets.sh"
      "install-brainstorming.sh" "install-lazy-capabilities.sh" "install-wiki-ask.sh"
      "install-context-handoff.sh"
      "tests/test-shared-models.py" "tests/test-workspace-installers.py"
      "extensions/context-handoff" "extensions/lazy-capabilities" "extensions/wiki-ask"
    ] ++ map (name: resources + "/tests/test-${name}.py") detailed);
  };
in pkgs.runCommand "pi-workspace-installer-contracts" {
  nativeBuildInputs = [ pkgs.python3 pkgs.bash pkgs.coreutils pkgs.gnugrep ];
} ''
  export HOME="$TMPDIR/home" PYTHONDONTWRITEBYTECODE=1
  mkdir -p "$HOME"
  python3 ${source}/modules/programs/pi/tests/test-shared-models.py
  python3 ${source}/modules/programs/pi/tests/test-workspace-installers.py
  ${pkgs.lib.concatMapStringsSep "\n" (name:
    "python3 ${source}/modules/programs/pi/tests/test-${name}.py") detailed}
  touch $out
''
