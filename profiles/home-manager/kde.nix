# KDE user profile. Plasma/login manager/portals must already exist on the host;
# Home Manager configures the user session, it does not install the desktop OS.
{
  config,
  lib,
  pkgs,
  ...
}:
let
  kitty = config.programs.kitty;
in
{
  imports = [
    ./desktop-common.nix
  ];

  desktop.environment = "kde";

  # MAX's focus gate is a GNOME Shell extension. Keep isolation fail-closed:
  # sound, GPU, shared folder and notifications survive, automatic clipboard
  # exchange does not until a KWin-aware gate is implemented.
  programs.max-container.clipboard.enable = false;

  home.packages = with pkgs.kdePackages; [
    dolphin
    konsole
    okular
    filelight
    ark
  ];

  programs.plasma = {
    enable = true;
    # Do not wipe unrelated KDE settings or force a panel/theme layout.
    overrideConfig = false;

    # Carry the keyboard setup documented in handy.nix to KDE. Handy's shim
    # reads KDE's live kxkbrc instead of stale GNOME dconf (see handy.nix).
    input.keyboard = {
      layouts = [
        { layout = "us"; }
        { layout = "ru"; }
        { layout = "de"; }
      ];
      options = [
        "grp:caps_toggle"
        "altwin:ctrl_alt_win"
      ];
      switchingPolicy = "global";
    };

    hotkeys.commands.vicinae = {
      name = "Vicinae";
      key = "Ctrl+Space";
      command = "${config.programs.vicinae.package}/bin/vicinae toggle";
    };
    # Yakuake used to own F12. overrideConfig = false keeps an old binding in
    # kglobalshortcutsrc, so release it explicitly ("none").
    shortcuts.yakuake.toggle-window-state = [ ];

    # The dropdown terminal is Kitty's quick-access-terminal: KWin has the
    # wlr-layer-shell it needs (GNOME does not, see desktop/kitty.nix), and the
    # agents rely on Kitty graphics and the Kitty keyboard protocol, which
    # Konsole/Yakuake lack. The caller decides whether Kitty is installed.
    hotkeys.commands.kitty-quick-access = lib.mkIf kitty.enable {
      name = "Kitty dropdown terminal";
      key = "F12";
      command = "${kitty.package}/bin/kitten quick-access-terminal";
    };
  };

  programs.konsole = {
    enable = true;
    defaultProfile = "HomeManager";
    profiles.HomeManager = {
      name = "HomeManager";
      font = {
        name = "JetBrains Mono";
        size = 10;
      };
      extraConfig.TerminalFeatures.BellMode = 0; # System beep, not visual bell.
    };
  };

  # Start hidden at login so the first F12 does not wait for Kitty to start.
  xdg.configFile."autostart/kitty-quick-access-home-manager.desktop" = lib.mkIf kitty.enable {
    text = ''
      [Desktop Entry]
      Type=Application
      Name=Kitty dropdown terminal (Home Manager)
      Exec=${kitty.package}/bin/kitten quick-access-terminal -o start_as_hidden=yes
      OnlyShowIn=KDE;
      Terminal=false
    '';
  };

  # Dolphin handles clipboard text -> new file natively via Ctrl+V, replacing
  # the Nautilus scripts/extension. Do not replace browser defaults from common.
  xdg.mimeApps = {
    enable = true;
    defaultApplications = {
      "inode/directory" = [ "org.kde.dolphin.desktop" ];
      "application/pdf" = [ "org.kde.okular.desktop" ];
    };
  };
}
