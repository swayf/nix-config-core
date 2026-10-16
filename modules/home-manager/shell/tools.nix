# Generic desktop tools, with caller package set and policy.
{ pkgs, lib, environment, nixodooFlake }:
let
  # Ghostscript's own -dPDFSETTINGS presets. A list, not an attribute set,
  # because the order is the point -- they run from smallest to largest and an
  # attribute set would sort them alphabetically. Kept as data so the help text
  # and the argument check cannot drift apart.
  pdfPresets = [
    {
      name = "/screen";
      description = "Низкое качество (72 dpi). Минимальный размер, только для экрана.";
    }
    {
      name = "/ebook";
      description = "Среднее качество (150 dpi). Идеальный баланс для чтения (стандарт).";
    }
    {
      name = "/printer";
      description = "Высокое качество (300 dpi). Для домашней печати.";
    }
    {
      name = "/prepress";
      description = "Максимальное качество (300 dpi). Сохранение цветов, для типографии.";
    }
    {
      name = "/default";
      description = "Почти без сжатия, универсальный вариант.";
    }
  ];

  # lib.fixedWidthString pads on the left, which right-aligns the name against
  # the dash; the column wanted here is left-aligned.
  padRight =
    width: str: str + lib.concatStrings (lib.genList (_: " ") (width - lib.stringLength str));

  pdfPresetHelp = lib.concatMapStringsSep "\n" (
    p: "  ${padRight 11 p.name}— ${p.description}"
  ) pdfPresets;

  pdfPresetList = lib.concatMapStringsSep "|" (p: p.name) pdfPresets;

