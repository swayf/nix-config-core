{ writeShellApplication, python3, openssl }:
writeShellApplication {
  name = "xray-render";
  runtimeInputs = [ python3 openssl ];
  text = ''
    exec python3 ${./xray-render.py} "$@"
  '';
}
