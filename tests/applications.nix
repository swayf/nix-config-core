{ nixpkgs, homeManager, modules }:
let
  pkgs = import nixpkgs { system = "x86_64-linux"; };
  lib = nixpkgs.lib;
  mk = username: homeDirectory: homeManager.lib.homeManagerConfiguration {
    inherit pkgs;
    modules = [ modules.cli modules.playwright {
      home = { inherit username homeDirectory; stateVersion = "26.05"; };
    } ];
  };
  alice = mk "alice" "/srv/alice";
  bob = mk "bob" "/srv/bob";
  c = alice.config;
  result = {
    callerIdentity = c.home.username == "alice" && c.home.homeDirectory == "/srv/alice"
      && bob.config.home.username == "bob" && bob.config.home.homeDirectory == "/srv/bob";
    tools = lib.all (name: c.programs.${name}.enable)
      [ "bat" "btop" "jq" "fzf" "eza" "zoxide" "atuin" "mise" "direnv" ];
    history = c.programs.fzf.historyWidget.command == ""
      && c.programs.atuin.flags == [ "--disable-up-arrow" ]
      && !c.programs.atuin.settings.update_check;
    packages = builtins.elem pkgs.playwright-test c.home.packages
      && builtins.elem pkgs.kubectx c.home.packages;
    projectBrowsers = !(c.home.sessionVariables ? PLAYWRIGHT_BROWSERS_PATH);
    noDesktop = !c.targets.genericLinux.enable && !c.programs.firefox.enable;
    assertions = lib.all (a: a.assertion) c.assertions;
    generations = lib.hasSuffix ".drv" alice.activationPackage.drvPath
      && lib.hasSuffix ".drv" bob.activationPackage.drvPath;
  };
in {
  inherit result;
  check = assert lib.all (x: x) (builtins.attrValues result);
    pkgs.runCommand "application-contracts" { } ''
      echo ${lib.escapeShellArg (builtins.toJSON result)} > $out
    '';
}
