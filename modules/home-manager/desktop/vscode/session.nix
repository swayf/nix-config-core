# Nix owns the baseline, not the settings.json files used by a running editor.
{
  config,
  lib,
  pkgs,
  userDataDir,
}:
let
  profiles = config.programs.vscode.profiles;
  settingsPath =
    name:
    "${userDataDir}/User${lib.optionalString (name != "default") "/profiles/${name}"}/settings.json";
  json = pkgs.formats.json { };
  defaults = pkgs.linkFarm "vscode-settings-defaults" (
    lib.mapAttrsToList (name: profile: {
      inherit name;
      # Mirror Home Manager's update-check settings as well as settings contributed
      # by other modules (notably build123d's pycode interpreter/viewer settings).
      path =
        if lib.hm.strings.isPathLike profile.userSettings then
          profile.userSettings
        else
          json.generate "vscode-${name}-settings.json" (
            profile.userSettings
            // lib.optionalAttrs (profile.enableUpdateCheck == false) { "update.mode" = "none"; }
            // lib.optionalAttrs (profile.enableExtensionUpdateCheck == false) {
              "extensions.autoCheckUpdates" = false;
            }
          );
    }) profiles
  );
  arguments = lib.escapeShellArgs [
    # Interpolation copies the script into the store and keeps it in the
    # launcher's closure. toString would leave an untracked source-tree path.
    "${./session.py}"
    "--defaults-dir"
    (toString defaults)
    "--user-data-dir"
    userDataDir
    "--editor"
    (lib.getExe pkgs.vscode)
    "--"
  ];
  package = pkgs.symlinkJoin {
    name = "vscode-session-${pkgs.vscode.version}";
    inherit (pkgs.vscode) version meta passthru;
    paths = [ pkgs.vscode ];
    nativeBuildInputs = [ pkgs.makeWrapper ];
    postBuild = ''
      rm "$out/bin/code"
      makeWrapper ${lib.getExe pkgs.python3} "$out/bin/code" --add-flags ${lib.escapeShellArg arguments}
      # Both desktop entries and their New Window action must use the same
      # launcher, independent of the desktop session's PATH.
      for desktop in "$out"/share/applications/*.desktop; do
        source=$(readlink -f "$desktop")
        rm "$desktop"
        substitute "$source" "$desktop" --replace-fail 'Exec=code ' "Exec=$out/bin/code "
      done
    '';
  };
in
{
  programs.vscode.package = lib.mkDefault package;
  # Keep Home Manager's profile/extension/keybinding management, but exclude
  # settings from its link and cleanup operations. The launcher migrates old
  # store symlinks atomically on the next cold start. A switch during a session
  # does not overwrite the user's in-session settings.
  home.file = lib.mapAttrs' (
    name: _:
    lib.nameValuePair (settingsPath name) {
      enable = lib.mkForce false;
    }
  ) profiles;
}
