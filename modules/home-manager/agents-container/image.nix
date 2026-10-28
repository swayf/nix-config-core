{
  pkgs,
  lib,
  username,
  software,
  imageName,
  timeZone,
  projectDirectoryName,
  minimumFreePids ? 256,
  webLinks ? false,
}:
assert builtins.match "[a-z_][a-z0-9_-]*" username != null;
assert builtins.match "[a-z0-9][a-z0-9./_-]*" imageName != null;
assert builtins.match "[A-Za-z0-9_+/-]+" timeZone != null && !lib.hasPrefix "/" timeZone;
assert builtins.match "[A-Za-z0-9_-]+" projectDirectoryName != null;
let
  home = "/home/${username}";
  buildUsers = lib.range 1 16;
  # libcap headers enable shadow's setuid-to-caller + keep-capabilities path.
  # Without it, newuidmap stays euid 0 and requires SYS_ADMIN over the child's
  # namespace. We deliberately do not grant SYS_ADMIN to the workspace.
  shadow = pkgs.shadow.overrideAttrs (old: {
    buildInputs = old.buildInputs ++ [ pkgs.libcap ];
  });
  graphics = pkgs.buildEnv {
    name = "agents-mesa";
    paths = [
      pkgs.mesa
      pkgs.libglvnd
      pkgs.intel-media-driver
    ];
    pathsToLink = [
      "/lib"
      "/share"
    ];
  };
  # Podman's default init would retain the same ambient UID-changing caps as
  # the original entrypoint. Clear them before PID 1 as well as before sessions.
  init = pkgs.writeShellScript "agents-init" ''
    exec ${pkgs.util-linux}/bin/setpriv --inh-caps=-all --ambient-caps=-all \
      ${pkgs.catatonit}/bin/catatonit "$@"
  '';
  sessionEntrypoint = pkgs.writeShellScript "agents-session" ''
    set -euo pipefail
    mkdir -p "$XDG_RUNTIME_DIR" "$HOME/.local/bin"
    chmod 700 "$XDG_RUNTIME_DIR"
    ${software.homeInit}
    if [[ $# == 2 && $1 == /bin/sleep && $2 == infinity ]]; then
      # Fresh workspace /tmp: admission and home initialization both completed.
      : > /tmp/agents-workspace-ready
    fi
    exec "$@"
  '';
  # Load secrets before homeInit and each agents shell/exec invocation. Values
  # never become Docker Env entries, Podman arguments or shell source code.
  entrypoint = pkgs.writeShellScript "agents-entrypoint" ''
    set -euo pipefail
    source ${./process-preflight.sh}
    agents_process_preflight ${toString minimumFreePids}
    # Keep SETUID/SETGID only in the bounding set for newuidmap/newgidmap.
    # Ambient capabilities on the agent would let it impersonate daemon root.
    exec ${pkgs.util-linux}/bin/setpriv --inh-caps=-all --ambient-caps=-all \
      ${pkgs.python3}/bin/python3 ${./secret-environment.py} exec \
      /run/agents-secret-environment/environment.json ${sessionEntrypoint} "$@"
  '';
  clipboard = pkgs.runCommand "agents-clipboard-tools" { } ''
    mkdir -p "$out/bin"
    for tool in wl-copy wl-paste xclip; do
      cat > "$out/bin/$tool" <<EOF
    #!${pkgs.bash}/bin/bash
    exec ${pkgs.python3}/bin/python3 ${./clipboard.py} $tool "\$@"
    EOF
      chmod +x "$out/bin/$tool"
    done
    cat > "$out/bin/clip" <<'EOF'
    #!${pkgs.bash}/bin/bash
    case "''${1:-}" in
      copy) shift; exec wl-copy "$@" ;;
      paste) shift; exec wl-paste --no-newline "$@" ;;
      *) echo 'Usage: clip {copy|paste}' >&2; exit 2 ;;
    esac
    EOF
    chmod +x "$out/bin/clip"
  '';
  webLinkOpener = pkgs.writeShellApplication {
    name = "xdg-open";
    text = ''
      exec ${pkgs.python3}/bin/python3 -B ${./web-links.py} open "$@"
    '';
  };
  etc = pkgs.runCommand "agents-etc" { } ''
    mkdir -p "$out/etc/containers" "$out/etc/nix" "$out/etc/ssl/certs"
    cat > "$out/etc/passwd" <<'EOF'
    root:x:0:0:root:/root:/bin/bash
    ${username}:x:1000:1000:Developer:${home}:/bin/bash
    nobody:x:65534:65534:nobody:/var/empty:/bin/false
    ${lib.concatMapStringsSep "\n" (n: "nixbld${toString n}:x:${toString (30000 + n)}:30000:Nix build user:/var/empty:/bin/false") buildUsers}
    EOF
    cat > "$out/etc/group" <<'EOF'
    root:x:0:
    ${username}:x:1000:
    nogroup:x:65534:
    nixbld:x:30000:${lib.concatMapStringsSep "," (n: "nixbld${toString n}") buildUsers}
    EOF
    # The outer keep-id namespace has 65537 IDs (host user + 65536 subuids).
    # Exclude root 0 and developer 1000; the remaining 65535 plus the developer
    # cover inner IDs 0..65535, including the common nobody/nogroup 65534.
    printf '${username}:1:999\n${username}:1001:64536\n' > "$out/etc/subuid"
    cp "$out/etc/subuid" "$out/etc/subgid"
    echo 'hosts: files dns' > "$out/etc/nsswitch.conf"
    echo 'root:!:1::::::' > "$out/etc/shadow"
    echo '${username}:!:1::::::' >> "$out/etc/shadow"
    chmod 600 "$out/etc/shadow"
    ln -s ${pkgs.tzdata}/share/zoneinfo/${timeZone} "$out/etc/localtime"
    echo ${timeZone} > "$out/etc/timezone"
    ln -s ${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt "$out/etc/ssl/certs/ca-certificates.crt"
    cat > "$out/etc/nix/nix.conf" <<'EOF'
    experimental-features = nix-command flakes
    build-users-group = nixbld
    allowed-users = ${username}
    trusted-users = root
    sandbox = false
    accept-flake-config = false
    !include /run/agents-nix-auth/access-tokens.conf
    extra-substituters = https://cache.numtide.com
    extra-trusted-public-keys = niks3.numtide.com-1:DTx8wZduET09hRmMtKdQDxNNthLQETkc/yaX7M4qK0g=
    EOF
    cat > "$out/etc/containers/storage.conf" <<'EOF'
    [storage]
    driver = "vfs"
    runroot = "/tmp/containers-run"
    graphroot = "${home}/.local/share/containers/storage"
    EOF
    cat > "$out/etc/containers/containers.conf" <<'EOF'
    [containers]
    netns = "slirp4netns"
    cgroups = "disabled"
    [engine]
    cgroup_manager = "cgroupfs"
    events_logger = "file"
    runtime = "crun"
    EOF
    cat > "$out/etc/containers/policy.json" <<'EOF'
    {"default":[{"type":"insecureAcceptAnything"}]}
    EOF
    cat > "$out/etc/containers/registries.conf" <<'EOF'
    unqualified-search-registries = ["docker.io"]
    EOF
    cat > "$out/etc/profile" <<'EOF'
    export PATH="$HOME/.nix-profile/bin:$HOME/.local/bin:/run/wrappers/bin:/bin"
    EOF
    ${lib.concatStringsSep "\n" (
      lib.mapAttrsToList (name: content: ''
        install -Dm644 ${pkgs.writeText (baseNameOf name) content} "$out/etc/${name}"
      '') software.etcFiles
    )}
  '';
  root = pkgs.buildEnv {
    name = "agents-root";
    paths = software.packages ++ [
      etc
      clipboard
      pkgs.bashInteractive
      pkgs.coreutils
      pkgs.findutils
      pkgs.gnugrep
      pkgs.gnused
      pkgs.gnutar
      pkgs.gzip
      pkgs.which
      pkgs.procps
      pkgs.util-linux
      pkgs.nix
      pkgs.podman
      pkgs.slirp4netns
      pkgs.fuse-overlayfs
      shadow
      pkgs.iproute2
      pkgs.dnsutils
      pkgs.cacert
      pkgs.tzdata
      pkgs.strace
    ] ++ lib.optional webLinks webLinkOpener;
    pathsToLink = [
      "/bin"
      "/etc"
      "/share"
    ];
    # Keep drivers and the entrypoint in the seeded, private Nix store too.
    postBuild = ''
      mkdir -p "$out/opt/agents" "$out/run"
      ln -s ${entrypoint} "$out/opt/agents/entrypoint"
      ln -s ${init} "$out/opt/agents/init"
      ln -s ${graphics} "$out/run/opengl-driver"
    '';
  };
  image = pkgs.dockerTools.buildLayeredImage {
    name = imageName;
    # dockerTools derives the tag from the whole image configuration, including
    # Env and the setuid helper layer, not just the package closure.
    contents = [ root ];
    # /nix is supplied by the private store seeded from `root` in launcher.sh.
    # Keep the root's links/configuration and setuid helpers in the image, but
    # avoid importing and UID-remapping a second copy of the package closure.
    includeStorePaths = false;
    extraCommands = ''
      mkdir -p tmp var/tmp ${lib.removePrefix "/" home} root run/wrappers/bin usr/bin
      chmod 1777 tmp var/tmp
      ln -s /bin/env usr/bin/env
      cp ${shadow}/bin/newuidmap run/wrappers/bin/newuidmap
      cp ${shadow}/bin/newgidmap run/wrappers/bin/newgidmap
      # Podman reads account files before bind-mounting the private /nix.
      # shadow also opens subordinate-ID files with O_NOFOLLOW. Keep these
      # files directly in the image instead of buildEnv's store symlinks.
      for account_file in passwd group shadow subuid subgid; do
        rm "etc/$account_file"
        cp "${etc}/etc/$account_file" "etc/$account_file"
      done
    '';
    fakeRootCommands = ''
      chown -R 1000:1000 ./home
      chmod 4755 ./run/wrappers/bin/newuidmap ./run/wrappers/bin/newgidmap
    '';
    config = {
      User = "1000:1000";
      WorkingDir = "${home}/${projectDirectoryName}";
      Entrypoint = [ "/opt/agents/entrypoint" ];
      Cmd = [
        "/bin/sleep"
        "infinity"
      ];
      Env = [
        "HOME=${home}"
        "USER=${username}"
        "PATH=${home}/.nix-profile/bin:${home}/.local/bin:/run/wrappers/bin:/bin"
        "XDG_RUNTIME_DIR=/tmp/runtime-1000"
        "NIX_REMOTE=daemon"
        "NIX_SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt"
        "SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt"
        "TERM=xterm-256color"
        "LANG=C.UTF-8"
        "TZDIR=${pkgs.tzdata}/share/zoneinfo"
        "LIBGL_DRIVERS_PATH=${graphics}/lib/dri"
        "__EGL_VENDOR_LIBRARY_DIRS=${graphics}/share/glvnd/egl_vendor.d"
      ]
      ++ lib.mapAttrsToList (name: value: "${name}=${value}") software.environment;
    };
  };
in
{
  inherit image root init;
}
