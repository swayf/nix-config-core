# Pure composition only: no recipes, policy defaults or HOME discovery.
{ pkgs, packages, environment, resources, homeInit }:
let
  inherit (pkgs) lib;
  require = condition: message:
    if condition then true else throw "mkAgentsSoftware: ${message}";
  names = builtins.attrNames resources;
  validName = name:
    lib.hasPrefix "agents/" name
    && lib.all (part:
      part != "." && part != ".."
      && builtins.match "[A-Za-z0-9_+.-]+" part != null
    ) (lib.splitString "/" name);
  validResource = resource:
    builtins.isAttrs resource && (
      (builtins.attrNames resource == [ "text" ] && builtins.isString resource.text)
      || (builtins.attrNames resource == [ "source" ] && builtins.isPath resource.source)
    );
  etcFiles = lib.mapAttrs (_: resource:
    if resource ? text then resource.text else builtins.readFile resource.source
  ) resources;
in
assert require (builtins.isList packages && lib.all lib.isDerivation packages)
  "packages must be a caller-selected list of derivations";
assert require (builtins.isAttrs environment && lib.all (name:
  builtins.match "[A-Za-z_][A-Za-z0-9_]*" name != null
  && builtins.isString environment.${name}
) (builtins.attrNames environment)) "environment must map ENV names to strings";
assert require (builtins.isAttrs resources) "resources must be an explicit attribute set";
assert require (lib.all validName names)
  "resource names must be safe relative file names below agents/";
assert require (lib.all (name:
  !lib.any (other: lib.hasPrefix "${name}/" other) names
) names) "resource files must not overlap directories";
assert require (lib.all validResource (builtins.attrValues resources))
  "each resource requires exactly text (string) or source (Nix file path)";
assert require (builtins.isString homeInit) "homeInit must be explicit shell text";
# Force source reads even when only another output field is selected. Missing
# caller files fail; they never trigger a checkout or fallback source search.
assert lib.all builtins.isString (builtins.attrValues etcFiles);
{
  inherit packages environment etcFiles homeInit;
}
