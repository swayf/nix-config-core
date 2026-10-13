{
  lib,
  buildGoModule,
  fetchFromGitHub,
}:
buildGoModule rec {
  pname = "xray-exporter";
  version = "1.0.0";
  src = fetchFromGitHub {
    owner = "podocarp";
    repo = "xray-exporter";
    rev = "v${version}";
    hash = "sha256-Ocex/D1OYOlQ2BZ835qrlXaEuMdXaglIPpVzfo0OZ5g=";
  };
  vendorHash = "sha256-yRxy44SnEFa7yOJyiOgFTk+Z4s5HOJ4cMjcf8VTTfQk=";
  ldflags = [
    "-s"
    "-w"
    "-X main.buildVersion=${version}"
  ];
  meta = {
    description = "Prometheus exporter for Xray StatsService";
    homepage = "https://github.com/podocarp/xray-exporter";
    license = lib.licenses.mit;
    mainProgram = "xray-exporter";
  };
}
