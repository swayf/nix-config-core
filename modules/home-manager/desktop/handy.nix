# modules/programs/handy.nix
#
# Handy: hold a shortcut, speak, and the transcript is inserted into whatever
# window had focus. Nothing is sent anywhere -- the model runs locally, and the
# only thing fetched is the model file itself, once (MODELS below).
#
# The original host runs GNOME on Wayland; KDE also uses the uinput route.
# desktop.environment selects the live modifier settings source below. Two
# of the three routes Handy has for "press a key" and "type the text" are X11
# routes, and they are dead here. What is left needs uinput, and uinput needs
# one host-level step that Home Manager cannot take (UINPUT below).
#
# WHAT HANDY LOOKS FOR. The last step of the job -- getting the text into the
# *other* application -- is done by external binaries, chosen by session type
# and looked up with `which`, so silence reads as "not installed"
# (src-tauri/src/clipboard.rs):
#
#   Wayland   wtype (skipped on KDE)  ->  dotool  ->  ydotool
#   X11       xdotool                 ->  ydotool
#   none found: enigo, in-process
#
# plus `wl-copy` on Wayland, which is how the clipboard is put back after a
# paste. `which` itself is in the wrapper below for the same reason: it is the
# one binary whose absence turns every detection above into a silent
# "not installed".
#
# WHY NOT wtype, the first name on the Wayland list. It drives
# `zwp_virtual_keyboard_manager_v1`, and mutter does not implement that
# protocol. The list of what it does implement is one array in its own build
# file, and neither the virtual keyboard nor input-method-v2 is in it:
#
#   $ sed -n '/^wayland_protocols = /,/^]/p' mutter-50.4/src/meson.build
#   ...  ['keyboard-shortcuts-inhibit', 'unstable', 1, ],
#        ['text-input-unstable-v3', 'private', ],
#        ['xdg-activation', 'staging', 1, ],  ...
#
# So wtype is deliberately *not* installed here: present, it would be picked
# first on every paste and fail every time, and the fallbacks after it would
# never be reached.
#
# The in-process fallback cannot cover for it either. Handy takes `enigo =
# "0.6.1"` with default features, and that default is `["x11rb"]` alone --
# neither its `wayland` nor its `libei_*` backend is compiled in. Under a
# Wayland session enigo talks to Xwayland, which reaches X11 clients and no
# native Wayland window.
#
# UINPUT is therefore the only route, through `dotool`: it opens /dev/uinput and
# feeds the kernel, so the compositor sees an ordinary keyboard and has no say
# in it. The node is root-only out of the box (systemd tags it with no `uaccess`
# and gives it no group):
#
#   $ ls -l /dev/uinput
#   crw------- 1 root root 10, 223 ... /dev/uinput
#
# Three commands on the host, once, exactly what dotool's own man page and its
# shipped 80-dotool.rules ask for:
#
#   echo 'KERNEL=="uinput", GROUP="input", MODE="0620", OPTIONS+="static_node=uinput"' \
#     | sudo tee /etc/udev/rules.d/80-dotool.rules
#   echo uinput | sudo tee /etc/modules-load.d/uinput.conf
#   sudo usermod -aG input $USER
#
# then reboot -- the group membership needs a new login and the rule a new
# `udevadm trigger` at minimum. The modules-load.d line is insurance rather than
# a requirement: `static_node=uinput` makes udev create the node before the
# module is loaded, and opening it autoloads the module. Afterwards:
#
#   $ ls -l /dev/uinput          # crw--w---- 1 root input ...
#   $ echo type hello | dotool   # types into the focused window
#
# Home Manager owns none of that: a udev rule, a kernel module and a group are
# root-owned state outside the profile, the same wall ./wireshark.nix hits with
# dumpcap. On NixOS the equivalent is `hardware.uinput.enable = true` plus the
# user in `users.groups.input`.
#
# $DOTOOL_XKB_LAYOUT is set on the wrapper, and it is not cosmetic. dotool turns
# characters into keycodes through an XKB keymap *it compiles itself*, with no
# knowledge of the session's; left to its own default it picked one where the
# letters sit on level 3, and every keystroke came out with the level-3
# modifier attached -- in a terminal that reads as Alt, one ESC per character:
#
#   $ echo type hello | dotool
#   ^[h^[e^[l^[l^[o
#   $ echo type hello | DOTOOL_XKB_LAYOUT=us dotool
#   hello
#
# `us` is the right value here because it is the first of this session's groups
# (`gsettings get org.gnome.desktop.input-sources sources` ->
# `[('xkb', 'us'), ('xkb', 'ru'), ('xkb', 'de')]`, switched with CapsLock via
# `grp:caps_toggle`) and the one every Latin keycode is correct in. It is set
# with `--set-default`, so a shell that exports something else still wins.
#
# PASTE METHOD: pick "Ctrl+V", not "Direct" (Handy's Settings; `Direct` is its
# Linux default). Both go through dotool, and the layout above is what makes
# either work at all -- `key ctrl+v` is looked up in the same table as a typed
# letter, so a wrong keymap adds its modifier to the paste chord too. What
# separates them is how much of the layout question is left open:
#
#   Ctrl+V   the transcript goes into the clipboard (wl-copy) and dotool sends
#            one chord. In GNOME, enable handy-focus@home-manager: the shim
#            switches this chord to Ctrl+Shift+V only in an active Kitty window,
#            where raw Ctrl+V is reserved for agent image paste. Other windows
#            keep Ctrl+V. Text of any script, Cyrillic included, is carried by
#            the clipboard, so the active group does not matter.
#   Direct   dotool types character by character against the fixed `us` above,
#            so it cannot carry a script that layout does not contain at all:
#
#              [handy_app_lib::clipboard] Using paste method: Direct
#              [handy_app_lib::clipboard] Using dotool for direct text input
#              dotool: WARNING: impossible character for layout: П
#              dotool: WARNING: impossible character for layout: р
#              ...
#
#            Latin fares better but only while the session is actually in the
#            `us` group -- CapsLock into `ru` and the same keycodes produce
#            Cyrillic. Pointing $DOTOOL_XKB_LAYOUT at `ru` only swaps which half
#            breaks, and naming both does nothing: dotool searches group 0 of
#            its keymap and no other (`// TODO support layouts other than 0`,
#            keys.go). Its man page states the general case -- "dotool may type
#            gobbledygook if it's simulating keycodes for a different keyboard
#            layout than your environment is expecting" -- and a three-group
#            session is that case by construction, since nothing tells dotool
#            which group is live at the moment it types.
#
# HOTKEY is Handy's own, through its second backend. The default one on Linux,
# `tauri-plugin-global-shortcut` (`keyboard_implementation = Tauri`), is X11 key
# grabbing -- no key arrives under Wayland at all -- and it also refuses a
# chord made of modifiers alone:
#
#   Не удалось установить ярлык: Error: Tauri shortcuts must include a main key
#   (letter, number, F-key, etc.) in addition to modifiers
#
# `handy-keys` reads /dev/input/event* itself, so the compositor is not involved
# and neither are this session's XKB options: it sees the physical keys, not
# what `altwin:ctrl_alt_win` renames them to. Modifier-only chords are a
# declared feature of it, sides included (`CTRL_RIGHT`, `OPT_RIGHT` --
# handy-keys-0.3.0/src/types/{hotkey,modifiers}.rs):
#
#   /// A hotkey definition - either a key with modifiers, or modifiers only
#   pub struct Hotkey { pub modifiers: Modifiers, pub key: Option<Key> }
#
# It is selected in Settings > Advanced > Experimental > Keyboard
# Implementation, and the shortcut here is Right Alt + Right Ctrl. The `input`
# group the uinput step above already asks for is what lets it open those nodes;
# without it, it says so and falls back to the Tauri backend
# (handy-keys-0.3.0/src/platform/linux/listener.rs):
#
#   permission denied opening N device node(s) under /dev/input. Reading
#   keyboard events requires read access to these nodes: add your user to the
#   'input' group (sudo usermod -aG input $USER) and log out and back in
#
# NOTHING HERE WRITES A GNOME CUSTOM SHORTCUT, and a `dconf.settings` block
# binding `handy --toggle-transcription` (which `tauri-plugin-single-instance`
# hands to the running process rather than starting a second copy) was removed
# once the above worked. Two reasons, both specific to this session:
# `gtk_accelerator_parse` has no way to express a chord without a main key, so
# Right Alt + Right Ctrl is not writable as an accelerator at all; and GNOME
# matches accelerators by *modifier*, not by physical key, so with
# `altwin:ctrl_alt_win` a `<Super>d` binding fires on the physical Ctrl+D and
# takes that combination away from every application on the desktop.
#
# AUTOSTART is written here, and Handy's own "Start at login" toggle has to stay
# OFF. tauri-plugin-autostart rewrites ~/.config/autostart/Handy.desktop on every
# start with the path of the running executable, which is the innermost binary,
# not either wrapper:
#
#   $ grep Exec ~/.config/autostart/Handy.desktop
#   Exec=/nix/store/...-handy-0.9.6/bin/.handy-wrapped
#
# Started that way at login, Handy had none of the environment below and none of
# nixpkgs' wrapGAppsHook4 one (WEBKIT_DISABLE_DMABUF_RENDERER among it): the
# settings window drew once and ignored every click, and no tray icon appeared.
# The entry below has its own file name because turning the toggle off makes
# Handy delete `Handy.desktop`. With the toggle on, both entries fire and the
# unwrapped copy that starts first keeps the single instance.
#
# PROXY. Models come from Hugging Face over reqwest and ureq, and both take a
# proxy from HTTPS_PROXY/HTTP_PROXY only -- no GNOME proxy settings, no option in
# Handy. Without them the download leaves directly, past mihomo, and fails. Set
# with `--set-default` like the layout above; NO_PROXY keeps a local
# post-processing endpoint (Ollama on 127.0.0.1) off the proxy.
#
# GPU. The Linux build enables transcribe-cpp's Vulkan backend (`features =
# ["dynamic-backends", "vulkan"]` in src-tauri/Cargo.toml; `lib/libggml-vulkan.so`
# ships in the package next to the per-microarchitecture `libggml-cpu-*.so`).
# Vulkan finds a driver only because /run/opengl-driver exists -- the loader has
# that path compiled in (`strings libvulkan.so.1 | grep opengl-driver` ->
# `/run/opengl-driver/share`) and mesa, which `targets.genericLinux.gpu` links
# there, supplies `share/vulkan/icd.d`. See ../../home.nix. On this host it
# resolves, and Handy will say so without starting the GUI:
#
#   $ handy --list-devices
#   transcribe-cpp compute devices:
#     index=0 kind=vulkan name=Intel(R) Iris(R) Xe Graphics (ADL GT2) vram=47973MB
#     index=1 kind=cpu name=12th Gen Intel(R) Core(TM) i7-12700H vram=63964MB
#
# A host where `sudo non-nixos-gpu-setup` has not been run lists the CPU line
# only: transcription still works, just slower.
#
# MODELS are not in the store and are not installed by this module: Handy
# downloads the one that is picked in its UI on first use, into
# ~/.local/share/com.pais.handy/models, and reuses the shared Hugging Face cache
# (HF_HOME, else ~/.cache/huggingface/hub) for repositories it pulls from there
# (src-tauri/src/managers/model.rs). So a switch never fetches a model, and
# `nix-collect-garbage` never removes one. The transcription history database
# and its recordings sit in the same app-data directory.
#
# TRAY. The icon is a StatusNotifier item through libayatana-appindicator, which
# GNOME does not host on its own -- without the AppIndicator extension there is
# no tray icon and no tray menu. Not fatal: running `handy` again raises the
# window, which is the only thing the menu is needed for.
{ httpProxy, xkbLayout }:
{
  config,
  lib,
  pkgs,
  ...
}:

