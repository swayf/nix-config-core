{ node }:
let
  labels = {
    inherit node;
    severity = "warning";
  };
  rule = alert: expr: duration: summary: {
    inherit alert expr labels;
    "for" = duration;
    annotations = { inherit summary; };
  };
  unavailable =
    job:
    (rule "ExporterUnavailable"
      ''up{job="${job}",node="${node}"} == 0 or absent(up{job="${job}",node="${node}"})''
      "2m"
      "Expected local exporter unavailable or missing"
    )
    // {
      labels = labels // {
        inherit job;
      };
    };
  health =
    service:
    (rule "MonitoringHealthFailed"
      ''probe_success{job="monitoring-health",node="${node}",service="${service}"} == 0 or absent(probe_success{job="monitoring-health",node="${node}",service="${service}"})''
      "2m"
      "Local monitoring health check failed or missing"
    )
    // {
      labels = labels // {
        inherit service;
      };
    };
in
{
  groups = [
    {
      name = "local-xray-vps";
      rules = [
        (unavailable "node")
        (unavailable "xray")
        (unavailable "blackbox")
        (rule "XrayStatsUnavailable"
          ''xray_up{job="xray",node="${node}"} == 0 or absent(xray_up{job="xray",node="${node}"})''
          "2m"
          "Xray StatsService unavailable or missing (not exporter HTTP health)"
        )
        (rule "NodeResourceMetricsMissing"
          ''absent(node_memory_MemAvailable_bytes{node="${node}"}) or absent(node_memory_MemTotal_bytes{node="${node}"}) or absent(node_memory_SwapTotal_bytes{node="${node}"}) or absent(node_memory_SwapFree_bytes{node="${node}"}) or absent(node_cpu_seconds_total{node="${node}",mode="idle"}) or absent(node_filesystem_size_bytes{node="${node}",mountpoint="/",fstype!~"tmpfs|rootfs"}) or absent(node_filesystem_avail_bytes{node="${node}",mountpoint="/",fstype!~"tmpfs|rootfs"}) or absent(node_filesystem_files{node="${node}",mountpoint="/",fstype!~"tmpfs|rootfs"}) or absent(node_filesystem_files_free{node="${node}",mountpoint="/",fstype!~"tmpfs|rootfs"})''
          "2m"
          "Expected local resource metrics missing"
        )
        (rule "MemoryLow"
          ''node_memory_MemAvailable_bytes{node="${node}"} / node_memory_MemTotal_bytes{node="${node}"} < 0.15''
          "10m"
          "Available RAM below 15%"
        )
        (rule "RootDiskLow"
          ''node_filesystem_avail_bytes{node="${node}",mountpoint="/",fstype!~"tmpfs|rootfs"} / node_filesystem_size_bytes{node="${node}",mountpoint="/",fstype!~"tmpfs|rootfs"} < 0.15''
          "10m"
          "Available root filesystem space below 15%"
        )
        (
          (rule "RootDiskCritical"
            ''node_filesystem_avail_bytes{node="${node}",mountpoint="/",fstype!~"tmpfs|rootfs"} / node_filesystem_size_bytes{node="${node}",mountpoint="/",fstype!~"tmpfs|rootfs"} < 0.05''
            "2m"
            "Available root filesystem space below 5%"
          )
          // {
            labels = labels // {
              severity = "critical";
            };
          }
        )
        (rule "RootInodesLow"
          ''node_filesystem_files_free{node="${node}",mountpoint="/",fstype!~"tmpfs|rootfs"} / node_filesystem_files{node="${node}",mountpoint="/",fstype!~"tmpfs|rootfs"} < 0.15''
          "10m"
          "Free root filesystem inodes below 15%"
        )
        (rule "SwapHigh"
          ''(1 - node_memory_SwapFree_bytes{node="${node}"} / node_memory_SwapTotal_bytes{node="${node}"}) > 0.8 and node_memory_SwapTotal_bytes{node="${node}"} > 0''
          "10m"
          "Swap usage above 80%"
        )
        (rule "CpuHigh"
          ''1 - avg by (node) (rate(node_cpu_seconds_total{node="${node}",mode="idle"}[5m])) > 0.9''
          "15m"
          "CPU usage above 90%"
        )
        (rule "XrayUnitNotActive"
          ''node_systemd_unit_state{node="${node}",name="xray.service",state="active"} == 0 or absent(node_systemd_unit_state{node="${node}",name="xray.service",state="active"})''
          "2m"
          "Xray systemd unit not active or missing"
        )
        (rule "XrayFrequentRestarts"
          ''resets(xray_uptime_seconds{node="${node}"}[15m]) >= 3 or increase(node_systemd_service_restart_total{node="${node}",name="xray.service"}[15m]) >= 3''
          "0m"
          "At least three observed Xray restarts in 15 minutes (initial threshold)"
        )
        (health "prometheus")
        (health "grafana")
        (health "alertmanager")
      ];
    }
  ];
}
