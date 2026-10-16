# Synthetic HOME/clipboard/focus tests only. No desktop, OCI image or font payload.
{ pkgs }:
let
  source = pkgs.lib.fileset.toSource {
    root = ../.;
    fileset = pkgs.lib.fileset.unions [
      ./test-vscode-session.py
      ../modules/home-manager/desktop/vscode/session.py
      ../modules/home-manager/desktop/handy
      ../modules/home-manager/desktop/max/clipboard.py
      ../modules/home-manager/desktop/max/test_clipboard.py
      ../modules/home-manager/desktop/max/test_focus_extension.mjs
      ../modules/home-manager/desktop/max/focus-extension
    ];
  };
in pkgs.runCommand "desktop-synthetic-contracts" {
  nativeBuildInputs = [ pkgs.python3 pkgs.nodejs pkgs.bash ];
} ''
  export PYTHONDONTWRITEBYTECODE=1
  python3 ${source}/tests/test-vscode-session.py
  python3 ${source}/modules/home-manager/desktop/handy/test-dotool.py
  python3 ${source}/modules/home-manager/desktop/max/test_clipboard.py
  node ${source}/modules/home-manager/desktop/handy/test-focus-extension.mjs
  node ${source}/modules/home-manager/desktop/max/test_focus_extension.mjs
  touch $out
''
