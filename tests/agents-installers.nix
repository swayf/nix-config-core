# Source-only mocks; no Herdr/browser/Pi package, private checkout or nested Nix CLI.
{ pkgs }:
let
  exports = (import ../flake.nix).outputs { self = {}; nixpkgs2605 = {}; };
  installers = exports.lib.agentsInstallers;
  source = pkgs.lib.fileset.toSource {
    root = ../modules/home-manager/agents-container;
    fileset = pkgs.lib.fileset.unions [
      installers.claudeProfile
      installers.herdrNavigation
      installers.herdrRadar
      installers.herdrReviewPlugins
      installers.herdrProjects
      installers.piPrompts
      installers.piShell
      installers.terminalBrowser
      installers.wikiInit
      ../modules/home-manager/agents-container/test-install-herdr-navigation.py
      ../modules/home-manager/agents-container/test-install-herdr-radar.py
      ../modules/home-manager/agents-container/test-install-herdr-review-plugins.py
      ../modules/home-manager/agents-container/test-install-herdr-projects.py
      ../modules/home-manager/agents-container/test-install-pi-prompts.py
      ../modules/home-manager/agents-container/test-install-pi-shell.py
      ../modules/home-manager/agents-container/test-install-terminal-browser.py
      ../modules/home-manager/agents-container/test-wiki-init.py
      ../modules/home-manager/agents-container/test-claude-profile.py
    ];
  };
  software = exports.lib.mkAgentsSoftware {
    inherit pkgs;
    packages = [];
    environment = {};
    resources = {
      "agents/claude-profile.py" = { source = installers.claudeProfile; };
      "agents/install-navigation.py" = { source = installers.herdrNavigation; };
      "agents/install-radar.py" = { source = installers.herdrRadar; };
      "agents/install-review-plugins.py" = { source = installers.herdrReviewPlugins; };
      "agents/install-projects.py" = { source = installers.herdrProjects; };
      "agents/install-prompts.py" = { source = installers.piPrompts; };
      "agents/install-pi-shell.py" = { source = installers.piShell; };
      "agents/install-browser.py" = { source = installers.terminalBrowser; };
      "agents/wiki-init.sh" = { source = installers.wikiInit; };
    };
    homeInit = "caller-only; never-run-during-evaluation";
  };
in
assert builtins.attrNames installers == [ "claudeProfile" "herdrNavigation" "herdrProjects" "herdrRadar" "herdrReviewPlugins" "piPrompts" "piShell" "terminalBrowser" "wikiInit" ];
assert builtins.isPath installers.herdrRadar;
assert software.etcFiles."agents/install-radar.py" == builtins.readFile installers.herdrRadar;
assert builtins.isPath installers.herdrReviewPlugins;
assert software.etcFiles."agents/install-review-plugins.py" == builtins.readFile installers.herdrReviewPlugins;
assert builtins.isPath installers.herdrNavigation;
assert software.etcFiles."agents/install-navigation.py" == builtins.readFile installers.herdrNavigation;
assert builtins.isPath installers.claudeProfile && builtins.isPath installers.piShell;
assert software.etcFiles."agents/install-pi-shell.py" == builtins.readFile installers.piShell;
assert software.etcFiles."agents/claude-profile.py" == builtins.readFile installers.claudeProfile;
assert builtins.isPath installers.herdrProjects && builtins.isPath installers.piPrompts
  && builtins.isPath installers.terminalBrowser && builtins.isPath installers.wikiInit;
assert software.etcFiles."agents/install-projects.py" == builtins.readFile installers.herdrProjects;
assert software.etcFiles."agents/install-prompts.py" == builtins.readFile installers.piPrompts;
assert software.etcFiles."agents/install-browser.py" == builtins.readFile installers.terminalBrowser;
assert software.etcFiles."agents/wiki-init.sh" == builtins.readFile installers.wikiInit;
assert software.homeInit == "caller-only; never-run-during-evaluation";
pkgs.runCommand "agents-installers-synthetic" {
  nativeBuildInputs = [ pkgs.python3 pkgs.bash pkgs.coreutils ];
} ''
  export HOME="$TMPDIR/home"
  mkdir -p "$HOME"
  export PYTHONDONTWRITEBYTECODE=1
  python3 ${source}/test-install-herdr-navigation.py
  python3 ${source}/test-install-herdr-radar.py
  python3 ${source}/test-install-herdr-review-plugins.py
  python3 ${source}/test-install-herdr-projects.py
  python3 ${source}/test-install-pi-prompts.py
  python3 ${source}/test-install-pi-shell.py
  python3 ${source}/test-install-terminal-browser.py
  python3 ${source}/test-wiki-init.py
  python3 ${source}/test-claude-profile.py
  touch "$out"
''
