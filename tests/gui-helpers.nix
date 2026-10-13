{ pkgs }:
let
  lib = pkgs.lib;
  resources = ../modules/home-manager/gui-container;
  source = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions (map (name: resources + "/${name}") [
      "launcher.sh" "session.sh" "exec.sh" "windows-shortcuts.py"
      "test-launcher.py" "test-session.py" "test-windows-shortcuts.py"
    ]);
  };
in pkgs.runCommand "gui-helper-contracts" {
  nativeBuildInputs = [ pkgs.python3 pkgs.bash pkgs.coreutils pkgs.util-linux pkgs.dbus ];
  TEST_DBUS_RUN = "${pkgs.dbus}/bin/dbus-run-session";
  TEST_DBUS_SEND = "${pkgs.dbus}/bin/dbus-send";
  TEST_DBUS_CONFIG = "${pkgs.dbus}/share/dbus-1/session.conf";
  TEST_SESSION = pkgs.writeShellScript "gui-session-test" (builtins.readFile (resources + "/session.sh"));
  TEST_EXEC = pkgs.writeShellScript "gui-exec-test" (builtins.readFile (resources + "/exec.sh"));
} ''
  export PYTHONDONTWRITEBYTECODE=1
  python3 ${source}/modules/home-manager/gui-container/test-launcher.py
  python3 ${source}/modules/home-manager/gui-container/test-session.py
  python3 ${source}/modules/home-manager/gui-container/test-windows-shortcuts.py
  touch $out
''
