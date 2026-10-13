{
  pkgs,
  lib,
  port,
  directPort ? null,
  name ? "localhost/container-egress-gateway",
  tunDevice ? "tun0",
}:
let
  # Mihomo advertises its bound 127.0.0.1 in SOCKS UDP ASSOCIATE. Behind
  # The pasta loopback mapping uses 10.0.2.2. Mihomo advertises its bound
  # 127.0.0.1 in SOCKS UDP ASSOCIATE, so use the same upstream IP for TCP and
  # UDP; the nft allowlist still restricts the relay port.
  tun2socks = pkgs.tun2socks.overrideAttrs (old: {
    postPatch = (old.postPatch or "") + ''
      substituteInPlace proxy/socks5/socks5.go \
        --replace-fail 'if bindAddr.IP.IsUnspecified()' 'if true'
    '';
  });
  firewall = pkgs.writeText "agents-egress.nft" ''
    table inet agents_egress {
      chain output {
        type filter hook output priority 0; policy drop;
        oifname "lo" accept
        oifname "${tunDevice}" meta nfproto ipv4 accept
        ip daddr 10.0.2.2 tcp dport ${toString port} accept
        ip daddr 10.0.2.2 udp dport ${toString port} accept
        ${lib.optionalString (directPort != null) ''
          # Explicit proxy clients may opt into host DIRECT; never a TUN fallback.
          ip daddr 10.0.2.2 tcp dport ${toString directPort} accept
          ip daddr 10.0.2.2 udp dport ${toString directPort} accept
        ''}
      }
      chain forward {
        type filter hook forward priority 0; policy drop;
      }
      chain input {
        type filter hook input priority 0; policy drop;
        iifname "lo" accept
        iifname "${tunDevice}" accept
        ct state established,related accept
      }
    }
  '';
  start = pkgs.writeShellScript "agents-gateway-start" ''
    set -euo pipefail
    export PATH=${
      lib.makeBinPath [
        pkgs.nftables
        pkgs.iproute2
        pkgs.coreutils
        pkgs.gawk
      ]
    }
    # Atomic default-deny rules MUST precede both the route and readiness.
    nft -f ${firewall}
    uplink=$(ip -4 route show default | awk '{for (i = 1; i < NF; i++) if ($i == "dev") {print $(i + 1); exit}}')
    test -n "$uplink"
    ip tuntap add dev ${tunDevice} mode tun
    ip addr add 198.18.0.1/15 dev ${tunDevice}
    ip link set ${tunDevice} up
    ip route replace 10.0.2.2/32 dev "$uplink"
    ip route replace default dev ${tunDevice}
    ${lib.getExe tun2socks} --device tun://${tunDevice} \
      --proxy socks5://10.0.2.2:${toString port} --interface "$uplink" &
    child=$!
    trap 'kill "$child" 2>/dev/null || true; wait "$child" || true' TERM INT EXIT
    sleep 1
    kill -0 "$child"
    touch /tmp/ready
    wait "$child"
    # No rule flush on shutdown: a surviving workload namespace stays denied.
  '';
in
pkgs.dockerTools.buildLayeredImage {
  inherit name;
  contents = [
    pkgs.bash
    pkgs.coreutils
    pkgs.iproute2
    pkgs.nftables
  ];
  extraCommands = "mkdir -p tmp; chmod 1777 tmp";
  config = {
    Entrypoint = [ start ];
    User = "0:0";
  };
}
