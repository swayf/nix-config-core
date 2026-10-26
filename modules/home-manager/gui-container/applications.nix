{
  pkgs,
  lib,
  browserProfiles,
  firefoxPolicies ? { },
}:
let
  firefox = if firefoxPolicies == { } then pkgs.firefox else
    pkgs.firefox.override { extraPolicies = firefoxPolicies; };
  brave = pkgs.writeShellScriptBin "brave" ''
    exec ${pkgs.brave}/bin/brave --ozone-platform=wayland "$@"
  '';
  # Only run at container startup, after the previous container was removed.
  # Chromium's hostname/PID and /tmp socket links cannot survive that boundary.
  chrome = pkgs.writeShellScriptBin "chrome" ''
    exec ${pkgs.google-chrome}/bin/google-chrome-stable --ozone-platform=wayland "$@"
  '';
  resetChromiumLocks = pkgs.writeShellScript "gui-reset-chromium-locks" ''
    set -euo pipefail
    for relative in ${lib.escapeShellArgs (
      [
        ".config/BraveSoftware/Brave-Browser"
        ".config/google-chrome"
      ]
      ++ lib.mapAttrsToList (name: _: ".local/share/gcnt/browser-profiles/${name}") (
        lib.filterAttrs (_: profile: profile.browser != "firefox") browserProfiles
      )
    )}; do
      for name in SingletonLock SingletonCookie SingletonSocket; do
        lock="$HOME/$relative/$name"
        # Unlink only Chromium's symlinks (including dangling ones), not data.
        if [[ -L $lock ]]; then rm -f -- "$lock"; fi
      done
    done
  '';
  profileLaunchers = lib.mapAttrs (
    name: profile:
    let
      preferences = pkgs.writeText "gui-firefox-${name}-user.js" (
        lib.concatStringsSep "\n" (
          lib.mapAttrsToList (
            key: value: "user_pref(${builtins.toJSON key}, ${builtins.toJSON value});"
          ) profile.firefoxSettings
        )
        + "\n"
      );
    in
    pkgs.writeShellScriptBin "gcnt-profile-${name}" ''
      set -euo pipefail
      umask 077
      profile="$HOME/.local/share/gcnt/browser-profiles/${name}"
      mkdir -p "$profile"
      ${
        if profile.browser == "firefox" then
          ''
            # Writable, atomic copy; never let Firefox mutate a store file.
            temporary=$(mktemp "$profile/.user.js.XXXXXXXX")
            trap 'rm -f -- "$temporary"' EXIT
            cp ${preferences} "$temporary"
            mv -f -- "$temporary" "$profile/user.js"
            trap - EXIT
            exec ${firefox}/bin/firefox ${lib.escapeShellArgs profile.extraArgs} \
              --no-remote --profile "$profile" "$@"
          ''
        else
          ''
            exec ${if profile.browser == "chrome" then "${chrome}/bin/chrome" else "${brave}/bin/brave"} ${lib.escapeShellArgs profile.extraArgs} \
              --user-data-dir="$profile" "$@"
          ''
      }
    ''
  ) browserProfiles;
in
{
  inherit firefox brave chrome profileLaunchers resetChromiumLocks;
  packages = [ (lib.hiPrio brave) chrome ] ++ builtins.attrValues profileLaunchers;
}
