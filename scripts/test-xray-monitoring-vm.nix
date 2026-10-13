{ pkgs, modules }:
let
  source = pkgs.lib.fileset.toSource {
    root = ../.;
    fileset = pkgs.lib.fileset.unions [ ./xray-vm-fixture.py ./xray-test-fixture.py ];
  };
  fixture = "${source}/scripts/xray-vm-fixture.py";
in
pkgs.testers.runNixOSTest {
  name = "xray-monitoring";
  defaults.virtualisation.restrictNetwork = true;
  nodes = {
    server = { lib, ... }: {
      imports = [
        modules.xray-declarative
        modules.xray-monitoring
      ];
      networking.hostName = "test-vps";
      virtualisation.memorySize = 2048;
      services.xray.declarative = {
        enable = true;
        materialFile = "/run/test-xray-material";
        settings = {
          serverName = "target.test";
          target = "target:443";
          port = 443;
          tag = "test-inbound";
          clients = [
            "alice"
            "bob"
          ];
        };
      };
      specialisation.without-alice.configuration.services.xray.declarative.settings.clients = lib.mkForce [
        "bob"
      ];
      specialisation.invalid-config.configuration.services.xray.declarative.settings.outbounds =
        lib.mkForce
          [
            {
              tag = "direct";
              protocol = "invalid-protocol";
            }
          ];
      services.xrayMonitoring = {
        enable = true;
        telegram = {
          enable = true;
          chatId = -1;
          botTokenFile = "/run/test-telegram-token";
        };
      };
      # This nixpkgs option is a raw attrs value, not a recursive submodule:
      # override the whole config so nested mkForce records never become JSON.
      # The native receiver talks only to our fake loopback Telegram API.
      services.prometheus.alertmanager.configuration = lib.mkForce {
        route = {
          receiver = "telegram";
          group_by = [
            "node"
            "observer"
            "alertname"
          ];
          group_wait = "1s";
          group_interval = "1s";
          repeat_interval = "4h";
        };
        receivers = [
          {
            name = "telegram";
            telegram_configs = [
              {
                bot_token_file = "$CREDENTIALS_DIRECTORY/telegram-token";
                api_url = "http://127.0.0.1:18080";
                chat_id = -1;
                send_resolved = true;
                parse_mode = "HTML";
                message = "{{ .Status }} {{ range .Alerts }}{{ .Labels.alertname }} node={{ .Labels.node }} observer={{ .Labels.observer }} {{ end }}";
                http_config.proxy_from_environment = false;
              }
            ];
          }
        ];
      };
      # Shorter scrape interval in VM, same production rules and 2m hold times.
      services.prometheus.globalConfig = {
        scrape_interval = lib.mkForce "5s";
        scrape_timeout = lib.mkForce "4s";
        evaluation_interval = lib.mkForce "5s";
      };
      environment.systemPackages = [
        pkgs.curl
        pkgs.jq
        pkgs.openssl
        pkgs.iproute2
      ];
      systemd.services.test-seed = {
        before = [
          "xray-config.service"
          "alertmanager.service"
        ];
        requiredBy = [
          "xray-config.service"
          "alertmanager.service"
        ];
        serviceConfig = {
          Type = "oneshot";
          RemainAfterExit = true;
          UMask = "0077";
        };
        path = [ pkgs.openssl ];
        script = "${pkgs.python3}/bin/python3 ${fixture} seed";
      };
      systemd.services.test-telegram = {
        wantedBy = [ "multi-user.target" ];
        script = "exec ${pkgs.python3}/bin/python3 ${fixture} telegram";
      };
    };
    target = {
      virtualisation.memorySize = 512;
      networking.firewall.allowedTCPPorts = [ 443 ];
      systemd.services.test-tls = {
        wantedBy = [ "multi-user.target" ];
        path = [ pkgs.openssl ];
        script = "exec ${pkgs.python3}/bin/python3 ${fixture} tls";
      };
    };
    client = {
      virtualisation.memorySize = 512;
      environment.systemPackages = [
        pkgs.xray
        pkgs.curl
        pkgs.netcat-openbsd
        pkgs.python3
      ];
      systemd.services.test-client = {
        serviceConfig.ExecStart = "${pkgs.xray}/bin/xray run -config /run/test-client.json";
      };
    };
  };
  testScript = ''
    import json
    import shlex
    from datetime import datetime, timedelta, timezone

    # The driver otherwise adds QEMU -no-reboot and exits on guest reboot.
    server.start(allow_reboot=True)
    start_all()
    target.wait_for_unit("test-tls.service")
    target.wait_for_open_port(443)
    server.wait_for_unit("xray.service")
    server.wait_for_unit("xray-exporter.service")
    server.wait_for_unit("prometheus.service")
    server.wait_for_unit("grafana.service")
    server.wait_for_unit("alertmanager.service")
    server.wait_for_open_port(18080)
    server.wait_until_succeeds("curl -fsS http://127.0.0.1:9550/scrape | grep -x 'xray_up 1'")
    for endpoint in ["9090/-/ready", "3000/api/health", "9093/-/ready"]:
        server.wait_until_succeeds(f"curl -fsS http://127.0.0.1:{endpoint}")
    server.succeed("test $(stat -c %a /run/xray-config/config.json) = 600")
    server.succeed("systemctl show xray -p AmbientCapabilities --value | grep -x cap_net_bind_service")
    server.succeed("systemctl show xray -p DynamicUser --value | grep -x yes")
    server.succeed("systemctl show alertmanager -p DynamicUser --value | grep -x yes")
    grafana_key = server.succeed("cat /var/lib/grafana/secret-key").strip()
    server.succeed("test $(stat -c %a /var/lib/grafana/secret-key) = 600")
    # First prove DNS/network reachability: a DNS error must not pass negatives.
    client.wait_until_succeeds("nc -z -w 2 test-vps 443")
    # Verify actual listen addresses as well as external firewall isolation.
    for port in [3000, 9090, 9093, 9100, 9115, 9550, 10085]:
        addresses = server.succeed(f"ss -H -ltn 'sport = :{port}' | awk '{{print $4}}'").splitlines()
        assert addresses == [f"127.0.0.1:{port}"], (port, addresses)
        client.fail(f"nc -z -w 2 test-vps {port}")
    assert server.succeed("ss -H -ltn 'sport = :9094'; ss -H -lun 'sport = :9094'").strip() == ""
    client.fail("nc -z -w 2 test-vps 9094")

    material = server.succeed("cat /run/test-xray-material").strip()
    client.succeed("umask 077; printf %s " + shlex.quote(material) + " > /run/test-xray-material")
    client.succeed("python3 ${fixture} client --server test-vps > /run/test-client.json")
    client.succeed("systemctl start test-client")
    client.wait_for_open_port(1080)
    proxy = "curl -kfsS --http1.1 --max-time 10 --socks5-hostname 127.0.0.1:1080 https://target:443/"
    client.wait_until_succeeds(proxy + " | grep real-vless-tunnel-fixture > /dev/null")
    # User, inbound, outbound counters; repeat scrape must not reset them.
    metrics = server.succeed("curl -fsS http://127.0.0.1:9550/scrape")
    for dimension, target_name in [("user", "alice"), ("inbound", "test-inbound"), ("outbound", "direct")]:
        assert f'dimension="{dimension}",target="{target_name}"' in metrics
    counter = "curl -fsS http://127.0.0.1:9550/scrape | grep '^xray_traffic_downlink_bytes_total{dimension=\"user\",target=\"alice\"}' | awk '{print $2}'"
    count1 = float(server.succeed(counter).strip())
    count2 = float(server.succeed(counter).strip())
    assert count1 > 0 and count2 >= count1
    query = "curl -fsSG http://127.0.0.1:9090/api/v1/query --data-urlencode "
    server.wait_until_succeeds(query + shlex.quote('query=xray_traffic_downlink_bytes_total{dimension="user",target="alice"} > 0') + " | jq -e '.data.result | length > 0'")
    server.wait_until_succeeds(query + shlex.quote('query=node_systemd_unit_state{name="xray.service",state="active"} == 1') + " | jq -e '.data.result | length > 0'")
    server.wait_until_succeeds("curl -fsS http://127.0.0.1:3000/api/dashboards/uid/xray-vps | jq -e '.dashboard.uid == \"xray-vps\"'")
    server.succeed("curl -fsS http://127.0.0.1:3000/api/datasources/uid/prometheus | jq -e '.url == \"http://127.0.0.1:9090\" and .readOnly == true'")
    server.wait_until_succeeds(query + shlex.quote('query=probe_success{job="monitoring-health"} == 1') + " | jq -e '.data.result | length == 3'")

    # Establish enough persisted samples before any restarts or outages.
    history_query = query + shlex.quote('query=count_over_time(xray_traffic_downlink_bytes_total{dimension="user",target="alice"}[1h])')
    history_check = " | jq -e '.data.result[0].value[1] | tonumber > 2'"
    server.wait_until_succeeds(history_query + history_check, timeout=60)
    # Use the exact Prometheus evaluation timestamp, not rounded guest time.
    history_time = str(json.loads(server.succeed(history_query))["data"]["result"][0]["value"][0])
    saved_history_query = history_query + " --data-urlencode time=" + history_time

    # No-op activation must not restart Xray or rotate credentials.
    pid = server.succeed("systemctl show xray -p MainPID --value").strip()
    server.succeed("/run/current-system/bin/switch-to-configuration test")
    assert server.succeed("systemctl show xray -p MainPID --value").strip() == pid
    assert server.succeed("cat /run/test-xray-material").strip() == material
    server.succeed("systemctl restart xray")
    server.wait_until_succeeds("curl -fsS http://127.0.0.1:9550/scrape | grep -x 'xray_up 1'")
    client.wait_until_succeeds(proxy + " > /dev/null")

    # Apply removal and roll back an actual NixOS specialization, same UUID.
    server.succeed("/run/booted-system/specialisation/without-alice/bin/switch-to-configuration test")
    client.fail(proxy + " > /dev/null")
    assert server.succeed("cat /run/test-xray-material").strip() == material
    server.succeed("/run/booted-system/bin/switch-to-configuration test")
    client.wait_until_succeeds(proxy + " > /dev/null")

    # Renderer-valid JSON with an unsupported protocol must fail Xray -test.
    server.fail("/run/booted-system/specialisation/invalid-config/bin/switch-to-configuration test")
    server.fail("systemctl is-active xray")
    server.succeed("test ! -e /run/xray-config/config.json")
    server.succeed("/run/booted-system/bin/switch-to-configuration test")
    client.wait_until_succeeds(proxy + " > /dev/null")

    # Invalid material is rejected without echoing it, and no old config starts.
    server.succeed("cp /run/test-xray-material /run/material-backup; printf '%s' '{\"private_key\":\"NEVER_PRINT_THIS\"}' > /run/test-xray-material")
    server.fail("systemctl restart xray")
    server.fail("systemctl is-active xray")
    server.fail("journalctl -u xray -u xray-config | grep NEVER_PRINT_THIS")
    server.succeed("mv /run/material-backup /run/test-xray-material; systemctl reset-failed xray xray-config; systemctl start xray")
    try:
        client.wait_until_succeeds(proxy + " > /dev/null", timeout=60)
    except Exception:
        print(server.succeed("ss -lntp; journalctl -u xray -u xray-config --no-pager -n 40"))
        print(target.succeed("ss -ntp; journalctl -u test-tls --no-pager -n 40"))
        print(client.succeed("journalctl -u test-client --no-pager -n 40"))
        raise

    # M2 while exporter HTTP stays alive; firing and resolved via native receiver.
    server.succeed("systemctl stop xray")
    server.succeed("curl -fsS http://127.0.0.1:9550/scrape | grep -x 'xray_up 0'")
    server.wait_until_succeeds(query + shlex.quote('query=ALERTS{alertname="XrayStatsUnavailable",alertstate="firing"}') + " | jq -e '.data.result | length > 0'", timeout=180)
    server.wait_until_succeeds("grep 'firing.*XrayStatsUnavailable' /run/test-telegram-events", timeout=60)
    server.succeed("systemctl start xray")
    # gRPC's connection backoff can reach 120s after a prolonged outage.
    # Wait for actual stats recovery without restarting the independent exporter.
    server.wait_until_succeeds("curl -fsS http://127.0.0.1:9550/scrape | grep -x 'xray_up 1'", timeout=180)
    server.wait_until_succeeds("grep 'resolved.*XrayStatsUnavailable' /run/test-telegram-events", timeout=60)
    # M1 is a distinct failure; persist silence across AM restart.
    server.succeed("systemctl stop xray-exporter")
    server.wait_until_succeeds(query + shlex.quote('query=ALERTS{alertname="ExporterUnavailable",job="xray",alertstate="firing"}') + " | jq -e '.data.result | length > 0'", timeout=180)
    now = datetime.now(timezone.utc)
    silence = {"matchers": [{"name": "alertname", "value": "ExporterUnavailable", "isRegex": False}],
               "startsAt": now.isoformat(), "endsAt": (now + timedelta(hours=1)).isoformat(),
               "createdBy": "vm-test", "comment": "isolated fixture"}
    silence_id = json.loads(server.succeed("curl -fsS -H 'Content-Type: application/json' -d " + shlex.quote(json.dumps(silence)) + " http://127.0.0.1:9093/api/v2/silences"))["silenceID"]
    silenced_query = "curl -fsS http://127.0.0.1:9093/api/v2/alerts | jq -e " + shlex.quote('[.[] | select(.labels.alertname == "ExporterUnavailable") | .status.silencedBy[]] | index("' + silence_id + '") != null')
    server.wait_until_succeeds(silenced_query)
    server.succeed("systemctl restart alertmanager")
    server.wait_until_succeeds(silenced_query)
    server.succeed("systemctl start xray-exporter")
    server.wait_until_succeeds("curl -fsS http://127.0.0.1:9093/api/v2/silences | jq -e 'length > 0'")
    server.succeed("systemctl stop prometheus; systemctl start prometheus")
    try:
        server.wait_until_succeeds(saved_history_query + history_check, timeout=60)
    except Exception:
        print(server.succeed(saved_history_query))
        print(server.succeed("journalctl -u prometheus --no-pager -n 40; du -sh /var/lib/prometheus2"))
        raise
    server.reboot()
    server.wait_for_unit("xray.service")
    server.wait_for_unit("prometheus.service")
    server.wait_for_unit("grafana.service")
    server.wait_for_unit("alertmanager.service")
    assert server.succeed("cat /run/test-xray-material").strip() == material
    assert server.succeed("cat /var/lib/grafana/secret-key").strip() == grafana_key
    client.wait_until_succeeds(proxy + " > /dev/null")
    server.wait_until_succeeds(saved_history_query + history_check, timeout=60)
    server.wait_until_succeeds("curl -fsS http://127.0.0.1:3000/api/dashboards/uid/xray-vps | jq -e '.dashboard.uid == \"xray-vps\"'")
    server.wait_until_succeeds("curl -fsS http://127.0.0.1:9093/api/v2/silences | jq -e 'length > 0'")
    assert server.succeed("if test -d /var/lib/grafana/plugins; then find /var/lib/grafana/plugins -mindepth 1 -print -quit; fi").strip() == ""
  '';
}
