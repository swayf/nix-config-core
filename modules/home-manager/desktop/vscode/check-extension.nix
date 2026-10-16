# Check only the selected extension, not the whole marketplace catalogue.
# Support exact and caret semver triplets; fail closed on other range syntax
# rather than guessing. No VSIX download or upstream private API is needed.
{ lib, vscodeVersion }:
extension:
let
  id = "${extension.vscodeExtPublisher}.${extension.vscodeExtName}";
  engine = extension.passthru.engineVersion or null;
  triplet = "(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)";
  editor = builtins.match triplet vscodeVersion;
  caret = builtins.isString engine && lib.hasPrefix "^" engine;
  minimum = if caret then lib.removePrefix "^" engine else engine;
  required = if builtins.isString minimum then builtins.match triplet minimum else null;

  # ^1.2.3 allows <2.0.0, ^0.2.3 allows <0.3.0, ^0.0.3 allows <0.0.4.
  sameCompatibleSeries =
    builtins.elemAt editor 0 == builtins.elemAt required 0
    && (
      builtins.elemAt required 0 != "0"
      || (
        builtins.elemAt editor 1 == builtins.elemAt required 1
        && (builtins.elemAt required 1 != "0" || builtins.elemAt editor 2 == builtins.elemAt required 2)
      )
    );
  compatible =
    if caret then
      sameCompatibleSeries && lib.versionAtLeast vscodeVersion minimum
    else
      vscodeVersion == minimum;
  requirement = if builtins.isString engine then "'${engine}'" else "<missing or non-string>";
in
if editor == null then
  throw "VS Code extension ${id}: unsupported editor version '${vscodeVersion}'."
else if required == null then
  throw "VS Code extension ${id}: unsupported engines.vscode ${requirement} (editor ${vscodeVersion}); expected an exact or caret semver triplet."
else if !compatible then
  throw "VS Code extension ${id} requires engines.vscode ${requirement}, but the editor is ${vscodeVersion}. Update nixpkgs or pin a compatible extension version."
else
  extension
