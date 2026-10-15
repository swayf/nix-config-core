# Upstream's release contains the CLI and matching libkrun firmware. Keep them
# together: the CLI embeds its VMM and dlopens this exact firmware ABI.
{
  src, agentd,
  lib,
  stdenv,
  autoPatchelfHook,
  libcap_ng,
  makeWrapper,
}:
stdenv.mkDerivation {
  pname = "microsandbox";
  version = "0.7.4";
  inherit src;
  inherit agentd;
  sourceRoot = ".";
  nativeBuildInputs = [
    autoPatchelfHook
    makeWrapper
  ];
  buildInputs = [
    libcap_ng
    stdenv.cc.cc.lib
  ];
  dontBuild = true;
  installPhase = ''
    runHook preInstall
    install -Dm755 msb "$out/bin/msb"
    install -Dm755 "$agentd" "$out/libexec/agentd"
    install -Dm755 libkrunfw.so.5.6.1 "$out/lib/libkrunfw.so.5.6.1"
    ln -s libkrunfw.so.5.6.1 "$out/lib/libkrunfw.so.5"
    ln -s libkrunfw.so.5 "$out/lib/libkrunfw.so"
    runHook postInstall
  '';
  postFixup = ''
    wrapProgram "$out/bin/msb" \
      --set MSB_LIBKRUNFW_PATH "$out/lib/libkrunfw.so.5.6.1" \
      --set MSB_AGENTD_PATH "$out/libexec/agentd" \
      --prefix LD_LIBRARY_PATH : "$out/lib"
  '';
  meta = {
    description = "KVM microVM sandbox runtime";
    homepage = "https://github.com/superradcompany/microsandbox";
    license = lib.licenses.asl20;
    platforms = [ "x86_64-linux" ];
    mainProgram = "msb";
  };
}
