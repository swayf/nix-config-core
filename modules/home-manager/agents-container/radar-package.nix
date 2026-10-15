# Herdr plugin, not a Pi package: one daemon covers every agent/profile.
# Keep relative scripts and bundled fonts; no npm dependencies at runtime.
# Requires the paired Herdr group_id/radar_status patches; not stock-Herdr compatibility.
# Caller owns runtime Node/Herdr/fontconfig selection and all plugin activation.
{ pkgs }:
let inherit (pkgs) fetchzip stdenvNoCC nodejs; in
stdenvNoCC.mkDerivation {
  pname = "herdr-radar";
  version = "1.3.20-groups";
  src = fetchzip {
    name = "herdr-radar-1.3.20";
    extension = "tar.gz";
    url = "https://codeload.github.com/hhdebb/herdr-radar/tar.gz/2425fa080b7b72f30c1299481282b23003a2ee61";
    hash = "sha256-I/l8q7dSe/7LRXpZrSDlMcVJ+2x8f9LKdQzqqSUmN20=";
  };
  patches = [
    ./radar-package/herdr-radar-agent-groups.patch
    ./radar-package/herdr-radar-status.patch
  ];
  postPatch = ''
    cp ${./radar-package/test-herdr-radar-agent-groups.js} test/agent-groups.test.js
  '';
  nativeBuildInputs = [ nodejs ];
  dontConfigure = true;
  dontBuild = true;
  doCheck = true;
  checkPhase = ''
    runHook preCheck
    export HOME=$(mktemp -d)
    export XDG_CONFIG_HOME="$HOME/config" XDG_STATE_HOME="$HOME/state"
    export XDG_CACHE_HOME="$HOME/cache" XDG_DATA_HOME="$HOME/data"
    export XDG_RUNTIME_DIR="$HOME/run" TMPDIR="$HOME/tmp"
    export HERDR_PLUGIN_STATE_DIR="$HOME/plugin-state"
    export HERDR_PLUGIN_CONFIG_DIR="$HOME/plugin-config" HERDR_RADAR_STATE="$HOME/radar-state"
    export HERDR_SOCKET_PATH="$HOME/unavailable.sock" HERDR_BIN_PATH="$HOME/no-herdr"
    mkdir -p "$TMPDIR" "$XDG_RUNTIME_DIR"
    node --test
    node tools/check.js
    runHook postCheck
  '';
  installPhase = ''
    runHook preInstall
    mkdir -p "$out"
    cp -r . "$out/"
    runHook postInstall
  '';
}
