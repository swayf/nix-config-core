# Supplemental bytes only; completeness/linkage/terms remain caller-owned gates.
{ pkgs, package, manifest, texts }:
assert builtins.match "[A-Za-z0-9_+.-]+" package != null && package != "." && package != "..";
# Text-only builder: no shell/compiler environment or network fetch dependency.
builtins.derivation {
  name = "${package}-known-notices";
  system = pkgs.stdenv.hostPlatform.system;
  outputs = [ "out" "source" ];
  builder = "${pkgs.python3}/bin/python3";
  args = [ ./notice-bundle.py "--manifest" manifest "--texts" texts "--package" package ];
}
