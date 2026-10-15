# Keep container-specific Herdr fixes together; do not replace a live server.
{ herdr, lld, resources }:
herdr.overrideAttrs (old: {
  # GNU ld.bfd can reject Zig-built libghostty-vt unwind tables. Use lld
  # rather than disabling unwind metadata or changing the global toolchain.
  nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [ lld ];
  env = (old.env or { }) // {
    RUSTFLAGS = ((old.env or { }).RUSTFLAGS or "") + " -C link-arg=-fuse-ld=lld";
  };

  # Preserve DECSET 1016's pixel-coordinate contract when the outer terminal
  # only supplies cells (including focus/geometry fallback in Herdr 0.9.1).
  # Otherwise terminal-browser interprets cell indices as top-left pixels.
  patches = (old.patches or [ ]) ++ [
    resources."herdr-pixel-mouse.patch"
    resources."herdr-sound-bridge.patch"
    resources."herdr-agent-groups.patch"
    resources."herdr-sidebar-status.patch"
  ];

  # Upstream disables its host-dependent integration suite in nixpkgs. These
  # unit tests cover mouse encoding, the sound bridge and interactive groups.
  doCheck = true;
  cargoCheckType = "debug";
  cargoTestFlags = [ "--bin" "herdr" ];
  checkFlags = [ "ghostty_mouse" "sound_bridge" "client::shell" "ui::sidebar" "--nocapture" ];

  # The clipboard bridge exposes neither display: use its synchronous xclip
  # shim when the clipboard socket is mounted (text copy/paste and image paste).
  postPatch = (old.postPatch or "") + ''
    cp ${resources."herdr-sound-bridge.rs"} src/sound_bridge.rs
    cp ${resources."herdr-agent-groups.rs"} src/client/shell/agent_groups.rs
    substituteInPlace src/platform/linux.rs \
      --replace-fail 'std::env::var_os("DISPLAY").is_some()' \
        'std::env::var_os("DISPLAY").is_some() || std::fs::metadata("/run/agents-clipboard/socket").map(|m| std::os::unix::fs::FileTypeExt::is_socket(&m.file_type())).unwrap_or(false)'
  '';
})
