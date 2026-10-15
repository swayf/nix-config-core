# Upstream bundles a modified Electron (pixel); stock Electron is not a substitute.
# Update the release URL/hash and matching plugin commit together.
{
  src, pluginSrc,
  lib,
  stdenv,
  autoPatchelfHook,
  makeWrapper,
  makeFontsConf,
  dejavu_fonts,
  noto-fonts-color-emoji,
  alsa-lib,
  at-spi2-atk,
  at-spi2-core,
  cairo,
  cups,
  dbus,
  expat,
  glib,
  gtk3,
  libgbm,
  libGL,
  libx11,
  libxcb,
  libxcomposite,
  libxdamage,
  libxext,
  libxfixes,
  libxrandr,
  libxkbcommon,
  mesa,
  nspr,
  nss,
  pango,
  systemd,
  vulkan-loader,
}:
stdenv.mkDerivation {
  pname = "terminal-browser";
  version = "0.13.4";

  inherit src;
  inherit pluginSrc;

  nativeBuildInputs = [
    autoPatchelfHook
    makeWrapper
  ];
  buildInputs = [
    alsa-lib
    at-spi2-atk
    at-spi2-core
    cairo
    cups
    dbus
    expat
    glib
    gtk3
    libgbm
    libGL
    libx11
    libxcb
    libxcomposite
    libxdamage
    libxext
    libxfixes
    libxrandr
    libxkbcommon
    nspr
    nss
    pango
    systemd
    stdenv.cc.cc.lib
    vulkan-loader
  ];
  # Chromium loads graphics libraries dynamically, including in headless mode.
  runtimeDependencies = [
    libGL
    vulkan-loader
    (lib.getLib systemd)
  ];
  dontBuild = true;
  dontStrip = true;

  postPatch = ''
    # The clipboard bridge's DISPLAY is not an X server. ANGLE's default GL
    # backend tries X11 even with headless Ozone; Vulkan uses the render node.
    substituteInPlace cli/dist/main.js \
      --replace-fail '!process.env.DISPLAY && !process.env.WAYLAND_DISPLAY' \
        '(!process.env.DISPLAY || process.env.DISPLAY === ":agents-clipboard") && !process.env.WAYLAND_DISPLAY' \
      --replace-fail 'argv = [...argv, "--ozone-platform=headless", "--screen-info={8192x8192}"];' \
        'argv = [...argv, "--ozone-platform=headless", "--use-angle=vulkan", "--screen-info={8192x8192}"];'
  '';

  installPhase = ''
        runHook preInstall
        app="$out/libexec/terminal-browser"
        mkdir -p "$app" "$out/bin" "$out/share/herdr-plugins/terminal-browser"
        cp -a . "$app/"
        cp "$pluginSrc/herdr-plugin/"{herdr-plugin.toml,open-split.sh} \
          "$out/share/herdr-plugins/terminal-browser/"
        # The browser is already in the image: never run upstream's curl installer.
        substituteInPlace "$out/share/herdr-plugins/terminal-browser/herdr-plugin.toml" \
          --replace-fail '[[build]]
    command = ["bash", "-c", "set -o pipefail; curl -fsSL https://terminal-browser.sh/install | bash"]
    platforms = ["linux", "macos"]

    ' ""
        # Split clients launch $app/bin/terminal-browser directly, bypassing
        # $out/bin. Wrap that entry point too, so every daemon sees fonts/Mesa.
        mv "$app/bin/terminal-browser" "$app/bin/.terminal-browser-unwrapped"
        makeWrapper "$app/bin/.terminal-browser-unwrapped" "$app/bin/terminal-browser" \
          --set-default FONTCONFIG_FILE ${
            makeFontsConf {
              fontDirectories = [
                dejavu_fonts
                noto-fonts-color-emoji
              ];
            }
          } \
          --prefix XDG_DATA_DIRS : ${mesa}/share \
          --set-default TERMINAL_BROWSER_NO_TELEMETRY 1
        ln -s "$app/bin/terminal-browser" "$out/bin/terminal-browser"
        runHook postInstall
  '';

  meta = {
    description = "Chromium browser in a Kitty-graphics terminal, with Herdr integration";
    homepage = "https://github.com/zenbu-labs/terminal-browser";
    license = lib.licenses.mit;
    platforms = [ "x86_64-linux" ];
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
    mainProgram = "terminal-browser";
  };
}
