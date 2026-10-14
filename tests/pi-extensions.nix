{ pkgs }:
let
  pi = ../modules/programs/pi;
  source = pkgs.lib.fileset.toSource {
    root = ../.;
    fileset = pkgs.lib.fileset.unions (
      [ (pi + "/extensions") ]
      ++ map (name: pi + "/tests/${name}.test.mjs") [
        "bell" "clear" "codex-limits" "compact-prompts" "compact-tools"
        "model-presets" "statusline" "context-handoff" "lazy-capabilities"
      ]
    );
  };
in pkgs.runCommand "pi-extension-contracts" {
  nativeBuildInputs = [ pkgs.nodejs_24 ];
} ''
  export HOME="$TMPDIR/home"
  mkdir -p "$HOME"
  for name in bell clear codex-limits compact-prompts compact-tools model-presets statusline context-handoff lazy-capabilities; do
    node --test ${source}/modules/programs/pi/tests/$name.test.mjs
  done
  node --test ${source}/modules/programs/pi/extensions/wiki-ask/core.test.mjs
  touch $out
''