let
  # dotool names modifiers the way an *unmodified* keymap does and presses the
  # matching evdev code; the session then re-reads that code through its own XKB
  # options. This one sets `altwin:ctrl_alt_win`, which rotates the three
  # modifier keys (xkeyboard-config 2.48, share/X11/xkb/symbols/altwin):
  #
  #   key <LCTL> {[ Super_L   ]};   physical Ctrl -> Super
  #   key <LALT> {[ Control_L ]};   physical Alt  -> Control
  #   key <LWIN> {[ Alt_L     ]};   physical Win  -> Alt
  #
  # (its one-line description in rules/base.lst, "Ctrl is mapped to Alt, Alt to
  # Win", describes the *keys*, not the resulting modifiers -- the symbols file
  # above is what the compositor loads.)
  #
  # So `key ctrl+v`, which Handy hardcodes for its Ctrl+V paste method
  # (`send_key_combo_via_dotool`, src-tauri/src/clipboard.rs), reaches the
  # focused window as Super+V and pastes nothing. Renaming the modifier in the
  # chord to the key that *produces* it here is the whole fix, and it has to
  # happen between Handy and dotool because neither end is configurable:
  # Handy writes a fixed string, and dotool compiles its keymap from layout and
  # variant only -- $XKB_DEFAULT_OPTIONS never reaches it, because it passes an
  # empty (not null) options field and libxkbcommon takes that literally
  # ("Options can be empty, so respect that if passed in", context-priv.c).
  #
  # `type` lines pass through untouched: their payload is the transcript, which
  # may itself contain the word "ctrl".
  #
  # Read the selected desktop's LIVE configuration on every call. In KDE,
  # never consult old GNOME dconf: a preserved HOME can still contain the remap
  # even after it has been disabled in KDE. A missing setting passes through.
  dotool = pkgs.writeShellApplication {
    name = "dotool";
    runtimeInputs = [
      pkgs.gnused
      pkgs.coreutils
    ]
    ++ (
      if config.desktop.environment == "kde" then
        [ pkgs.kdePackages.kconfig ]
      else
        [
          pkgs.dconf
          pkgs.glib
        ] # gdbus queries the GNOME focus extension
    );
    text = ''
      ${
        if config.desktop.environment == "kde" then
          ''
            opts=$(kreadconfig6 --file kxkbrc --group Layout --key Options 2>/dev/null || true)
          ''
        else
          ''
            opts=$(dconf read /org/gnome/desktop/input-sources/xkb-options 2>/dev/null || true)
          ''
      }

      real_dotool=${lib.escapeShellArg (lib.getExe' pkgs.dotool "dotool")}
      query_kitty=${if config.desktop.environment == "gnome" then "1" else "0"}
    ''
    + builtins.readFile ./handy/dotool.sh;
  };

  # Found by name at run time, never by store path -- see the `which` note
  # above. wtype is absent on purpose; `dotool` here is the shim above, and the
  # real one is reachable only through it.
  inputTools = [
    dotool
    pkgs.which
    pkgs.xdotool # X11 sessions, where Handy never reaches dotool
    pkgs.wl-clipboard # wl-copy, for restoring the clipboard after a paste
  ];

  handy = pkgs.symlinkJoin {
    name = "handy-${pkgs.handy.version}";
    paths = [ pkgs.handy ];
    nativeBuildInputs = [ pkgs.makeBinaryWrapper ];

    # `bin/handy` is already a wrapper -- wrapGAppsHook4 built it to set
    # GST_PLUGIN_SYSTEM_PATH_1_0, ALSA_PLUGIN_DIR and the rest. This wraps that
    # wrapper; the environment it sets is untouched.
    #
    # A wrapper rather than `overrideAttrs`: the package is a Rust/Tauri build
    # with a vendored Cargo tree and a bun frontend, and any change to its
    # derivation throws away the binary cache for all of it.
    postBuild = ''
      wrapProgram $out/bin/handy \
        --prefix PATH : ${lib.makeBinPath inputTools} \
        --set-default DOTOOL_XKB_LAYOUT ${lib.escapeShellArg xkbLayout} \
        --set-default HTTPS_PROXY ${httpProxy} \
        --set-default HTTP_PROXY ${httpProxy} \
        --set-default NO_PROXY localhost,127.0.0.1,::1
    '';

    inherit (pkgs.handy) meta;
  };
in
{
  home.packages = [ handy ];

  # Enable manually after GNOME discovers the extension (logout/login on
  # Wayland). Do not overwrite the user's enabled-extensions list.
  xdg.dataFile."gnome-shell/extensions/handy-focus@home-manager" =
    lib.mkIf (config.desktop.environment == "gnome")
      {
        source = ./handy/focus-extension;
        recursive = true;
      };

  xdg.configFile."autostart/handy-home-manager.desktop".text = ''
    [Desktop Entry]
    Type=Application
    Name=Handy
    Exec=${handy}/bin/handy
    Terminal=false
  '';
}
