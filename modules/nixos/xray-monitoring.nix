{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.xrayMonitoring;
  node = config.networking.hostName;
  json = pkgs.formats.json { };
  rules = json.generate "xray-monitoring-rules.json" (
    import ./xray-monitoring-rules.nix { inherit node; }
  );
  blackboxConfig = json.generate "monitoring-health.json" {
    modules.http_local = {
      prober = "http";
      timeout = "5s";
      http = {
        valid_status_codes = [ 200 ];
        preferred_ip_protocol = "ip4";
        proxy_from_environment = false;
      };
    };
  };
  tokenPath = cfg.telegram.botTokenFile;
  telegram = cfg.telegram.enable;
  localTarget = job: port: {
    job_name = job;
    static_configs = [
      {
        targets = [ "127.0.0.1:${toString port}" ];
        labels = { inherit node; };
      }
    ];
  };
  healthTarget = service: url: {
    targets = [ url ];
    labels = { inherit node service; };
  };
  exporter = pkgs.callPackage ./xray-exporter-package.nix { };
in
{
  options.services.xrayMonitoring = {
    enable = lib.mkEnableOption "loopback-only VPS metrics, dashboards and alerts";
    telegram = {
      enable = lib.mkEnableOption "Alertmanager's native Telegram receiver";
      chatId = lib.mkOption {
        type = lib.types.int;
        default = 0;
      };
      botTokenFile = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "Runtime token file outside the store, supplied by the caller or explicit SOPS adapter. No token in Nix.";
      };
    };
  };
  config = lib.mkIf cfg.enable (
    lib.mkMerge [
      {
        assertions = [
          {
            assertion = config.services.xray.declarative.enable;
            message = "xrayMonitoring requires the declarative native Xray module.";
          }
          {
            assertion =
              !telegram
              || (
                cfg.telegram.chatId != 0
                && cfg.telegram.botTokenFile != null
              );
            message = "Telegram requires a nonzero chatId and runtime botTokenFile (or the explicit SOPS adapter).";
          }
          {
            assertion =
              cfg.telegram.botTokenFile == null
              || (
                lib.hasPrefix "/" cfg.telegram.botTokenFile
                && !lib.hasPrefix "/nix/store/" cfg.telegram.botTokenFile
              );
            message = "Telegram botTokenFile must be an absolute runtime path outside the store.";
          }
          {
            assertion = builtins.match "[a-zA-Z0-9_-]+" node != null;
            message = "Monitoring hostname must be safe as a PromQL label value.";
          }
        ];
        systemd.services.xray-exporter = {
          description = "Xray StatsService exporter (non-resetting counters, no access logs)";
          wantedBy = [ "multi-user.target" ];
          after = [ "network.target" ];
          # Deliberately NOT Requires/PartOf Xray: M2 must work while Xray is down.
          serviceConfig = {
            ExecStart = "${lib.getExe exporter} --listen=127.0.0.1:9550 --xray-endpoint=127.0.0.1:${toString config.services.xray.declarative.statsPort} --with-user-metrics --log-path=disabled --scrape-timeout=3";
            DynamicUser = true;
            Restart = "on-failure";
            RestartSec = "5s";
            NoNewPrivileges = true;
            CapabilityBoundingSet = [ "" ];
            ProtectSystem = "strict";
            ProtectHome = true;
            PrivateTmp = true;
            PrivateDevices = true;
            ProtectKernelTunables = true;
            ProtectKernelModules = true;
            ProtectControlGroups = true;
            RestrictAddressFamilies = [
              "AF_UNIX"
              "AF_INET"
              "AF_INET6"
            ];
            LimitCORE = 0;
          };
        };
        services.prometheus = {
          enable = true;
          listenAddress = "127.0.0.1";
          globalConfig = {
            scrape_interval = "30s";
            scrape_timeout = "10s";
            evaluation_interval = "30s";
            external_labels.observer = node;
          };
          retentionTime = "15d";
          extraFlags = [ "--storage.tsdb.retention.size=1GB" ];
          ruleFiles = [ rules ];
          alertmanagers = [ { static_configs = [ { targets = [ "127.0.0.1:9093" ]; } ]; } ];
          scrapeConfigs = [
            (localTarget "node" 9100)
            ((localTarget "xray" 9550) // { metrics_path = "/scrape"; })
            (localTarget "blackbox" 9115)
            (localTarget "prometheus" 9090)
            {
              job_name = "monitoring-health";
              metrics_path = "/probe";
              params.module = [ "http_local" ];
              static_configs = [
                (healthTarget "prometheus" "http://127.0.0.1:9090/-/ready")
                (healthTarget "grafana" "http://127.0.0.1:3000/api/health")
                (healthTarget "alertmanager" "http://127.0.0.1:9093/-/ready")
              ];
              relabel_configs = [
                {
                  source_labels = [ "__address__" ];
                  target_label = "__param_target";
                }
                {
                  source_labels = [ "__param_target" ];
                  target_label = "instance";
                }
                {
                  target_label = "__address__";
                  replacement = "127.0.0.1:9115";
                }
              ];
            }
          ];
          exporters = {
            node = {
              enable = true;
              listenAddress = "127.0.0.1";
              enabledCollectors = [ "systemd" ];
              extraFlags = [
                "--collector.systemd.unit-include=(xray|prometheus|grafana|alertmanager|xray-exporter)[.]service"
                "--collector.systemd.enable-restarts-metrics"
                "--collector.systemd.enable-start-time-metrics"
              ];
            };
            blackbox = {
              enable = true;
              listenAddress = "127.0.0.1";
              configFile = blackboxConfig;
            };
          };
          alertmanager = {
            enable = true;
            listenAddress = "127.0.0.1";
            extraFlags = [ "--cluster.listen-address=" ];
            # Build-time amtool cannot open a runtime systemd credential.
            checkConfig = !telegram;
            configuration = {
              route = {
                receiver = if telegram then "telegram" else "no-notifications";
                group_by = [
                  "node"
                  "observer"
                  "alertname"
                ];
                group_wait = "30s";
                group_interval = "5m";
                repeat_interval = "4h";
              };
              receivers = [
                (
                  if telegram then
                    {
                      name = "telegram";
                      telegram_configs = [
                        {
                          bot_token_file = "$CREDENTIALS_DIRECTORY/telegram-token";
                          chat_id = cfg.telegram.chatId;
                          send_resolved = true;
                          parse_mode = "HTML";
                          message = "{{ .Status }}{{ range .Alerts }}\n{{ .Labels.alertname }} node={{ .Labels.node }} observer={{ .Labels.observer }}{{ end }}";
                          http_config = {
                            proxy_from_environment = false;
                          };
                        }
                      ];
                    }
                  else
                    { name = "no-notifications"; }
                )
              ];
            };
          };
        };
        # Only HTTP health probes: no CAP_NET_RAW for blackbox.
        systemd.services.prometheus-blackbox-exporter.serviceConfig = {
          AmbientCapabilities = lib.mkForce [ "" ];
          CapabilityBoundingSet = lib.mkForce [ "" ];
        };
        # Grafana's DB encryption key is persistent, generated once on first start.
        # It is not an Xray credential and is never regenerated at rebuild.
        systemd.services.grafana.preStart = lib.mkBefore ''
          umask 077
          if [ ! -e /var/lib/grafana/secret-key ]; then
            ${pkgs.openssl}/bin/openssl rand -hex 32 > /var/lib/grafana/.secret-key.tmp
            mv /var/lib/grafana/.secret-key.tmp /var/lib/grafana/secret-key
          fi
          test -s /var/lib/grafana/secret-key
        '';
        services.grafana = {
          enable = true;
          settings = {
            server = {
              http_addr = "127.0.0.1";
              http_port = 3000;
            };
            analytics = {
              reporting_enabled = false;
              check_for_updates = false;
              check_for_plugin_updates = false;
            };
            # No unpinned plugin downloads or updates outside the Nix closure.
            plugins = {
              preinstall_disabled = true;
              preinstall_auto_update = false;
              public_key_retrieval_disabled = true;
            };
            security = {
              disable_initial_admin_creation = true;
              secret_key = "$__file{/var/lib/grafana/secret-key}";
            };
            "auth.anonymous" = {
              enabled = true;
              org_role = "Viewer";
            };
            auth.disable_login_form = true;
            "auth.basic".enabled = false;
            unified_alerting.enabled = false;
          };
          provision = {
            enable = true;
            datasources.settings.datasources = [
              {
                name = "Prometheus";
                uid = "prometheus";
                type = "prometheus";
                access = "proxy";
                url = "http://127.0.0.1:9090";
                isDefault = true;
                editable = false;
              }
            ];
            dashboards.settings.providers = [
              {
                name = "VPS";
                options.path = ./xray-dashboards;
                disableDeletion = true;
                allowUiUpdates = false;
              }
            ];
          };
        };
        services.journald.extraConfig = lib.mkDefault "SystemMaxUse=200M";
      }
      (lib.mkIf telegram {
        systemd.services.alertmanager = {
          serviceConfig.LoadCredential = [ "telegram-token:${tokenPath}" ];
          # Run as the same DynamicUser, after credential/envsubst materialization.
          preStart = lib.mkAfter ''
            ${config.services.prometheus.alertmanager.package}/bin/amtool check-config /tmp/alert-manager-substituted.yaml >/dev/null
          '';
        };
      })
    ]
  );
}
