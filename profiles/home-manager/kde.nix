# KDE user profile. Plasma/login manager/portals must already exist on the host;
# Home Manager configures the user session, it does not install the desktop OS.
{
  config,
  pkgs,
  ...
}:
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
    yakuake
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
    shortcuts.yakuake.toggle-window-state = "F12";
    configFile = {
      # Yakuake uses Konsole profiles; preserve ddterm's audible BEL.
      yakuakerc."Desktop Entry".DefaultProfile = "HomeManager.profile";
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

  xdg.configFile."autostart/yakuake-home-manager.desktop".text = ''
    [Desktop Entry]
    Type=Application
    Name=Yakuake (Home Manager)
    Exec=${pkgs.kdePackages.yakuake}/bin/yakuake
    OnlyShowIn=KDE;
    Terminal=false
  '';

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
