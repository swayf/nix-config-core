# WSL-only, explicitly invoked Windows integration; no activation side effects.
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.gui-container;
  gcnt = lib.findFirst (
    p: (p.name or "") == "gcnt"
  ) (throw "windows-shortcuts requires gui-container") config.home.packages;
  entries = import ./host-desktop.nix {
    inherit gcnt lib pkgs;
    inherit (cfg) browserProfiles;
  };
  # These Exec strings contain only fixed words and validated profile names.
  commands = lib.mapAttrs (
    _: entry: lib.splitString " " (lib.removePrefix "${gcnt}/bin/gcnt " entry.exec)
  ) entries;
  launch = pkgs.writeShellApplication {
    name = "gcnt-windows-launch";
    runtimeInputs = [ pkgs.coreutils ];
    text = ''
      if [[ $# != 1 ]]; then
        echo 'Usage: gcnt-windows-launch APPLICATION_ID' >&2
        exit 2
      fi
      # wsl.exe --exec does not run PAM or a login shell. Podman needs the
      # systemd user runtime directory, not Windows' inherited environment.
      XDG_RUNTIME_DIR="/run/user/$(id -u)"
      export XDG_RUNTIME_DIR
      case "$1" in
      ${lib.concatStringsSep "\n" (
        lib.mapAttrsToList (id: args: ''
          ${id}) exec ${gcnt}/bin/gcnt ${lib.escapeShellArgs args} ;;
        '') commands
      )}
        *) echo "Unknown GCNT application: $1" >&2; exit 2 ;;
      esac
    '';
  };
  assets =
    pkgs.runCommand "gcnt-windows-shortcut-assets"
      {
        nativeBuildInputs = [ pkgs.imagemagick ];
      }
      ''
        mkdir -p "$out"
        ${lib.concatStringsSep "\n" (
          lib.mapAttrsToList (
            id: entry:
            let
              profileName = lib.removePrefix "gui-container-profile-" id;
              profile = cfg.browserProfiles.${profileName} or null;
              # Host theme names/custom paths need not exist at build time. Windows
              # profile shortcuts therefore use their browser's portable icon.
              icon =
                if profile == null then
                  entry.icon
                else
                  entries.${
                    if profile.browser == "chrome" then "gui-container-chrome" else "gui-container-${profile.browser}"
                  }.icon;
            in
            ''
              magick ${lib.escapeShellArg icon} -define icon:auto-resize=128,64,48,32,16 "$out/${id}.ico"
            ''
          ) entries
        )}
      '';
  manifest = pkgs.writeText "gcnt-windows-shortcuts.json" (
    builtins.toJSON {
      username = config.home.username;
      launcher = "${config.home.profileDirectory}/bin/gcnt-windows-launch";
      applications = lib.mapAttrsToList (id: entry: {
        inherit id;
        inherit (entry) name;
        icon = "${assets}/${id}.ico";
      }) entries;
    }
  );
  shortcuts = pkgs.writeShellApplication {
    name = "gcnt-windows-shortcuts";
    runtimeInputs = [ pkgs.python3 ];
    text = ''
      exec python3 ${./windows-shortcuts.py} ${manifest} "$@"
    '';
  };
in
{
  config = lib.mkIf cfg.enable {
    home.packages = [
      launch
      shortcuts
    ];
  };
}
