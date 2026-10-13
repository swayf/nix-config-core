# Explicit VPS baseline with the maintained stock kernel.
{ config, ... }:
{
  services.fail2ban = {
    enable = true;
    maxretry = 5;
    bantime = "1h";
    bantime-increment = {
      enable = true;
      maxtime = "24h";
    };
    jails.sshd = {
      enabled = true;
      settings = {
        backend = "systemd";
        port = builtins.concatStringsSep "," (map toString config.services.openssh.ports);
        findtime = "10m";
      };
    };
  };
  services.openssh.settings = {
    LogLevel = "VERBOSE"; # Fail2ban observes failed public-key authentication.
    MaxAuthTries = 3;
    LoginGraceTime = "30s";
    MaxStartups = "10:30:30";
    AllowAgentForwarding = false;
    AllowTcpForwarding = "local"; # Preserve -L for loopback monitoring UIs.
    X11Forwarding = false;
  };

  security = {
    protectKernelImage = true; # Disables kexec only in the installed system.
    allowUserNamespaces = true; # Required by Nix sandbox; no blanket namespace ban.
    lockKernelModules = false; # Keep normal driver loading available.
    apparmor.enable = true;
    auditd = {
      enable = true;
      settings = {
        max_log_file = 20; # MiB per file; keep audit-log rotation bounded.
        num_logs = 5;
        max_log_file_action = "ROTATE";
        space_left_action = "SYSLOG";
        admin_space_left_action = "SYSLOG";
        disk_full_action = "SYSLOG";
        disk_error_action = "SYSLOG";
      };
    };
  };

  boot.kernelParams = [ "slab_nomerge" "init_on_alloc=1" "init_on_free=1" "page_alloc.shuffle=1" ];
  boot.kernel.sysctl = {
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

  # Crashes must not persist credentials in core files.
  systemd.coredump.settings.Coredump = { Storage = "none"; ProcessSizeMax = 0; };
  systemd.settings.Manager.DefaultLimitCORE = "0";
  security.pam.loginLimits = [ { domain = "*"; type = "hard"; item = "core"; value = "0"; } ];

  # This is not a kernel-module/user-namespace lockdown or a compliance profile.
  # Root key access and passwordless sudo remain deliberate deployment choices.
}
