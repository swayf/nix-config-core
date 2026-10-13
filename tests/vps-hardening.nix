{ nixpkgs, module }:
let
  inherit (nixpkgs) lib;
  system = "x86_64-linux";
  pkgs = nixpkgs.legacyPackages.${system};
  sshSettings = {
    LogLevel = "VERBOSE";
    MaxAuthTries = 3;
    LoginGraceTime = "30s";
    MaxStartups = "10:30:30";
    AllowAgentForwarding = false;
    AllowTcpForwarding = "local";
    X11Forwarding = false;
  };
  auditSettings = {
    max_log_file = 20;
    num_logs = 5;
    max_log_file_action = "ROTATE";
    space_left_action = "SYSLOG";
    admin_space_left_action = "SYSLOG";
    disk_full_action = "SYSLOG";
    disk_error_action = "SYSLOG";
  };
  sysctls = {
    "kernel.kptr_restrict" = 2;
    "kernel.dmesg_restrict" = 1;
    "kernel.yama.ptrace_scope" = 1;
    "kernel.perf_event_paranoid" = 3;
    "kernel.unprivileged_bpf_disabled" = 1;
    "kernel.randomize_va_space" = 2;
    "fs.protected_hardlinks" = 1;
    "fs.protected_symlinks" = 1;
    "fs.protected_fifos" = 2;
    "fs.protected_regular" = 2;
    "net.ipv4.tcp_syncookies" = 1;
    "net.ipv4.conf.all.accept_source_route" = 0;
    "net.ipv4.conf.default.accept_source_route" = 0;
    "net.ipv4.conf.all.accept_redirects" = 0;
    "net.ipv4.conf.default.accept_redirects" = 0;
    "net.ipv4.conf.all.send_redirects" = 0;
    "net.ipv4.conf.default.send_redirects" = 0;
    "net.ipv6.conf.all.accept_redirects" = 0;
    "net.ipv6.conf.default.accept_redirects" = 0;
  };
  kernelParams = [ "slab_nomerge" "init_on_alloc=1" "init_on_free=1" "page_alloc.shuffle=1" ];
  evaluate = fixture:
    let
      c = (lib.nixosSystem {
        inherit system;
        modules = [
          module
          {
            networking.hostName = "fixture";
            services.openssh = { enable = true; ports = fixture.ports; };
            users.users.alice = { isNormalUser = true; home = "/srv/fixture-home/alice"; };
            system.stateVersion = fixture.stateVersion;
            boot.loader.grub.enable = false;
            fileSystems."/" = { device = "none"; fsType = "tmpfs"; };
          }
        ];
      }).config;
    in
    {
      inherit (fixture) name;
      checks = {
        identity = c.networking.hostName == "fixture"
          && c.users.users.alice.home == "/srv/fixture-home/alice"
          && c.system.stateVersion == fixture.stateVersion;
        fail2ban = c.services.fail2ban.enable && c.services.fail2ban.maxretry == 5
          && c.services.fail2ban.bantime == "1h"
          && c.services.fail2ban.bantime-increment.enable
          && c.services.fail2ban.bantime-increment.maxtime == "24h"
          && c.services.fail2ban.jails.sshd.enabled
          && lib.matchAttrs {
            backend = "systemd";
            port = lib.concatStringsSep "," (map toString fixture.ports);
            findtime = "10m";
          } c.services.fail2ban.jails.sshd.settings;
        ssh = lib.matchAttrs sshSettings c.services.openssh.settings;
        audit = c.security.auditd.enable && lib.matchAttrs auditSettings c.security.auditd.settings;
        security = c.security.protectKernelImage && c.security.allowUserNamespaces
          && !c.security.lockKernelModules && c.security.apparmor.enable;
        kernel = lib.all (param: lib.elem param c.boot.kernelParams) kernelParams
          && lib.matchAttrs sysctls c.boot.kernel.sysctl;
        coredumps = lib.matchAttrs { Storage = "none"; ProcessSizeMax = 0; }
          c.systemd.coredump.settings.Coredump
          && c.systemd.settings.Manager.DefaultLimitCORE == "0"
          && lib.elem { domain = "*"; type = "hard"; item = "core"; value = "0"; }
            c.security.pam.loginLimits;
        assertions = lib.all (entry: entry.assertion) c.assertions;
      };
    };
  results = map evaluate [
    { name = "single-port"; ports = [ 2222 ]; stateVersion = "25.11"; }
    { name = "multiple-ports"; ports = [ 2201 2202 ]; stateVersion = "26.05"; }
  ];
  failed = lib.concatMap (result:
    map (name: "${result.name}.${name}")
      (builtins.attrNames (lib.filterAttrs (_: passed: !passed) result.checks))
  ) results;
in
assert lib.assertMsg (failed == [ ]) "Hardening checks failed: ${lib.concatStringsSep ", " failed}";
pkgs.runCommand "vps-hardening-eval" { passthru = { inherit results; }; } ''
  printf '%s\n' ${lib.escapeShellArg (builtins.toJSON results)} > $out
''
