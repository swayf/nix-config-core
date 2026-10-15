{ pkgs, src, packageLock }:

pkgs.buildNpmPackage rec {
  pname = "zcode-app-cli";
  version = "3.14.4-30";

  inherit src;

  npmDepsHash = "sha256-ZVwBoRDzyvrBAViopx9seKzD9Dp4yH2Q8WVBx+Ra8pc=";
  dontNpmBuild = true;
  npmFlags = [ "--ignore-scripts" ];

  nativeBuildInputs = [ pkgs.makeWrapper ];

  sourceRoot = "package";

  postPatch = ''
    # The release vendors the ZCode runtime, but not its public npm dependencies.
    # Only install runtime dependencies; upstream's build scripts need Bun/source.
    ${pkgs.nodejs}/bin/node -e 'const fs = require("fs"); const p = JSON.parse(fs.readFileSync("package.json")); delete p.devDependencies; delete p.scripts; fs.writeFileSync("package.json", JSON.stringify(p, null, 2));'
    cp ${packageLock} package-lock.json
  '';

  installPhase = ''
    runHook preInstall

    mkdir -p "$out/lib/zcode-app-cli" "$out/bin"
    cp -R . "$out/lib/zcode-app-cli/"
    chmod +x "$out/lib/zcode-app-cli/bin/zcode.js"
    makeWrapper ${pkgs.nodejs}/bin/node "$out/bin/zcode" \
      --add-flags "$out/lib/zcode-app-cli/bin/zcode.js"

    runHook postInstall
  '';
}
