# Terminal Code vendors pixel (a modified Electron); stock Electron cannot render it.
# Keep the backend version/hash aligned with dist/codeserver/vendored.js upstream.
{
  src, codeServerSrc,
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
  curl,
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
  libxkbfile,
  libsecret,
  libkrb5,
  libsoup_3,
  mesa,
  nspr,
  nss,
  openssl,
  pango,
  systemd,
  vulkan-loader,
  webkitgtk_4_1,
}:
let
  codeServer = stdenv.mkDerivation {
    pname = "terminal-code-server";
    version = "4.132.0";
    src = codeServerSrc;
    nativeBuildInputs = [ autoPatchelfHook makeWrapper ];
    # Includes the native Microsoft authentication extension's dependencies.
    buildInputs = [
      stdenv.cc.cc.lib libsecret libkrb5 libx11 libxkbfile
      dbus curl openssl webkitgtk_4_1 gtk3 libsoup_3
    ];
    dontBuild = true;
    dontStrip = true;
    installPhase = ''
      runHook preInstall
      mkdir -p "$out/libexec/code-server" "$out/bin"
      cp -a . "$out/libexec/code-server/"
      makeWrapper "$out/libexec/code-server/bin/code-server" "$out/bin/code-server"
      runHook postInstall
    '';
    meta = {
      license = lib.licenses.mit;
      platforms = [ "x86_64-linux" ];
      sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
    };
  };
in
stdenv.mkDerivation {
  pname = "terminal-code";
  version = "0.4.2";
  inherit src;
  nativeBuildInputs = [ autoPatchelfHook makeWrapper ];
  buildInputs = [
    alsa-lib at-spi2-atk at-spi2-core cairo cups dbus expat glib gtk3
    libgbm libGL libx11 libxcb libxcomposite libxdamage libxext libxfixes
    libxrandr libxkbcommon nspr nss pango systemd stdenv.cc.cc.lib vulkan-loader
  ];
  runtimeDependencies = [ libGL vulkan-loader (lib.getLib systemd) ];
  dontBuild = true;
  dontStrip = true;

  postPatch = ''
    # Pi's clipboard bridge uses a sentinel DISPLAY, not an X server. As with
    # terminal-browser, headless ANGLE needs Vulkan instead of the X11 GL path.
    # tode bypasses Pixel's CLI when it spawns the window daemon, so that
    # launch path needs the same flags (before Electron initializes Ozone).
    substituteInPlace dist/launch.js \
      --replace-fail 'const child = (0, node_child_process_1.spawn)((0, launcher_1.electronBinary)(), [(0, launcher_1.bootstrapEntry)(), (0, launcher_1.daemonEntry)()], {' \
        'const chromiumArgs = process.platform === "linux" && (!env.DISPLAY || env.DISPLAY === ":agents-clipboard") && !env.WAYLAND_DISPLAY ? ["--ozone-platform=headless", "--use-angle=vulkan", "--screen-info={8192x8192}"] : [];
    const child = (0, node_child_process_1.spawn)((0, launcher_1.electronBinary)(), [(0, launcher_1.bootstrapEntry)(), (0, launcher_1.daemonEntry)(), ...chromiumArgs], {'
    # PID 1 in the container may leave a killed daemon as a zombie. kill(pid, 0)
    # still succeeds for it, so Pixel otherwise joins its abandoned tty socket.
    substituteInPlace node_modules/@zenbu-labs/pixel/dist/instances.js \
      --replace-fail 'process.kill(pid, 0);' 'process.kill(pid, 0);
        if (process.platform === "linux") {
            const stat = node_fs_1.default.readFileSync("/proc/" + pid + "/stat", "utf8");
            const state = stat.slice(stat.lastIndexOf(")") + 2).split(" ")[0];
            if (state === "Z" || state === "X") return false;
        }'
    substituteInPlace node_modules/@zenbu-labs/pixel/dist/bin.js \
      --replace-fail '!process.env.DISPLAY && !process.env.WAYLAND_DISPLAY' \
        '(!process.env.DISPLAY || process.env.DISPLAY === ":agents-clipboard") && !process.env.WAYLAND_DISPLAY' \
      --replace-fail 'chromiumArgs.push("--ozone-platform=headless", "--screen-info={8192x8192}");' \
        'chromiumArgs.push("--ozone-platform=headless", "--use-angle=vulkan", "--screen-info={8192x8192}");'
  '';

  installPhase = ''
    runHook preInstall
    app="$out/libexec/tode"
    mkdir -p "$app" "$out/bin"
    cp -a . "$app/"
    makeWrapper "$app/bin/tode" "$out/bin/tode" \
      --set TODE_INSTALL_ROOT "$app" \
      --set TODE_CODE_SERVER "${codeServer}/bin/code-server" \
      --set-default FONTCONFIG_FILE ${makeFontsConf { fontDirectories = [ dejavu_fonts noto-fonts-color-emoji ]; }} \
      --prefix XDG_DATA_DIRS : ${mesa}/share
    runHook postInstall
  '';

  passthru = { inherit codeServer; };
  meta = {
    description = "VS Code inside a Kitty-graphics terminal";
    homepage = "https://github.com/zenbu-labs/terminal-code";
    license = lib.licenses.mit;
    platforms = [ "x86_64-linux" ];
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
    mainProgram = "tode";
  };
}
