# Nix-patched upstream binary + manifest, linked without Cargo at homeInit.
{ src, lib, stdenv, stdenvNoCC, autoPatchelfHook, python3 }:
stdenvNoCC.mkDerivation rec {
  pname = "herdr-navigator";
  version = "0.3.3";
  inherit src;
  nativeBuildInputs = [ autoPatchelfHook python3 ];
  buildInputs = [ stdenv.cc.cc.lib ];
  dontBuild = true;
  installPhase = ''
    runHook preInstall
    install -Dm755 herdr-navigator "$out/bin/herdr-navigator"
    mkdir -p "$out/share/herdr-plugins/herdr-navigator"
    python3 - "$out" <<'PY'
import pathlib, re, sys, tomllib
out = pathlib.Path(sys.argv[1])
text = pathlib.Path('herdr-plugin.toml').read_text()
text = re.sub(r'(?ms)^\[\[build\]\].*?(?=^\[\[|\Z)', "", text)
text = text.replace('"./target/release/herdr-navigator"', '"' + str(out / 'bin/herdr-navigator') + '"')
tomllib.loads(text)
(out / 'share/herdr-plugins/herdr-navigator/herdr-plugin.toml').write_text(text)
PY
    runHook postInstall
  '';
  meta = {
    description = "Unified fuzzy navigation for Herdr and Herdr Plus";
    homepage = "https://github.com/thanhdat77/herdr-navigator";
    license = lib.licenses.mit;
    platforms = [ "x86_64-linux" ];
    mainProgram = "herdr-navigator";
  };
}
