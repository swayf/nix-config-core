# Desktop Exec strings contain only store paths and validated profile names;
# user extraArgs are interpreted exclusively by the in-container wrappers.
{
  gcnt,
  browserProfiles,
  lib,
  pkgs,
}:
let
  icons = {
    firefox = "${pkgs.firefox}/share/icons/hicolor/128x128/apps/firefox.png";
    brave = "${pkgs.brave}/share/icons/hicolor/128x128/apps/brave-browser.png";
    chrome = "${pkgs.google-chrome}/share/icons/hicolor/128x128/apps/google-chrome.png";
  };
  entry = name: command: icon: {
    inherit name icon;
    exec = "${gcnt}/bin/gcnt ${command}";
    comment = "Container application with US-only egress";
    terminal = false;
    categories = [ "Network" ];
    startupNotify = false;
  };
in
{
  gui-container =
    entry "GCNT · Terminal (US tunnel)" "exec foot"
      "${pkgs.foot}/share/icons/hicolor/48x48/apps/foot.png";
  gui-container-firefox = entry "GCNT · Firefox (US tunnel)" "firefox" icons.firefox;
  gui-container-brave = entry "GCNT · Brave (US tunnel)" "brave" icons.brave;
  gui-container-chrome = entry "GCNT · Google Chrome (US tunnel)" "chrome" icons.chrome;
  gui-container-files =
    (entry "GCNT · Files (Thunar)" "exec thunar /shared"
      "${pkgs.thunar}/share/icons/hicolor/128x128/apps/org.xfce.thunar.png"
    )
    // {
      categories = [
        "System"
        "FileManager"
      ];
    };
}
// lib.mapAttrs' (
  name: profile:
  lib.nameValuePair "gui-container-profile-${name}" (
    entry "GCNT · ${profile.label}" "exec gcnt-profile-${name}" (
      if profile.icon == null then icons.${profile.browser} else profile.icon
    )
  )
) browserProfiles
