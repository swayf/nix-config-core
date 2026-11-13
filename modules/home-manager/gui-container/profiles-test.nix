# Local fixtures; no host profiles, credentials or public network required.
# profiles and session build with the default pin. image needs the caller's
# package set with its browsers; use it with test-gui.py --profiles.
{
  pkgs ? import (builtins.getFlake ("path:" + toString ../../..)).inputs.nixpkgs2605 {
    system = builtins.currentSystem;
    config.allowUnfree = true;
  },
}:
let
  inherit (pkgs) lib;
  browserProfiles = {
    smoke-firefox = {
      browser = "firefox";
      label = "Firefox · Тест <&> \"quoted\"";
      order = 10;
      icon = null;
      color = "#ff9e64";
      extraArgs = [ "--width=900" ];
      firefoxSettings = {
        "browser.startup.homepage" = "about:blank";
        "browser.shell.checkDefaultBrowser" = false;
      };
    };
    smoke-brave = {
      browser = "brave";
      label = "Brave · Тест";
      order = 20;
      icon = null;
      color = "#7dcfff";
      extraArgs = [
        "--no-first-run"
        "--no-default-browser-check"
      ];
      firefoxSettings = { };
    };
    smoke-chrome = browserProfiles.smoke-brave // {
      browser = "chrome";
      label = "Google Chrome · Тест";
    };
  };
  mockBrowser =
    name:
    pkgs.writeShellScriptBin name ''
      printf '%s\0' "$@" > "$TEST_ARGS"
    '';
  desktop = import ./applications.nix {
    pkgs = pkgs // {
      firefox = mockBrowser "firefox";
      brave = mockBrowser "brave";
      google-chrome = mockBrowser "google-chrome-stable";
    };
    inherit lib browserProfiles;
  };
  adversarialDesktop = import ./applications.nix {
    pkgs = pkgs // {
      firefox = mockBrowser "firefox";
      brave = mockBrowser "brave";
    };
    inherit lib;
    browserProfiles = {
      quotes = browserProfiles.smoke-firefox // {
        label = "<&> \"quoted\" \\ path";
        firefoxSettings = {
          "test.quoted\"key" = "line1\nline2\"; ); //";
          "test.integer" = 42;
          "test.boolean" = true;
        };
        extraArgs = [
          "space separated"
          "quote'arg"
          "$(touch /tmp/gcnt-should-not-exist)"
        ];
      };
    };
  };
  entries = pkgs.writeText "gui-host-entries.json" (builtins.toJSON (
    import ./host-desktop.nix {
      inherit lib pkgs browserProfiles;
      gcnt = "/mock/gcnt";
    }
  ));
