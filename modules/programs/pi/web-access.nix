# Published extension + locked runtime dependencies. Pi supplies its own peers.
{ buildNpmPackage, fetchurl, nodejs, lib }:
buildNpmPackage rec {
  pname = "pi-web-access";
  version = "0.35.0";
  src = fetchurl {
    url = "https://registry.npmjs.org/pi-web-access/-/pi-web-access-${version}.tgz";
    hash = "sha256-QuSFMyGNA41IPL1ZwyOqLX9QMAtH98ZZVOm5vLQO4J4=";
  };
  sourceRoot = "package";
  npmDepsHash = "sha256-M4mfFSwaMv2Uq/SoLHnGn+APrDIoDzHuB/VrEtXXXT0=";
  npmFlags = [ "--ignore-scripts" "--legacy-peer-deps" ];
  dontNpmBuild = true; # npm release already contains dist/index.js.
  postPatch = ''
    ${nodejs}/bin/node -e 'const fs = require("fs"); const p = JSON.parse(fs.readFileSync("package.json")); delete p.devDependencies; delete p.scripts; fs.writeFileSync("package.json", JSON.stringify(p, null, 2));'
    cp ${./web-access-package-lock.json} package-lock.json
  '';
  installPhase = ''
    runHook preInstall
    mkdir -p "$out"
    cp -R package.json node_modules "$out/"
    # Pi displays the extension entry directory's basename. Avoid "dist".
    cp -R dist "$out/pi-web-access"
    ${nodejs}/bin/node -e 'const fs = require("fs"); const path = process.argv[1]; const p = JSON.parse(fs.readFileSync(path)); p.pi.extensions = ["./pi-web-access"]; fs.writeFileSync(path, JSON.stringify(p, null, 2) + "\n");' "$out/package.json"
    runHook postInstall
  '';
  meta = {
    description = "Web search and content fetching for Pi";
    homepage = "https://github.com/nicobailon/pi-web-access";
    license = lib.licenses.mit;
    platforms = lib.platforms.all;
  };
}
