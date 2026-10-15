# Pinned upstream release: no Go toolchain or network installer at homeInit.
{ src, manifest, lib, stdenvNoCC, python3 }:
stdenvNoCC.mkDerivation rec {
  pname = "herdr-plus";
  version = "0.1.24";
  inherit src;
  inherit manifest;
  nativeBuildInputs = [ python3 ];
  sourceRoot = ".";
  dontBuild = true;
  installPhase = ''
    runHook preInstall
    install -Dm755 herdr-plus "$out/bin/herdr-plus"
    mkdir -p "$out/share/herdr-plugins/herdr-plus"
    python3 - "$manifest" "$out" <<'PY'
import pathlib, re, sys, tomllib
out = pathlib.Path(sys.argv[2])
text = pathlib.Path(sys.argv[1]).read_text()
# Remove install-time network/build hooks; Herdr only links the ready package.
text = re.sub(r'(?ms)^\[\[build\]\].*?(?=^\[\[|\Z)', "", text)
text = text.replace('"./bin/herdr-plus"', '"' + str(out / 'bin/herdr-plus') + '"')
tomllib.loads(text)
(out / 'share/herdr-plugins/herdr-plus/herdr-plugin.toml').write_text(text)
PY
    runHook postInstall
  '';
  meta = {
    description = "Herdr workspace templates and Quick Actions";
    homepage = "https://github.com/cloudmanic/herdr-plus";
    license = lib.licenses.mit;
    platforms = [ "x86_64-linux" ];
    mainProgram = "herdr-plus";
  };
}
