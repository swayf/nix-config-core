{ buildEnv, python3Minimal, openssl, cacert }:
# The upstream minimal kexec installer does not expose python3/openssl on PATH.
# Copy this public closure into its temporary Nix store before inventory/TLS checks.
buildEnv {
  name = "vps-installer-tools";
  paths = [ python3Minimal openssl cacert ];
  pathsToLink = [ "/bin" "/etc/ssl/certs" ];
}
