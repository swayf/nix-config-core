{ nixpkgs, modules }:
let
  inherit (nixpkgs) lib;
  system = "x86_64-linux";
  pkgs = nixpkgs.legacyPackages.${system};
  settings = {
    serverName = "target.test";
    target = "127.0.0.1:2443";
    port = 2444;
    listen = "127.0.0.1";
    tag = "test-inbound";
    clients = [ "alice" ];
  };
  base = {
    networking.hostName = "fixture-node";
    users.users.alice = { isNormalUser = true; home = "/srv/fixture-home/alice"; };
    system.stateVersion = "26.05";
    boot.loader.grub.enable = false;
    fileSystems."/" = { device = "none"; fsType = "tmpfs"; };
  };
  runtime = {
    services.xray.declarative = { enable = true; inherit settings; materialFile = "/run/fixture/material"; };
    services.xrayMonitoring.enable = true;
  };
  evaluate = extra: (lib.nixosSystem {
    inherit system;
    modules = [ modules.xray-declarative modules.xray-monitoring base ] ++ extra;
  }).config;
  c = evaluate [ runtime ];
  profile = (lib.nixosSystem {
    inherit system;
    modules = [ modules.vps-xray base runtime { services.openssh = { enable = true; ports = [ 2222 ]; }; } ];
  }).config;
  warpSettings = settings // { outbounds = [
    { tag = "direct"; protocol = "freedom"; }
    { tag = "warp"; protocol = "wireguard"; }
  ]; };
  w = evaluate [ runtime { services.xray.declarative = {
    settings = lib.mkForce warpSettings;
    warp = { enable = true; materialFile = "/run/fixture/warp"; };
  }; } ];
  t = evaluate [ runtime { services.xrayMonitoring.telegram = {
    enable = true; chatId = -1; botTokenFile = "/run/fixture/token";
  }; } ];
  passed = configuration: lib.all (a: a.assertion) configuration.assertions;
  rejected = extra: !(passed (evaluate [ runtime extra ]));
  secretFile = builtins.toFile "synthetic-xray-secrets.yaml" "synthetic test contract; no credentials\n";
  sops = extra: (lib.nixosSystem {
    inherit system;
    specialArgs.sopsModule = ./sops-stub.nix;
    modules = [ modules.xray-sops base {
      services.xray.declarative = { enable = true; inherit settings secretFile; };
      services.xrayMonitoring = { enable = true; telegram = { enable = true; chatId = -1; inherit secretFile; }; };
    } ] ++ extra;
  }).config;
  s = sops [ ];
  sw = sops [{ services.xray.declarative = { settings = lib.mkForce warpSettings; warp.enable = true; }; }];
  sr = sops [{ services.xray.declarative = {
    settings = lib.mkForce warpSettings;
    warp = { enable = true; useSops = false; materialFile = "/run/fixture/separate-warp"; };
  }; }];
  checks = {
    runtimeAssertions = passed c && passed w && passed t;
    partialProfile = passed profile && profile.services.fail2ban.enable
      && profile.services.xray.declarative.enable && profile.services.xrayMonitoring.enable
      && profile.services.openssh.ports == [ 2222 ] && profile.users.users.alice.home == "/srv/fixture-home/alice";
    callerIdentity = c.users.users.alice.home == "/srv/fixture-home/alice"
      && c.networking.hostName == "fixture-node" && c.system.stateVersion == "26.05";
    runtimeCredentials = c.systemd.services.xray-config.serviceConfig.LoadCredential == [ "material:/run/fixture/material" ]
      && c.systemd.services.xray-config.serviceConfig.RuntimeDirectoryMode == "0700"
      && c.systemd.services.xray-config.serviceConfig.UMask == "0077"
      && c.services.xray.settingsFile == "/run/xray-config/config.json"
      && lib.elem "xray-config.service" c.systemd.services.xray.requires;
    firewall = c.networking.firewall.allowedTCPPorts == [ settings.port ];
    runtimeNoSops = !(c ? sops) && !(lib.elem "sops-install-secrets.service" c.systemd.services.xray-config.requires);
    warpCredentials = w.systemd.services.xray-config.serviceConfig.LoadCredential == [ "material:/run/fixture/material" "warp:/run/fixture/warp" ]
      && lib.any (p: (p.name or "") == "xray-warp-probe") w.environment.systemPackages;
    isolatedExporter = !(lib.elem "xray.service" c.systemd.services.xray-exporter.requires)
      && !(lib.elem "xray.service" c.systemd.services.xray-exporter.partOf)
      && lib.hasInfix "--listen=127.0.0.1:9550 --xray-endpoint=127.0.0.1:10085" c.systemd.services.xray-exporter.serviceConfig.ExecStart;
    loopbackMonitoring = c.services.prometheus.listenAddress == "127.0.0.1"
      && c.services.prometheus.alertmanager.listenAddress == "127.0.0.1"
      && c.services.prometheus.exporters.node.listenAddress == "127.0.0.1"
      && c.services.prometheus.exporters.blackbox.listenAddress == "127.0.0.1"
      && c.services.grafana.settings.server.http_addr == "127.0.0.1";
    notificationsOff = c.services.prometheus.alertmanager.checkConfig
      && (builtins.head c.services.prometheus.alertmanager.configuration.receivers).name == "no-notifications";
    telegramCredential = !t.services.prometheus.alertmanager.checkConfig
      && t.systemd.services.alertmanager.serviceConfig.LoadCredential == [ "telegram-token:/run/fixture/token" ];
    grafana = c.services.grafana.settings.security.disable_initial_admin_creation
      && c.services.grafana.settings.plugins.preinstall_disabled
      && c.services.grafana.settings."auth.anonymous".org_role == "Viewer";
    badMaterial = rejected { services.xray.declarative.materialFile = lib.mkForce "/nix/store/invalid-material"; }
      && rejected { services.xray.declarative.materialFile = lib.mkForce "relative"; }
      && rejected { services.xray.declarative.materialFile = lib.mkForce null; };
    badPorts = rejected { services.xray.declarative.statsPort = settings.port; };
    badNode = rejected { networking.hostName = lib.mkForce ""; };
    badTelegram = rejected { services.xrayMonitoring.telegram = { enable = true; chatId = 0; botTokenFile = "/run/fixture/token"; }; }
      && rejected { services.xrayMonitoring.telegram = { enable = true; chatId = -1; botTokenFile = "/nix/store/invalid-token"; }; }
      && rejected { services.xrayMonitoring.telegram = { enable = true; chatId = -1; }; };
    badWarp = rejected { services.xray.declarative.warp.enable = true; }
      && rejected { services.xray.declarative.settings = lib.mkForce warpSettings; }
      && rejected { services.xray.declarative = {
        settings = lib.mkForce warpSettings;
        warp = { enable = true; materialFile = "/nix/store/invalid-warp"; };
      }; }
      && rejected { services.xray.declarative = {
        settings = lib.mkForce (settings // { outbounds = [ {
          tag = "warp"; protocol = "wireguard"; settings.secretKey = "synthetic-invalid-inline-key";
        } ]; });
        warp = { enable = true; materialFile = "/run/fixture/warp"; };
      }; };
    sopsContract = passed s && passed sw && passed sr
      && s.sops.secrets.xray_material.key == "material"
      && s.sops.secrets.xray_material.mode == "0400"
      && s.sops.secrets.xray_material.restartUnits == [ "xray.service" ]
      && s.sops.secrets.monitoring_telegram_token.key == "bot_token"
      && s.sops.secrets.monitoring_telegram_token.restartUnits == [ "alertmanager.service" ]
      && s.services.xray.declarative.materialFile == s.sops.secrets.xray_material.path
      && s.systemd.services.xray-config.requires == [ "sops-install-secrets.service" ]
      && s.systemd.services.alertmanager.requires == [ "sops-install-secrets.service" ]
      && sw.services.xray.declarative.warp.materialFile == sw.sops.secrets.xray_warp.path
      && !(sr.sops.secrets ? xray_warp);
    sopsAmbiguity = !(passed (sops [{ services.xray.declarative.materialFile = "/run/fixture/other"; }]))
      && !(passed (sops [{ services.xrayMonitoring.telegram.botTokenFile = "/run/fixture/other"; }]))
      && !(passed (sops [{ services.xray.declarative = { settings = lib.mkForce warpSettings;
        warp = { enable = true; materialFile = "/run/fixture/other"; }; }; }]));
    disabledNoSecrets = passed (evaluate [ ]) && !(evaluate [ ]).services.xray.enable
      && !(evaluate [ ]).services.prometheus.enable;
  };
  failed = builtins.attrNames (lib.filterAttrs (_: ok: !ok) checks);
in
assert lib.assertMsg (failed == [ ]) "Xray module checks failed: ${lib.concatStringsSep ", " failed}";
pkgs.runCommand "xray-modules-eval" { passthru = { inherit checks; }; } ''
  printf '%s\n' ${lib.escapeShellArg (builtins.toJSON checks)} > $out
''
