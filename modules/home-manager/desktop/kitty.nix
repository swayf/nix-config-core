# Shared Kitty configuration for GNOME and KDE.
# On GNOME, Home Manager owns the terminal, GNOME owns Quake Terminal.
# Install/configure the extension manually; do not manage enabled-extensions
# or its dconf keys here. The caller owns the host Quake Terminal setup.
# GNOME Wayland has no wlr-layer-shell, so quickAccessTerminalConfig is unused.
{ pkgs, ... }:
{
  programs.kitty = {
    enable = true;

    font = {
      name = "JetBrains Mono";
      size = 10;
    };

    settings = {
      # Use the Home Manager shell even if the host's login shell is different.
      shell = "${pkgs.zsh}/bin/zsh";
      scrollback_lines = 10000;
      enable_audio_bell = true;

      # Copy clicked URLs instead of opening a browser; clip supports Wayland/X11.
      open_url_with = "${pkgs.writeShellScript "kitty-copy-url" ''
        printf '%s' "$1" | clip copy
      ''}";

      # Keep the tab strip visible, including when only one tab is open.
      tab_bar_edge = "bottom";
      tab_bar_style = "powerline";
      tab_bar_min_tabs = 1;
    };

    keybindings = {
      # Overrides the shell's Ctrl+T; each new tab inherits the current cwd.
      "ctrl+t" = "new_tab_with_cwd";
      # Leave Ctrl+V to Pi/other agents for clipboard image paste.
      # Kitty's normal text paste remains on Ctrl+Shift+V.
    };

    # fonts.nix already installs both families. Map Nerd Font/Powerline icons
    # explicitly: Kitty does not use fontconfig's generic monospace fallback.
    extraConfig = ''
      # Copy links with Ctrl+Alt+click, including when Pi captures the mouse.
      # Swallow the press so the application does not also react to the click.
      mouse_map ctrl+alt+left press grabbed discard_event
      mouse_map ctrl+alt+left release grabbed,ungrabbed mouse_handle_click link

      symbol_map U+E000-U+F8FF,U+F0000-U+FFFFD,U+100000-U+10FFFD Symbols Nerd Font Mono
    '';
  };
}
