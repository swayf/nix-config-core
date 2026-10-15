# Standalone selected recipe build: no private checkout, Herdr process or real HOME.
{ pkgs, factory ? import ../modules/home-manager/agents-container/radar-package.nix }:
let
  radar = factory { inherit pkgs; };
  # Verify dependency injection without instantiating a different package set.
  probe = factory { pkgs = {
    fetchzip = args: args;
    stdenvNoCC.mkDerivation = args: args;
    nodejs = "caller-node";
  }; };
in
assert builtins.functionArgs factory == { pkgs = false; };
assert probe.nativeBuildInputs == [ "caller-node" ];
assert probe.src.hash == radar.src.outputHash;
pkgs.runCommand "agents-radar-package-check" {
  nativeBuildInputs = [ pkgs.python3 ];
} ''
  export HOME="$TMPDIR/home" XDG_CACHE_HOME="$TMPDIR/cache"
  mkdir -p "$HOME" "$XDG_CACHE_HOME" "$out"
  # Building radar runs node --test and tools/check.js inside its isolated checkPhase.
  test -d ${radar}/lib
  for font in HerdrAgentIconsMax-Regular JetBrainsMonoHerdr-Regular; do
    test -s "${radar}/dist/$font.ttf"
    cmp "${radar.src}/dist/$font.ttf" "${radar}/dist/$font.ttf"
  done
  for notice in LICENSE THIRD_PARTY_NOTICES.md dist/OFL.txt; do
    test -s "${radar}/$notice"
    cmp "${radar.src}/$notice" "${radar}/$notice"
  done
  python3 - <<'PY'
  from pathlib import Path
  import struct
  root = Path('${radar}')
  for font in sorted((root / 'dist').glob('*.ttf')):
      data = font.read_bytes()
      assert data[:4] == b'\x00\x01\x00\x00', font
      def checksum(raw):
          raw += b'\0' * (-len(raw) % 4)
          return sum(struct.unpack('>' + 'I' * (len(raw) // 4), raw)) & 0xffffffff
      assert checksum(data) == 0xB1B0AFBA, font
      count = struct.unpack_from('>H', data, 4)[0]
      tables = {}
      for i in range(count):
          tag, expected, offset, size = struct.unpack_from('>4sIII', data, 12 + 16 * i)
          assert offset + size <= len(data), (font, tag)
          raw = data[offset:offset + size]
          if tag == b'head':
              raw = raw[:8] + b'\0' * 4 + raw[12:]
          assert checksum(raw) == expected, (font, tag)
          tables[tag] = (offset, size)
      assert {b'cmap', b'head', b'name', b'maxp', b'glyf', b'loca'} <= tables.keys(), font
      assert struct.unpack_from('>H', data, tables[b'maxp'][0] + 4)[0] > 0, font
      assert struct.unpack_from('>H', data, tables[b'name'][0] + 2)[0] > 0, font
      assert struct.unpack_from('>HH', data, tables[b'cmap'][0])[0] == 0, font
      assert struct.unpack_from('>HH', data, tables[b'cmap'][0])[1] > 0, font
  fixture = (root / 'test/agent-groups.test.js').read_text()
  assert 'synthetic-session' in fixture
  # These assertions exercise Radar's side only; callers MUST supply paired Herdr patches.
  for token in ('group_id', 'group_stale', 'radar_status'):
      assert token in fixture, token
  PY
  printf '%s\n' '${radar}' > "$out/package"
''
