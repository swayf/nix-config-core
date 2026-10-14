# Published Pi extension without runtime dependencies. Pi supplies both peers.
{ fetchurl, runCommand, lib }:
let
  version = "2.1.0";
  src = fetchurl {
    url = "https://registry.npmjs.org/pi-context-usage/-/pi-context-usage-${version}.tgz";
    hash = "sha256-ildAsSIQ02FEvH/tsXKHE8sCgMX0lUEF73C/H/UhcVo=";
  };
in
runCommand "pi-context-usage-${version}" {
  meta = {
    description = "Context usage and system prompt breakdown for Pi";
    homepage = "https://github.com/championswimmer/pi-context-usage";
    license = lib.licenses.isc;
    platforms = lib.platforms.all;
  };
} ''
  mkdir -p "$out"
  tar -xzf ${src} --strip-components=1 -C "$out"
''
