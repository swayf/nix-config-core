# Desktop Exec strings contain only store paths and validated profile names;
# user extraArgs are interpreted exclusively by the in-container wrappers.
{
  gcnt,
  browserProfiles,
  commandName ? "gcnt",
  egressLabel ? "US",
  lib,
  pkgs,
}:
let
  # The default instance keeps its existing desktop IDs and labels.
  idPrefix = if commandName == "gcnt" then "gui-container" else commandName;
  label = lib.toUpper commandName;
  icons = {
    firefox = "${pkgs.firefox}/share/icons/hicolor/128x128/apps/firefox.png";
    brave = "${pkgs.brave}/share/icons/hicolor/128x128/apps/brave-browser.png";
    chrome = "${pkgs.google-chrome}/share/icons/hicolor/128x128/apps/google-chrome.png";
  };
  entry = name: command: icon: {
    inherit name icon;
    exec = "${gcnt}/bin/${commandName} ${command}";
    comment = "Container application with ${egressLabel}-only egress";
    terminal = false;
    categories = [ "Network" ];
    startupNotify = false;
  };
in
{
  ${idPrefix} =
    entry "${label} · Terminal (${egressLabel} tunnel)" "exec foot"
      "${pkgs.foot}/share/icons/hicolor/48x48/apps/foot.png";
  "${idPrefix}-firefox" = entry "${label} · Firefox (${egressLabel} tunnel)" "firefox" icons.firefox;
  "${idPrefix}-brave" = entry "${label} · Brave (${egressLabel} tunnel)" "brave" icons.brave;
  "${idPrefix}-chrome" = entry "${label} · Google Chrome (${egressLabel} tunnel)" "chrome" icons.chrome;
  "${idPrefix}-files" =
    (entry "${label} · Files (Thunar)" "exec thunar /shared"
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
  lib.nameValuePair "${idPrefix}-profile-${name}" (
    entry "${label} · ${profile.label}" "exec gcnt-profile-${name}" (
      if profile.icon == null then icons.${profile.browser} else profile.icon
    )
  )
) browserProfiles
