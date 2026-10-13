{
  description = "Reusable Nix configuration modules";

  inputs.nixpkgs2605.url = "github:NixOS/nixpkgs/release-26.05";

  outputs = { self, nixpkgs2605 }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs2605.legacyPackages.${system};
      renderer = pkgs.callPackage ./modules/nixos/xray-render-package.nix { };
      exporter = pkgs.callPackage ./modules/nixos/xray-exporter-package.nix { };
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
      lib.xrayMonitoringRules = import ./modules/nixos/xray-monitoring-rules.nix;
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
      lib.mkWslChecks = args: import ./tests/wsl.nix (args // {
        module = self.nixosModules.wsl;
      });
      homeManagerModules = {
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
        wsl-dependencies = pkgs.runCommand "wsl-dependency-guards" {
          nativeBuildInputs = [ pkgs.python3 ];
        } ''
          PYTHONDONTWRITEBYTECODE=1 python3 ${self}/tests/test-wsl-dependencies.py
          touch $out
        '';
        home-profile-dependencies = pkgs.runCommand "home-profile-dependency-guards" {
          nativeBuildInputs = [ pkgs.python3 ];
        } ''
          PYTHONDONTWRITEBYTECODE=1 python3 ${self}/tests/test-home-dependencies.py
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
