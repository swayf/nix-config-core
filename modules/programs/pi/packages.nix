# Use the caller's package set; this factory selects no Pi, HM or nixpkgs pin.
{ pkgs }:
{
  askUserQuestion = pkgs.callPackage ./ask-user-question.nix { };
  contextUsage = pkgs.callPackage ./context-usage.nix { };
  subagents = pkgs.callPackage ./subagents.nix { };
  webAccess = pkgs.callPackage ./web-access.nix { };
}