in
{
  profiles =
    pkgs.runCommand "gui-container-profile-tests" { nativeBuildInputs = [ pkgs.python3 ]; }
      ''
        export HOME="$TMPDIR/home" TEST_ARGS="$TMPDIR/args"
        mkdir -p "$HOME"
        ${desktop.profileLaunchers.smoke-firefox}/bin/gcnt-profile-smoke-firefox --headless 'extra argument'
        python3 - <<'PY'
        import json, os
        from pathlib import Path
        home = Path(os.environ['HOME'])
        args = Path(os.environ['TEST_ARGS']).read_bytes().split(b'\0')[:-1]
        profile = home / '.local/share/gcnt/browser-profiles/smoke-firefox'
        assert args == [b'--width=900', b'--no-remote', b'--profile', str(profile).encode(), b'--headless', b'extra argument'], args
        assert profile.is_dir()
        prefs = profile / 'user.js'
        assert not prefs.is_symlink()
        assert 'user_pref("browser.shell.checkDefaultBrowser", false);' in prefs.read_text()
        assert 'user_pref("browser.startup.homepage", "about:blank");' in prefs.read_text()
        assert (profile.stat().st_mode & 0o777) == 0o700
        (profile / 'cookies-marker').write_text('preserved')
        prefs.write_text('stale configuration')
        PY
        ${desktop.profileLaunchers.smoke-firefox}/bin/gcnt-profile-smoke-firefox
        ${desktop.profileLaunchers.smoke-brave}/bin/gcnt-profile-smoke-brave --version
        python3 - <<'PY'
        import json, os
        from pathlib import Path
        home = Path(os.environ['HOME']) / '.local/share/gcnt/browser-profiles'
        assert (home / 'smoke-firefox/cookies-marker').read_text() == 'preserved'
        assert 'stale configuration' not in (home / 'smoke-firefox/user.js').read_text()
        assert not list((home / 'smoke-firefox').glob('.user.js.*'))
        args = Path(os.environ['TEST_ARGS']).read_bytes().split(b'\0')[:-1]
        assert args == [b'--ozone-platform=wayland', b'--no-first-run', b'--no-default-browser-check',
                        ('--user-data-dir=' + str(home / 'smoke-brave')).encode(), b'--version'], args
        assert not (home / 'smoke-brave/user.js').exists()
        entries = json.loads(Path('${entries}').read_text())
        assert entries['gui-container']['exec'] == '/mock/gcnt/bin/gcnt exec foot'
        assert entries['gui-container-firefox']['exec'] == '/mock/gcnt/bin/gcnt firefox'
        assert entries['gui-container-brave']['exec'] == '/mock/gcnt/bin/gcnt brave'
        assert entries['gui-container-chrome']['exec'] == '/mock/gcnt/bin/gcnt chrome'
        assert Path(entries['gui-container-chrome']['icon']).is_file()
        assert entries['gui-container-profile-smoke-chrome']['icon'] == entries['gui-container-chrome']['icon']
        entry = entries['gui-container-profile-smoke-firefox']
        assert entry['name'] == 'GCNT · Firefox · Тест <&> "quoted"'
        assert entry['exec'] == '/mock/gcnt/bin/gcnt exec gcnt-profile-smoke-firefox'
        assert entry['icon'].endswith('/apps/firefox.png') and not entry['terminal']
        assert Path(entry['icon']).is_file()
        assert entries['gui-container-profile-smoke-brave']['icon'].endswith('/apps/brave-browser.png')
        assert Path(entries['gui-container-profile-smoke-brave']['icon']).is_file()
        assert Path(entries['gui-container']['icon']).is_file()
        PY
        ${desktop.chrome}/bin/chrome '--new-window' 'https://example.invalid/path with spaces' ""
        python3 - <<'PY'
        import os
        from pathlib import Path
        args = Path(os.environ['TEST_ARGS']).read_bytes().split(b'\0')[:-1]
        assert args == [b'--ozone-platform=wayland', b'--new-window', b'https://example.invalid/path with spaces', b""], args
        PY
        ${desktop.profileLaunchers.smoke-chrome}/bin/gcnt-profile-smoke-chrome --version
        python3 - <<'PY'
        import os
        from pathlib import Path
        profile = Path(os.environ['HOME']) / '.local/share/gcnt/browser-profiles/smoke-chrome'
        args = Path(os.environ['TEST_ARGS']).read_bytes().split(b'\0')[:-1]
        assert args == [b'--ozone-platform=wayland', b'--no-first-run', b'--no-default-browser-check',
                        ('--user-data-dir=' + str(profile)).encode(), b'--version'], args
        assert profile.is_dir() and not (profile / 'user.js').exists()
        PY
        # Startup cleanup covers default/named Chromium browsers, never Firefox or data.
        python3 - <<'PY'
        import os
        from pathlib import Path
        home = Path(os.environ['HOME'])
        for relative in ('.config/BraveSoftware/Brave-Browser',
                         '.config/google-chrome',
                         '.local/share/gcnt/browser-profiles/smoke-brave',
                         '.local/share/gcnt/browser-profiles/smoke-chrome',
                         '.local/share/gcnt/browser-profiles/smoke-firefox'):
            profile = home / relative
            profile.mkdir(parents=True, exist_ok=True)
            (profile / 'cookies-marker').write_text('preserved')
            for name, target in (('SingletonLock', 'old-container-36'),
                                 ('SingletonCookie', '123456'),
                                 ('SingletonSocket', '/tmp/old-brave/SingletonSocket')):
                (profile / name).symlink_to(target)
        PY
        ${desktop.resetChromiumLocks}
        ${desktop.resetChromiumLocks}
        python3 - <<'PY'
        import os
        from pathlib import Path
        home = Path(os.environ['HOME'])
        for relative in ('.config/BraveSoftware/Brave-Browser',
                         '.config/google-chrome',
                         '.local/share/gcnt/browser-profiles/smoke-brave',
                         '.local/share/gcnt/browser-profiles/smoke-chrome'):
            profile = home / relative
            assert (profile / 'cookies-marker').read_text() == 'preserved'
            for name in ('SingletonLock', 'SingletonCookie', 'SingletonSocket'):
                assert not os.path.lexists(profile / name)
        firefox = home / '.local/share/gcnt/browser-profiles/smoke-firefox'
        assert (firefox / 'SingletonLock').is_symlink()
        # Unexpected regular files/directories must not be deleted.
        brave = home / '.local/share/gcnt/browser-profiles/smoke-brave'
        (brave / 'SingletonLock').write_text('not a symlink')
        (brave / 'SingletonSocket').mkdir()
        PY
        ${desktop.resetChromiumLocks}
        # Normal browser launches must leave current locks untouched.
        ln -s current-container-42 "$HOME/.local/share/gcnt/browser-profiles/smoke-brave/SingletonCookie"
        ${desktop.profileLaunchers.smoke-brave}/bin/gcnt-profile-smoke-brave --version
        python3 - <<'PY'
        import os
        from pathlib import Path
        brave = Path(os.environ['HOME']) / '.local/share/gcnt/browser-profiles/smoke-brave'
        assert (brave / 'SingletonLock').read_text() == 'not a symlink'
        assert (brave / 'SingletonSocket').is_dir()
        assert os.readlink(brave / 'SingletonCookie') == 'current-container-42'
        PY
        ${adversarialDesktop.profileLaunchers.quotes}/bin/gcnt-profile-quotes
        python3 - <<'PY'
        import json, os
        from pathlib import Path
        args = Path(os.environ['TEST_ARGS']).read_bytes().split(b'\0')[:-1]
        assert args[:3] == [b'space separated', b"quote'arg", b'$(touch /tmp/gcnt-should-not-exist)'], args
        prefs = Path(os.environ['HOME']) / '.local/share/gcnt/browser-profiles/quotes/user.js'
        values = {}
        for line in prefs.read_text().splitlines():
            if line:
                key, value = json.loads('[' + line[len('user_pref('):-2] + ']')
                values[key] = value
        assert values == {'test.quoted"key': 'line1\nline2"; ); //', 'test.integer': 42, 'test.boolean': True}, values
        PY
        touch "$out"
      '';
  session = pkgs.runCommand "gui-container-session-tests" {
    nativeBuildInputs = [ pkgs.python3 pkgs.coreutils pkgs.bash pkgs.shellcheck pkgs.dbus ];
    TEST_SESSION = pkgs.writeShellScript "gui-session-test" (builtins.readFile ./session.sh);
    TEST_EXEC = pkgs.writeShellScript "gui-exec-test" (builtins.readFile ./exec.sh);
    TEST_DBUS_RUN = "${pkgs.dbus}/bin/dbus-run-session";
    TEST_DBUS_CONFIG = "${pkgs.dbus}/share/dbus-1/session.conf";
    TEST_DBUS_SEND = "${pkgs.dbus}/bin/dbus-send";
  } ''
    shellcheck -s bash ${./session.sh} ${./exec.sh} ${./launcher.sh}
    python3 ${./test-session.py}
    touch "$out"
  '';
  image = import ./image.nix {
    inherit pkgs lib browserProfiles;
    gpu = false;
    packages = [ ];
  };
}
