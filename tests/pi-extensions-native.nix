# Opt-in only: the caller supplies the existing Pi package and package set.
{ pkgs, pi }:
let
  source = pkgs.lib.fileset.toSource {
    root = ../.;
    fileset = pkgs.lib.fileset.unions [
      ../modules/programs/pi/extensions
      ../modules/programs/pi/install-lazy-capabilities.sh
      ../modules/programs/pi/tests/test-context-handoff-pi.py
      ../modules/programs/pi/tests/test-lazy-capabilities-pi.py
      ../modules/programs/pi/tests/test-compact-prompts-tui.py
      ../modules/programs/pi/tests/test-compact-tools-tui.py
      ./test-pi-extension-load.py
    ];
  };
  environment = ''
    export HOME="$TMPDIR/home" PYTHONDONTWRITEBYTECODE=1
    export PI_BIN=${pi}/bin/pi WIKI_ASK_TEST_CLI=${pi}/bin/pi
    export PI_CODING_AGENT_DIR="$HOME/.pi/agent" PI_OFFLINE=1 PI_SKIP_VERSION_CHECK=1 PI_TELEMETRY=0
    mkdir -p "$PI_CODING_AGENT_DIR"
  '';
in {
  native = pkgs.runCommand "pi-extension-native-contracts" {
    nativeBuildInputs = [ pkgs.python3 pkgs.nodejs_24 pkgs.bash.out pkgs.coreutils pi ];
  } ''
    ${environment}
    python3 ${source}/tests/test-pi-extension-load.py --pi "$PI_BIN"
    python3 ${source}/modules/programs/pi/tests/test-context-handoff-pi.py
    python3 ${source}/modules/programs/pi/tests/test-lazy-capabilities-pi.py
    node --test ${source}/modules/programs/pi/extensions/wiki-ask/core.test.mjs
    touch $out
  '';
  tui = pkgs.runCommand "pi-extension-tui-contracts" {
    nativeBuildInputs = [ pkgs.python3 pkgs.coreutils pi ];
  } ''
    ${environment}
    python3 ${source}/modules/programs/pi/tests/test-compact-prompts-tui.py
    python3 ${source}/modules/programs/pi/tests/test-compact-tools-tui.py
    touch $out
  '';
}
