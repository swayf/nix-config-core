# Pinned Linux binaries and plugin manifests; no downloads/build hooks at homeInit.
{ payloads, lib, stdenvNoCC, stdenv, autoPatchelfHook, python3, makeWrapper, git, glow, delta, bat }:
let
  stripBuildHooks = ''
    python3 - "$plugin/herdr-plugin.toml" <<'PY'
import pathlib, re, sys, tomllib
path = pathlib.Path(sys.argv[1])
text = re.sub(r'(?ms)^\[\[build\]\].*?(?=^\[\[|\Z)', "", path.read_text())
tomllib.loads(text)
path.write_text(text)
PY
  '';
  metaFor = repo: description: {
    inherit description;
    homepage = "https://github.com/${repo}";
    license = lib.licenses.mit;
    platforms = [ "x86_64-linux" ];
  };
in
{
  reviewr = stdenvNoCC.mkDerivation rec {
    pname = "herdr-reviewr";
    version = "0.44.0";
    src = payloads.reviewr.src;
    binary = payloads.reviewr.binary;
    nativeBuildInputs = [ python3 ];
    dontBuild = true;
    installPhase = ''
      runHook preInstall
      plugin="$out/share/herdr-plugins/herdr-reviewr"
      mkdir -p "$out/bin" "$plugin/bin" "$plugin/herdr"
      tar xzf "$binary" -C "$out/bin"
      chmod +x "$out/bin/herdr-reviewr"
      ln -s "$out/bin/herdr-reviewr" "$plugin/bin/herdr-reviewr"
      cp herdr-plugin.toml "$plugin/"
      cp herdr/pane.sh "$plugin/herdr/"
      ${stripBuildHooks}
      runHook postInstall
    '';
    meta = (metaFor "persiyanov/herdr-reviewr" "Review agent diffs and send line comments to the agent") // {
      mainProgram = "herdr-reviewr";
    };
  };

  fileViewer = stdenvNoCC.mkDerivation rec {
    pname = "herdr-file-viewer";
    version = "1.17.0";
    src = payloads.fileViewer.src;
    binary = payloads.fileViewer.binary;
    nativeBuildInputs = [ python3 makeWrapper ];
    dontBuild = true;
    installPhase = ''
      runHook preInstall
      plugin="$out/share/herdr-plugins/herdr-file-viewer"
      mkdir -p "$out/bin" "$plugin/target/release" "$plugin/scripts"
      install -Dm755 "$binary" "$out/libexec/herdr-file-viewer"
      # Keep the real executable's basename intact for Herdr's process detection.
      makeWrapper "$out/libexec/herdr-file-viewer" "$out/bin/herdr-file-viewer" \
        --prefix PATH : ${lib.makeBinPath [ git glow delta bat ]}
      ln -s "$out/bin/herdr-file-viewer" "$plugin/target/release/herdr-file-viewer"
      cp herdr-plugin.toml config.example.toml "$plugin/"
      cp scripts/open-file-viewer{,-tab}.sh "$plugin/scripts/"
      ${stripBuildHooks}
      runHook postInstall
    '';
    meta = (metaFor "smarzban/herdr-file-viewer" "Read-only file tree with diffs, Markdown and pinned previews") // {
      mainProgram = "herdr-file-viewer";
    };
  };

  annotate = stdenvNoCC.mkDerivation {
    pname = "herdr-annotate";
    version = "0.8.0";
    src = payloads.annotate.src;
    binary = payloads.annotate.binary;
    documentBinary = payloads.annotate.documentBinary;
    nativeBuildInputs = [ python3 autoPatchelfHook ];
    buildInputs = [ stdenv.cc.cc.lib ];
    dontBuild = true;
    installPhase = ''
      runHook preInstall
      plugin="$out/share/herdr-plugins/herdr-annotate"
      mkdir -p "$out/bin" "$plugin/scripts"
      install -Dm755 "$binary" "$plugin/bin/herdr-annotate.exe"
      install -Dm755 "$documentBinary" "$plugin/bin/plannotator-tui.exe"
      ln -s "$plugin/bin/herdr-annotate.exe" "$out/bin/herdr-annotate"
      ln -s "$plugin/bin/plannotator-tui.exe" "$out/bin/plannotator-tui"
      cp herdr-plugin.toml herdr-annotate.version plannotator-tui.version "$plugin/"
      cp scripts/plannotator-tui.sh "$plugin/scripts/"
      ${stripBuildHooks}
      runHook postInstall
    '';
    meta = (metaFor "plannotator/herdr-annotate" "Annotate terminal text, documents and agent replies in Herdr") // {
      mainProgram = "herdr-annotate";
    };
  };
}
