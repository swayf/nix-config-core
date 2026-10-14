# Standalone Pi extension with its only required dependency. Pi supplies the
# typebox/pi-* peers; bundling another copy would break host module identity.
{ fetchurl, runCommand, lib, patch }:
let
  version = "2.12.0";
  extension = fetchurl {
    url = "https://registry.npmjs.org/@juicesharp/rpiv-ask-user-question/-/rpiv-ask-user-question-${version}.tgz";
    hash = "sha256-9AeJNjjxiTo3MERPRntO3Bkkzy0gauWE91JeHoroYPY=";
  };
  config = fetchurl {
    url = "https://registry.npmjs.org/@juicesharp/rpiv-config/-/rpiv-config-${version}.tgz";
    hash = "sha256-5ZKpbqxsSPTx7n+pHvh/UOqCVeiH/wSuvHkEpL7Q8dQ=";
  };
in
runCommand "rpiv-ask-user-question-${version}" {
  nativeBuildInputs = [ patch ];
  meta = {
    description = "Structured user questionnaires for Pi";
    homepage = "https://github.com/juicesharp/rpiv-mono/tree/main/packages/rpiv-ask-user-question";
    license = lib.licenses.mit;
    platforms = lib.platforms.all;
  };
} ''
  mkdir -p "$out"
  tar -xzf ${extension} --strip-components=1 -C "$out"
  # Translate questionnaire waits to Herdr Blocked/Working, without terminal BEL.
  patch -d "$out" -p1 < ${./ask-user-question-herdr.patch}
  # Fullscreen mouse collapse/reopen; the hidden questionnaire releases chat input.
  patch -d "$out" -p1 < ${./ask-user-question-mouse.patch}
  mkdir -p "$out/node_modules/@juicesharp/rpiv-config"
  tar -xzf ${config} --strip-components=1 -C "$out/node_modules/@juicesharp/rpiv-config"
''
