# Standalone profile evaluation; no private flake, builds, activation or decryption.
{ nixpkgs, modules }:
let
  inherit (nixpkgs) lib;
  pkgs = nixpkgs.legacyPackages.x86_64-linux;
  settings = { serverName = "target.test"; target = "127.0.0.1:2443"; clients = [ "alice" ]; };
  key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEhISEhISEhISEhISEhISEhISEhISEhISEhISEhISEhI synthetic";
  base = {
    networking.hostName = "fixture-node";
    system.stateVersion = "26.05";
    boot.loader.grub.enable = false;
    fileSystems."/" = { device = "none"; fsType = "tmpfs"; };
    systemd.network.networks."10-fixture" = {
      matchConfig.Name = "eth-fixture";
      networkConfig = { DHCP = "no"; IPv6AcceptRA = true; LinkLocalAddressing = "ipv6"; };
    };
  };
  profile = { services.vps = { enable = true; adminKeys = [ key ]; inherit settings; }; };
  runtime = { services.xray.declarative = {
    materialFile = "/run/fixture/material"; warp.materialFile = "/run/fixture/warp";
  }; };
  evaluate = extra: (lib.nixosSystem {
    system = "x86_64-linux";
    modules = [ modules.vps-base base ] ++ extra;
  }).config;
  c = evaluate [ profile runtime ];
  passed = cfg: lib.all (a: a.assertion) cfg.assertions;
  rejected = extra: !(passed (evaluate ([ profile runtime ] ++ extra)));
  disabled = evaluate [ ];
  noWarp = evaluate [ profile runtime { services.xray.declarative.warp.enable = false; } ];
  overridden = evaluate [ profile runtime {
    services.vps = {
      adminUser = "alice"; sshPort = 2222; warpDomains = [ "example.test" ];
      packages = [ pkgs.hello ];
      settings = { port = 2444; tag = "caller-inbound"; sniffing.enabled = false; };
    };
    users.users.alice.home = "/srv/fixture-home/alice";
    time.timeZone = "Europe/Berlin";
    nix.settings = { max-jobs = 3; cores = 4; trusted-users = [ "root" "alice" ]; };
    nix.gc = { automatic = false; dates = "daily"; options = "--delete-older-than 30d"; };
    services.journald.extraConfig = "SystemMaxUse=80M";
    services.xrayMonitoring.enable = false;
  } ];
  attempted = evaluate [ profile runtime {
    networking.enableIPv6 = true;
    boot.kernel.sysctl."net.ipv6.conf.all.disable_ipv6" = 0;
    services.openssh.settings = {
      PasswordAuthentication = true; KbdInteractiveAuthentication = true;
      PermitRootLogin = "yes"; AllowUsers = [ "root" ];
    };
    systemd.network.networks."90-extra" = {
      matchConfig.Name = "unused-test0";
      networkConfig = { IPv6AcceptRA = true; LinkLocalAddressing = "ipv6"; };
    };
  } ];
  secretFile = builtins.toFile "synthetic-sops.yaml" "synthetic evaluation fixture; no credentials\n";
  # Only a contract stub in core: real pinned SOPS compatibility stays caller-side.
  cases = [
    { encrypted = false; telegram = false; port = 22; }
    { encrypted = false; telegram = true; port = 3333; }
    { encrypted = true; telegram = false; port = 22; }
    { encrypted = true; telegram = true; port = 3333; }
  ];
  contract = case: let
    cfg = (lib.nixosSystem {
      system = "x86_64-linux";
      specialArgs.sopsModule = ./sops-stub.nix;
      modules = [ modules.vps-base modules.xray-sops base profile
        { services.vps = { adminUser = "alice"; sshPort = case.port; }; }
        (if case.encrypted then { services.xray.declarative.secretFile = secretFile; } else runtime)
        { services.xrayMonitoring.telegram = {
          enable = case.telegram; chatId = -1;
          secretFile = if case.encrypted && case.telegram then secretFile else null;
          botTokenFile = lib.mkIf (!case.encrypted && case.telegram) "/run/fixture/token";
        }; }
      ];
    }).config;
  in passed cfg && cfg.services.openssh.settings.AllowUsers == [ "alice" ]
    && cfg.services.openssh.ports == [ case.port ]
    && cfg.systemd.services.xray-config.serviceConfig.LoadCredential ==
      (if case.encrypted then [ "material:/run/synthetic-secrets/xray_material" "warp:/run/synthetic-secrets/xray_warp" ]
       else [ "material:/run/fixture/material" "warp:/run/fixture/warp" ])
    && (if case.encrypted then cfg.sops.secrets.xray_material.sopsFile == secretFile
      && cfg.sops.secrets.xray_material.key == "material"
      && cfg.sops.secrets.xray_warp.key == "warp"
      && cfg.systemd.services.xray-config.requires == [ "sops-install-secrets.service" ]
      else cfg.sops.secrets == {} && cfg.systemd.services.xray-config.requires == [])
    && (if case.telegram then cfg.systemd.services.alertmanager.serviceConfig.LoadCredential ==
      [ "telegram-token:${if case.encrypted then "/run/synthetic-secrets/monitoring_telegram_token" else "/run/fixture/token"}" ]
      else cfg.services.prometheus.alertmanager.checkConfig);
  checks = {
    defaultAdmin = passed c && c.services.vps.adminUser == "admin"
      && c.services.openssh.settings.AllowUsers == [ "admin" ]
      && c.users.users.admin.isNormalUser && lib.elem "wheel" c.users.users.admin.extraGroups
      && c.users.users.admin.openssh.authorizedKeys.keys == [ key ];
    differentAdmins = lib.all (name: let a = evaluate [ profile runtime { services.vps.adminUser = name; } ]; in
      passed a && a.services.openssh.settings.AllowUsers == [ name ]
      && a.users.users.${name}.isNormalUser) [ "alice" "ops-user" ];
    overrides = passed overridden && overridden.users.users.alice.home == "/srv/fixture-home/alice"
      && overridden.services.openssh.ports == [ 2222 ]
      && overridden.services.xray.declarative.settings.port == 2444
      && overridden.services.xray.declarative.settings.tag == "caller-inbound"
      && !overridden.services.xray.declarative.settings.sniffing.enabled
      && (builtins.elemAt overridden.services.xray.declarative.settings.routing.rules 2).domain == [ "domain:example.test" ]
      && overridden.time.timeZone == "Europe/Berlin"
      && overridden.nix.settings.max-jobs == 3 && overridden.nix.settings.cores == 4
      && lib.unique overridden.nix.settings.trusted-users == [ "root" "alice" ]
      && !overridden.nix.gc.automatic && overridden.nix.gc.dates == [ "daily" ]
      && overridden.services.journald.extraConfig == "SystemMaxUse=80M"
      && !overridden.services.xrayMonitoring.enable
      && lib.any (p: (p.pname or "") == "hello") overridden.environment.systemPackages
      && !(lib.any (p: (p.pname or "") == "git") overridden.environment.systemPackages);
    ipv4KeyOnly = !attempted.networking.enableIPv6
      && lib.elem "ipv6.disable=1" attempted.boot.kernelParams
      && attempted.boot.kernel.sysctl."net.ipv6.conf.all.disable_ipv6" == 1
      && lib.all (n: !n.networkConfig.IPv6AcceptRA && n.networkConfig.LinkLocalAddressing == "no")
        (builtins.attrValues attempted.systemd.network.networks)
      && !attempted.services.openssh.settings.PasswordAuthentication
      && !attempted.services.openssh.settings.KbdInteractiveAuthentication
      && attempted.services.openssh.settings.PermitRootLogin == "no"
      && attempted.services.openssh.settings.AllowUsers == [ "admin" ];
    warpOptional = passed noWarp && !(lib.any (o: o.tag == "warp") noWarp.services.xray.declarative.settings.outbounds)
      && builtins.length noWarp.services.xray.declarative.settings.routing.rules == 2
      && noWarp.systemd.services.xray-config.serviceConfig.LoadCredential == [ "material:/run/fixture/material" ];
    runtimeOnly = !(c ? sops) && c.systemd.services.xray-config.serviceConfig.LoadCredential == [ "material:/run/fixture/material" "warp:/run/fixture/warp" ];
    toplevelEvaluates = lib.hasSuffix ".drv" c.system.build.toplevel.drvPath;
    disabled = passed disabled && !disabled.services.fail2ban.enable && !disabled.services.openssh.enable
      && !disabled.services.xray.enable && !disabled.services.xrayMonitoring.enable
      && !(disabled.users.users ? admin) && !(disabled ? sops)
      && disabled.systemd.network.networks."10-fixture".networkConfig.IPv6AcceptRA;
    rejectsAdmins = rejected [{ services.vps.adminUser = "root"; }]
      && rejected [{ services.vps.adminUser = "nobody"; }]
      && !(builtins.tryEval (evaluate [ profile runtime { services.vps.adminUser = "Bad.User"; } ]).services.vps.adminUser).success;
    rejectsKeysPortsDomains = rejected [{ services.vps.adminKeys = lib.mkForce []; }]
      && rejected [{ services.vps.sshPort = 443; }]
      && rejected [{ services.xray.declarative.settings.port = 22; }]
      && rejected [{ services.vps.warpDomains = [ "regexp:.*" ]; }];
    rejectsMaterial = !(passed (evaluate [ profile ]))
      && rejected [{ services.xray.declarative.materialFile = lib.mkForce "/nix/store/invalid-material"; }]
      && rejected [{ services.xray.declarative.warp.materialFile = lib.mkForce null; }];
    rejectsTelegram = rejected [{ services.xrayMonitoring.telegram.enable = true; }]
      && rejected [{ services.xrayMonitoring.telegram = {
        enable = true; chatId = -1; botTokenFile = "/nix/store/invalid-token";
      }; }];
    sopsContract = lib.all contract cases;
  };
  failed = builtins.attrNames (lib.filterAttrs (_: ok: !ok) checks);
in
assert lib.assertMsg (failed == []) "VPS profile checks failed: ${lib.concatStringsSep ", " failed}";
pkgs.runCommand "vps-base-eval" { passthru = { inherit checks; }; } ''
  printf '%s\n' ${lib.escapeShellArg (builtins.toJSON checks)} > $out
''
