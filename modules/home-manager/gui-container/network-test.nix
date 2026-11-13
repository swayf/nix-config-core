# Local-only SOCKS fixtures: no VLESS credentials or Internet destinations.
# pkgs is the caller's package set: the gateway's tun2socks patch follows the
# caller pin, and the core's own nixpkgs2605 is not a supported gateway pin.
#   nix build --impure --expr 'import ./network-test.nix { pkgs = CALLER_PKGS; }' gateway workload
#   python3 test-network.py --strict GATEWAY WORKLOAD
{ pkgs }:
{
  gateway = import ../container-gateway.nix {
    inherit pkgs;
    inherit (pkgs) lib;
    port = 12081;
    name = "localhost/hm-gui-test-gateway";
    tunDevice = "gui-tun";
  };
  workload = pkgs.dockerTools.buildLayeredImage {
    name = "localhost/hm-gui-test-workload";
    contents = [ pkgs.busybox ];
    config.User = "1000:1000";
  };
}
