{
  description = "Reusable Nix configuration modules";

  inputs.nixpkgs2605.url = "github:NixOS/nixpkgs/release-26.05";

  outputs = { self, nixpkgs2605 }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs2605.legacyPackages.${system};
      renderer = pkgs.callPackage ./modules/nixos/xray-render-package.nix { };
      exporter = pkgs.callPackage ./modules/nixos/xray-exporter-package.nix { };
      # Guards depend on the domains they inspect, not the entire Git tree.
      guardSource = files: pkgs.lib.fileset.toSource {
        root = ./.;
        fileset = pkgs.lib.fileset.unions files;
      };
      guardSources = {
        agents-software = guardSource [
          ./tests/test-agents-software-dependencies.py
          ./modules/home-manager/agents-container/software.nix
          ./flake.nix
        ];
        agents-installers = guardSource [
          ./tests/test-agents-installers-dependencies.py
          ./tests/agents-installers.nix
          ./modules/home-manager/agents-container
          ./modules/home-manager/agents-container.nix
          ./flake.nix
        ];
        agents = guardSource [
          ./tests/test-agents-dependencies.py
          ./modules/home-manager/agents-container
          ./modules/home-manager/agents-container.nix
          ./modules/home-manager/container-gateway.nix
        ];
        pi-extension = guardSource [
          ./tests/test-pi-extension-dependencies.py
          ./tests/pi-extensions.nix
          ./tests/pi-extensions-native.nix
          ./modules/programs/pi/extensions
          ./modules/programs/pi/extensions.nix
          ./modules/programs/pi/wiki-ask.nix
          ./modules/home-manager/pi.nix
          ./flake.nix
        ];
        pi = guardSource [
          ./tests/test-pi-dependencies.py
          ./modules/programs/pi
          ./modules/home-manager/pi.nix
          ./flake.nix
        ];
        gui-proxy = guardSource [
          ./tests/test-gui-proxy-dependencies.py
          ./modules/home-manager/gui-container.nix
          ./modules/home-manager/gui-container
          ./modules/home-manager/mihomo.nix
          ./modules/home-manager/container-gateway.nix
          ./profiles/home-manager/wsl-gui-device.nix
        ];
        wsl = guardSource [
          ./tests/test-wsl-dependencies.py
          ./modules/nixos/wsl.nix
        ];
        home-profile = guardSource [
          ./tests/test-home-dependencies.py
          ./modules/home-manager
          ./profiles/home-manager
          ./modules/programs/pi
          ./tests/fixtures
        ];
      };
      dependencySource = pkgs.lib.fileset.toSource {
        root = ./.;
        fileset = pkgs.lib.fileset.unions [
          ./tests/test-xray-dependencies.py
          ./scripts/test-xray-render.py
          ./scripts/test-xray-render.nix
          ./modules/nixos/xray-render.py
          ./modules/nixos/xray-render-package.nix
        ];
      };
      rulesDependencySource = pkgs.lib.fileset.toSource {
        root = ./.;
        fileset = pkgs.lib.fileset.unions [
          ./tests/test-xray-rules-dependencies.py
          ./scripts/test-xray-rules.nix
          ./scripts/test-xray-rules.json
          ./modules/nixos/xray-monitoring-rules.nix
        ];
      };
      moduleDependencySource = pkgs.lib.fileset.toSource {
        root = ./.;
        fileset = pkgs.lib.fileset.unions [
          ./tests/test-xray-module-dependencies.py
          ./modules/nixos/xray-declarative.nix
          ./modules/nixos/xray-monitoring.nix
          ./modules/nixos/xray-sops.nix
          ./modules/nixos/xray-dashboards/vps.json
          ./profiles/nixos/vps-xray.nix
          ./scripts/xray-warp-probe.py
          ./scripts/xray-vm-fixture.py
          ./scripts/test-xray-monitoring-vm.nix
        ];
      };
      vpsProfileDependencySource = pkgs.lib.fileset.toSource {
        root = ./.;
        fileset = pkgs.lib.fileset.unions [
          ./tests/test-vps-base-dependencies.py
          ./tests/vps-base.nix
          ./tests/sops-stub.nix
          ./profiles/nixos/vps-base.nix
          ./modules/nixos/vps-hardening.nix
          ./modules/nixos/xray-declarative.nix
          ./modules/nixos/xray-monitoring.nix
          ./flake.nix
        ];
      };
      probeSource = pkgs.lib.fileset.toSource {
        root = ./.;
        fileset = pkgs.lib.fileset.unions [
          ./tests/test-xray-warp-probe.py
          ./scripts/xray-warp-probe.py
          ./modules/nixos/xray-render.py
        ];
      };
      runtimeDependencySource = pkgs.lib.fileset.toSource {
        root = ./.;
        fileset = pkgs.lib.fileset.unions [
          ./tests/test-xray-runtime-dependencies.py
          ./scripts/test-xray-runtime.py
          ./scripts/test-xray-runtime.nix
          ./scripts/xray-test-fixture.py
        ];
      };
      exporterDependencySource = pkgs.lib.fileset.toSource {
        root = ./.;
        fileset = pkgs.lib.fileset.unions [
          ./tests/test-xray-exporter-dependencies.py
          ./scripts/test-xray-exporter.py
          ./scripts/test-xray-exporter.nix
          ./modules/nixos/xray-exporter-package.nix
        ];
      };
    in
    {
      # Factories take explicit caller pins/policies; no private flake lookups.
      lib.desktopModules = {
        vscode = import ./modules/home-manager/desktop/vscode.nix;
        build123d = import ./modules/home-manager/desktop/build123d.nix;
        handy = import ./modules/home-manager/desktop/handy.nix;
        max = import ./modules/home-manager/desktop/max.nix;
        msWindowsFonts = import ./modules/home-manager/desktop/ms-windows-fonts.nix;
      };
      lib.mkVpsCli = { pkgs, sourcePolicy, coreRevision, coreNarHash }:
        pkgs.callPackage ./modules/nixos/vps-cli-package.nix {
          inherit sourcePolicy coreRevision coreNarHash;
        };
      # Tool-owned source for explicit source/fixture use; never load code from --repo.
      lib.vpsCliSource = ./scripts;
      lib.mkVpsCliChecks = import ./tests/vps-cli.nix;
      lib.mkDesktopShellTools = import ./modules/home-manager/shell/tools.nix;
      lib.mkDesktopChecks = import ./tests/desktop.nix;
      lib.xrayMonitoringRules = import ./modules/nixos/xray-monitoring-rules.nix;
      lib.mkAgentsSoftware = import ./modules/home-manager/agents-container/software.nix;
      lib.mkAgentsPackages = import ./modules/home-manager/agents-container/packages.nix;
      lib.mkAgentsNoticeBundle = import ./modules/home-manager/agents-container/notice-bundle.nix;
      # Explicit caller package set; paired Herdr patches and activation stay caller-owned.
      lib.mkAgentsRadarPackage = import ./modules/home-manager/agents-container/radar-package.nix;
      # Paths only: callers supply sources/config and explicitly invoke the helpers.
      lib.agentsInstallers = {
        claudeProfile = ./modules/home-manager/agents-container/claude-profile.py;
        herdrNavigation = ./modules/home-manager/agents-container/install-herdr-navigation.py;
        herdrProjects = ./modules/home-manager/agents-container/install-herdr-projects.py;
        herdrRadar = ./modules/home-manager/agents-container/install-herdr-radar.py;
        herdrReviewPlugins = ./modules/home-manager/agents-container/install-herdr-review-plugins.py;
        piPrompts = ./modules/home-manager/agents-container/install-pi-prompts.py;
        piShell = ./modules/home-manager/agents-container/install-pi-shell.py;
        terminalBrowser = ./modules/home-manager/agents-container/install-terminal-browser.py;
        wikiInit = ./modules/home-manager/agents-container/wiki-init.sh;
      };
      lib.mkAgentsImage = import ./modules/home-manager/agents-container/image.nix;
      lib.mkAgentsLauncherText = import ./modules/home-manager/agents-container/launcher.nix;
      lib.agentsRuntimeHelpers = {
        processPreflight = ./modules/home-manager/agents-container/process-preflight.sh;
        secretEnvironment = ./modules/home-manager/agents-container/secret-environment.py;
        clipboard = ./modules/home-manager/agents-container/clipboard.py;
        sound = ./modules/home-manager/agents-container/sound.py;
        soundClient = ./modules/home-manager/agents-container/sound-client.py;
      };
      lib.mkAgentsChecks = args: import ./tests/agents-home.nix (args // {
        module = self.homeManagerModules.agents-container;
      });
      lib.mkPiPackages = import ./modules/programs/pi/packages.nix;
      # Paths only: callers choose when to run these against a writable HOME.
      lib.piInstallers = import ./modules/programs/pi/installers.nix;
      lib.piExtensions = import ./modules/programs/pi/extensions.nix;
      lib.mkPiWikiAsk = import ./modules/programs/pi/wiki-ask.nix;
      lib.mkPiExtensionChecks = import ./tests/pi-extensions-native.nix;
      lib.mkPiChecks = args: import ./tests/pi-home.nix (args // {
        module = self.homeManagerModules.pi;
      });
      # Explicit opt-in: expensive multi-VM acceptance is not a default offline check.
      lib.mkXrayMonitoringTest = { pkgs }: import ./scripts/test-xray-monitoring-vm.nix {
        inherit pkgs;
        modules = self.nixosModules;
      };
      # Caller supplies its existing pins; the core adds no HM/SOPS/desktop inputs.
      lib.mkHomeManagerChecks = args: import ./tests/home-manager.nix (args // {
        modules = self.homeManagerModules;
      });
      lib.mkApplicationChecks = args: import ./tests/applications.nix (args // {
        modules = self.homeManagerModules;
      });
      lib.mkGuiProxyChecks = args: import ./tests/gui-proxy.nix (args // {
        modules = self.homeManagerModules;
      });
      lib.mkWslChecks = args: import ./tests/wsl.nix (args // {
        module = self.nixosModules.wsl;
      });
      homeManagerModules = {
        kitty = ./modules/home-manager/desktop/kitty.nix;
        shell-hm = ./modules/home-manager/shell/hm.nix;
        shell-zsh = ./modules/home-manager/shell/zsh.nix;
        shell-colors = ./modules/home-manager/shell/colors.nix;
        shell-aliases = ./modules/home-manager/shell/aliases.nix;
        agents-container = ./modules/home-manager/agents-container.nix;
        pi = ./modules/home-manager/pi.nix;
        mihomo = ./modules/home-manager/mihomo.nix;
        gui-container = ./modules/home-manager/gui-container.nix;
        gui-windows-shortcuts = ./modules/home-manager/gui-container/windows-shortcuts.nix;
        wsl-gui-device = ./profiles/home-manager/wsl-gui-device.nix;
        cli = ./modules/home-manager/cli.nix;
        playwright = ./modules/home-manager/playwright.nix;
        platform = ./modules/home-manager/platform.nix;
        desktop = ./modules/home-manager/desktop.nix;
        secrets = ./modules/home-manager/secrets.nix;
        secrets-file = ./modules/home-manager/secrets.nix;
        desktop-common = ./profiles/home-manager/desktop-common.nix;
        gnome = ./profiles/home-manager/gnome.nix;
        kde = ./profiles/home-manager/kde.nix;
        wsl-common = ./profiles/home-manager/wsl-common.nix;
        wsl-standalone = ./profiles/home-manager/wsl-standalone.nix;
        wsl-integrated = ./profiles/home-manager/wsl-integrated.nix;
      };
      nixosModules = {
        wsl = ./modules/nixos/wsl.nix;
        vps-hardening = ./modules/nixos/vps-hardening.nix;
        xray-declarative = ./modules/nixos/xray-declarative.nix;
        xray-monitoring = ./modules/nixos/xray-monitoring.nix;
        xray-sops = ./modules/nixos/xray-sops.nix;
        vps-xray = ./profiles/nixos/vps-xray.nix;
        vps-base = ./profiles/nixos/vps-base.nix;
      };
      packages.${system} = {
        xray-render = renderer;
        xray-exporter = exporter;
      };

      checks.${system} = {
        vps-cli = self.lib.mkVpsCliChecks { inherit pkgs; };
        desktop-contracts = self.lib.mkDesktopChecks { inherit pkgs; };
        agents-notices = import ./tests/agents-notices.nix { inherit pkgs; };
        agents-radar-package = import ./tests/agents-radar-package.nix { inherit pkgs; };
        agents-software = (import ./tests/agents-software.nix { inherit pkgs; }).check;
        agents-software-dependencies = pkgs.runCommand "agents-software-dependency-guards" {
          nativeBuildInputs = [ pkgs.python3 ];
        } ''
          PYTHONDONTWRITEBYTECODE=1 python3 ${guardSources.agents-software}/tests/test-agents-software-dependencies.py
          touch $out
        '';
        agents-installers = import ./tests/agents-installers.nix { inherit pkgs; };
        agents-installers-dependencies = pkgs.runCommand "agents-installers-dependency-guards" {
          nativeBuildInputs = [ pkgs.python3 ];
        } ''
          PYTHONDONTWRITEBYTECODE=1 python3 ${guardSources.agents-installers}/tests/test-agents-installers-dependencies.py
          touch $out
        '';
        agents-helpers = import ./tests/agents-helpers.nix { inherit pkgs; };
        agents-dependencies = pkgs.runCommand "agents-dependency-guards" {
          nativeBuildInputs = [ pkgs.python3 ];
        } ''
          PYTHONDONTWRITEBYTECODE=1 python3 ${guardSources.agents}/tests/test-agents-dependencies.py
          touch $out
        '';
        pi-extension-dependencies = pkgs.runCommand "pi-extension-dependency-guards" {
          nativeBuildInputs = [ pkgs.python3 ];
        } ''
          PYTHONDONTWRITEBYTECODE=1 python3 ${guardSources.pi-extension}/tests/test-pi-extension-dependencies.py
          touch $out
        '';
        pi-extensions = import ./tests/pi-extensions.nix { inherit pkgs; };
        pi-workspace-installers = import ./tests/pi-workspace-installers.nix { inherit pkgs; };
        pi-dependencies = pkgs.runCommand "pi-dependency-guards" {
          nativeBuildInputs = [ pkgs.python3 ];
        } ''
          PYTHONDONTWRITEBYTECODE=1 python3 ${guardSources.pi}/tests/test-pi-dependencies.py
          touch $out
        '';
        pi-installers = import ./tests/pi-installers.nix { inherit pkgs; };
        gui-proxy-dependencies = pkgs.runCommand "gui-proxy-dependency-guards" {
          nativeBuildInputs = [ pkgs.python3 ];
        } ''
          PYTHONDONTWRITEBYTECODE=1 python3 ${guardSources.gui-proxy}/tests/test-gui-proxy-dependencies.py
          touch $out
        '';
        gui-helpers = import ./tests/gui-helpers.nix { inherit pkgs; };
        wsl-dependencies = pkgs.runCommand "wsl-dependency-guards" {
          nativeBuildInputs = [ pkgs.python3 ];
        } ''
          PYTHONDONTWRITEBYTECODE=1 python3 ${guardSources.wsl}/tests/test-wsl-dependencies.py
          touch $out
        '';
        home-profile-dependencies = pkgs.runCommand "home-profile-dependency-guards" {
          nativeBuildInputs = [ pkgs.python3 ];
        } ''
          PYTHONDONTWRITEBYTECODE=1 python3 ${guardSources.home-profile}/tests/test-home-dependencies.py
          touch $out
        '';
        vps-base = import ./tests/vps-base.nix {
          nixpkgs = nixpkgs2605;
          modules = self.nixosModules;
        };
        vps-base-dependencies = pkgs.runCommand "vps-base-dependency-guards" {
          nativeBuildInputs = [ pkgs.python3 ];
        } ''
          PYTHONDONTWRITEBYTECODE=1 python3 ${vpsProfileDependencySource}/tests/test-vps-base-dependencies.py
          touch $out
        '';
        xray-modules = import ./tests/xray-modules.nix {
          nixpkgs = nixpkgs2605;
          modules = self.nixosModules;
        };
        xray-module-dependencies = pkgs.runCommand "xray-module-dependency-guards" {
          nativeBuildInputs = [ pkgs.python3 ];
        } ''
          PYTHONDONTWRITEBYTECODE=1 python3 ${moduleDependencySource}/tests/test-xray-module-dependencies.py
          touch $out
        '';
        xray-warp-probe = pkgs.runCommand "xray-warp-probe-mocks" {
          nativeBuildInputs = [ pkgs.python3 ];
        } ''
          PYTHONDONTWRITEBYTECODE=1 python3 ${probeSource}/tests/test-xray-warp-probe.py
          touch $out
        '';
        xray-runtime = import ./scripts/test-xray-runtime.nix { inherit pkgs exporter; };
        xray-runtime-dependencies = pkgs.runCommand "xray-runtime-dependency-guards" {
          nativeBuildInputs = [ pkgs.python3 ];
        } ''
          PYTHONDONTWRITEBYTECODE=1 python3 ${runtimeDependencySource}/tests/test-xray-runtime-dependencies.py
          touch $out
        '';
        xray-exporter-smoke = import ./scripts/test-xray-exporter.nix { inherit pkgs exporter; };
        xray-exporter-dependencies = pkgs.runCommand "xray-exporter-dependency-guards" {
          nativeBuildInputs = [ pkgs.python3 ];
        } ''
          PYTHONDONTWRITEBYTECODE=1 python3 ${exporterDependencySource}/tests/test-xray-exporter-dependencies.py
          touch $out
        '';
        xray-render = import ./scripts/test-xray-render.nix { inherit pkgs renderer; };
        xray-dependencies = pkgs.runCommand "xray-dependency-guards" {
          nativeBuildInputs = [ pkgs.python3 ];
        } ''
          PYTHONDONTWRITEBYTECODE=1 python3 ${dependencySource}/tests/test-xray-dependencies.py
          touch $out
        '';
        xray-monitoring-rules = import ./scripts/test-xray-rules.nix {
          inherit pkgs;
          rulesGenerator = self.lib.xrayMonitoringRules;
        };
        xray-rules-dependencies = pkgs.runCommand "xray-rules-dependency-guards" {
          nativeBuildInputs = [ pkgs.python3 ];
        } ''
          PYTHONDONTWRITEBYTECODE=1 python3 ${rulesDependencySource}/tests/test-xray-rules-dependencies.py
          touch $out
        '';
        vps-hardening = import ./tests/vps-hardening.nix {
          nixpkgs = nixpkgs2605;
          module = self.nixosModules.vps-hardening;
        };
        core-boundaries = pkgs.runCommand "core-boundaries" {
          nativeBuildInputs = [ pkgs.python3 ];
        } ''
          PYTHONDONTWRITEBYTECODE=1 python3 ${self}/tests/test-boundaries.py
          touch $out
        '';
      };
    };
}