in [
    (pkgs.writeShellApplication {
      name = "vless-qr";
      runtimeInputs = [ pkgs.qrencode ];
      text = ''
        if [[ $# -ne 2 ]]; then
          echo "usage: vless-qr 'vless://…' output.png" >&2
          exit 1
        fi
        if [[ $1 != vless://?* ]]; then
          echo "vless-qr: first argument must be a non-empty vless:// link" >&2
          exit 1
        fi
        if [[ -z $2 || $2 == - ]]; then
          echo "vless-qr: second argument must be an output file path" >&2
          exit 1
        fi

        # The image contains credentials; keep newly created files private.
        umask 077
        printf '%s' "$1" | qrencode -t PNG -s 8 -o "$2"
      '';
    })

    # Clipboard, replacing `alias c=xclip` / `alias v="xclip -o"`.
    #
    # Those two had a real bug: xclip defaults to the PRIMARY selection, i.e.
    # the middle-click buffer, not the one Ctrl+V reads. Copying with `c` and
    # pasting with Ctrl+V therefore did not round-trip.
    #
    # The dispatch exists because the answer differs per session type and this
    # config has to survive both -- GNOME runs Wayland by default now, but
    # x11docker and any XWayland client still land on the X11 side.
    (pkgs.writeShellApplication {
      name = "clip";
      runtimeInputs = [
        pkgs.wl-clipboard
        pkgs.xclip
      ];
      text = ''
        usage() {
          echo "usage: clip copy|paste" >&2
          echo "  clip copy   — read stdin into the clipboard" >&2
          echo "  clip paste  — write the clipboard to stdout" >&2
        }

        case "''${1:-}" in
          copy)
            if [[ -n ''${WAYLAND_DISPLAY:-} ]]; then
              wl-copy
            else
              xclip -selection clipboard
            fi
            ;;
          paste)
            if [[ -n ''${WAYLAND_DISPLAY:-} ]]; then
              wl-paste --no-newline
            else
              xclip -selection clipboard -o
            fi
            ;;
          -h | --help)
            usage
            ;;
          *)
            usage
            exit 1
            ;;
        esac
      '';
    })

    # PDF viewer. Papers is the GNOME 48+ successor to Evince, but which of the
    # two is installed is a property of the host, not of this configuration --
    # so the script asks instead of guessing, and falls back to the desktop's
    # default handler.
    #
    # Deliberately not `pkgs.papers`: it is GTK4, so a Nix-built one draws
    # through this config's own graphics stack and depends on the driver link
    # from `targets.genericLinux.gpu` being installed on the host (../../home.nix).
    # Using whichever viewer the distribution installed sidesteps that — a PDF
    # still opens on a machine where `non-nixos-gpu-setup` has never been run.
    (pkgs.writeShellApplication {
      name = "preview";
      runtimeInputs = [ ];
      text = ''
        viewers=( ${if environment == "kde" then "okular" else "papers evince"} )
        for viewer in "''${viewers[@]}"; do
          if command -v "$viewer" > /dev/null 2>&1; then
            exec "$viewer" "$@"
          fi
        done

        exec xdg-open "$@"
      '';
    })

    # Ghostscript re-compression, from the old `pdf_opti` function.
    #
    # `if gs ...; then` rather than the old `$?` check: under `set -e` a
    # failing gs would have exited before the check was ever reached, so the
    # "Произошла ошибка" branch was unreachable in a script.
    (pkgs.writeShellApplication {
      name = "pdf_opti";
      runtimeInputs = [
        pkgs.ghostscript
        pkgs.coreutils
      ];
      text = ''
        usage() {
          cat <<'EOF'
        Использование: pdf_opti ВХОДНОЙ_ФАЙЛ ВЫХОДНОЙ_ФАЙЛ [ПРЕСЕТ]

        Доступные пресеты качества (-dPDFSETTINGS):
        ${pdfPresetHelp}

        Пример: pdf_opti input.pdf output.pdf /screen
        EOF
        }

        if [[ $# -eq 0 || ''${1:-} == "-h" || ''${1:-} == "--help" ]]; then
          usage
          exit 0
        fi

        if [[ $# -lt 2 ]]; then
          usage
          exit 1
        fi

        input="$1"
        output="$2"
        quality="''${3:-/ebook}"

        case "$quality" in
          ${pdfPresetList}) ;;
          *)
            echo "Ошибка: неизвестный пресет '$quality'." >&2
            usage
            exit 1
            ;;
        esac

        if [[ ! -f "$input" ]]; then
          echo "Ошибка: Файл '$input' не найден." >&2
          exit 1
        fi

        echo "Оптимизация: $input -> $output (режим: $quality)..."

        if gs -sDEVICE=pdfwrite \
              -dCompatibilityLevel=1.5 \
              -dPDFSETTINGS="$quality" \
              -dNOPAUSE -dQUIET -dBATCH \
              -dEmbedAllFonts=true -dSubsetFonts=true \
              -dAutoRotatePages=/None \
              -sOutputFile="$output" "$input"; then
          echo "Готово! Новый размер: $(du -h "$output" | cut -f1)"
        else
          echo "Произошла ошибка при обработке." >&2
          exit 1
        fi
      '';
    })

    # The three snap cleanups as one command. snap and sudo come from the host
    # (snapd is a system service, and there is no point pinning a client that
    # has to match it), so they are not in runtimeInputs.
    (pkgs.writeShellApplication {
      name = "snap-clean";
      runtimeInputs = [
        pkgs.gawk
        pkgs.findutils
      ];
      text = ''
        usage() {
          echo "usage: snap-clean cache|snapshots|revisions|all" >&2
        }

        clean_cache() {
          sudo rm -rf /var/lib/snapd/cache/*
        }

        clean_snapshots() {
          snap saved | tail -n +2 | awk '{print $1}' | xargs -r -I{} sudo snap forget {}
        }

        clean_revisions() {
          snap list --all | awk '/disabled/{print $1, $3}' |
            while read -r snapname revision; do
              sudo snap remove "$snapname" --revision="$revision"
            done
        }

        case "''${1:-}" in
          cache) clean_cache ;;
          snapshots) clean_snapshots ;;
          revisions) clean_revisions ;;
          all)
            clean_cache
            clean_snapshots
            clean_revisions
            ;;
          -h | --help) usage ;;
          *)
            usage
            exit 1
            ;;
        esac
      '';
    })

    # nixodoo's flake apps without typing the flake URL every time:
    # `nxd project new --list` runs
    # `nix run <caller-flake>#project -- new --list`, and
    # `nxd catalog new ./acme-catalog --registry acme` does the same for the
    # `catalog` app. The first word is the app, everything after it is passed
    # through verbatim -- the `--` is always there, so an app's own `--list`,
    # `--help` or `--registry` never reaches nix.
    #
    # nix is taken from PATH rather than runtimeInputs: `nix run` on a flake
    # URL needs the user's own nix.conf (experimental-features, substituters,
    # access-tokens), and a second nix from this closure would be the same
    # binary with none of that reason to exist.
    (pkgs.writeShellApplication {
      name = "nxd";
      runtimeInputs = [
        pkgs.jq
        pkgs.coreutils
      ];
      text = ''
        flake="''${NIXODOO_FLAKE:-${nixodooFlake}}"

        # Read by scripts/_nxd, which must not run nix itself -- completing an
        # app name cannot cost a flake evaluation and a network round trip.
        # Written here, so the list is as fresh as the last `nxd --apps`.
        apps_cache="''${XDG_CACHE_HOME:-$HOME/.cache}/nxd/apps"

        usage() {
          cat <<EOF
        usage: nxd <app> [args...]   nix run $flake#<app> -- [args...]
               nxd --apps            list the apps this flake exposes, and
                                     cache them for the zsh completion
               nxd --help

        examples:
          nxd project new --list
          nxd catalog new ./acme-catalog --registry acme

        flake: $flake  (override with \$NIXODOO_FLAKE — a local checkout works:
               NIXODOO_FLAKE=~/projects/nixodoo nxd project new --list)
        EOF
        }

        case "''${1:-}" in
          "" | -h | --help)
            usage
            exit 0
            ;;
          --apps)
            # Apps and packages both, because `nix run` resolves either --
            # nixodoo exposes `options-reference` only as a package, and it
            # runs exactly like `project` and `catalog` do.
            #
            # `nix flake show` reports only the current system unless
            # --all-systems is given, so this is one evaluation, not one per
            # platform. Still an evaluation of the whole flake: seconds, cached
            # afterwards.
            apps="$(nix flake show --json "$flake" \
              | jq -r '[(.apps // {}), (.packages // {})] | .[] | .[] | keys[]' \
              | sort -u)"

            # An empty answer is a flake that failed to evaluate, not a flake
            # with no apps: keep the previous cache rather than blank it.
            if [[ -n "$apps" ]]; then
              mkdir -p "$(dirname "$apps_cache")"
              printf '%s\n' "$apps" > "$apps_cache"
            fi

            printf '%s\n' "$apps"
            exit 0
            ;;
          -*)
            echo "nxd: неизвестный флаг '$1' — первым аргументом идёт имя приложения" >&2
            usage >&2
            exit 1
            ;;
        esac

        app="$1"
        shift
        exec nix run "$flake#$app" -- "$@"
      '';
    })
]
