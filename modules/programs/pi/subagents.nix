# Published extension and locked runtime dependencies; Pi provides its peers.
{ buildNpmPackage, fetchurl, lib }:
buildNpmPackage rec {
  pname = "pi-subagents";
  version = "0.77.0";
  src = fetchurl {
    url = "https://registry.npmjs.org/pi-subagents/-/pi-subagents-${version}.tgz";
    hash = "sha256-SSJqQUyr7lbzddr6VQHykpvyS6lMSySR4+ZmxbrkTQc=";
  };
  sourceRoot = "package";
  npmDepsHash = "sha256-inCNTNSOUuwh6r/SRvTcGODNx1pWDwvg92LBeLZxx3g=";
  npmFlags = [ "--ignore-scripts" "--legacy-peer-deps" ];
  dontNpmBuild = true; # The release already contains compiled JavaScript.
  postPatch = ''
    cp ${./subagents-package-lock.json} package-lock.json
  '';
  installPhase = ''
    runHook preInstall
    mkdir -p "$out"
    cp -R . "$out/"
    runHook postInstall
  '';
  meta = {
    description = "Subagent delegation and workflows for Pi";
    homepage = "https://github.com/nicobailon/pi-subagents";
    license = lib.licenses.mit;
    platforms = lib.platforms.all;
  };
}
