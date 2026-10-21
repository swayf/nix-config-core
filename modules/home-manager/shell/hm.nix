# Shared Home Manager helper; profiles select their output and checkout path.
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.hm;
in
{
  options.programs.hm = {
    flakePath = lib.mkOption {
      type = lib.types.str;
      default = "${config.home.homeDirectory}/.config/home-manager";
      description = "Home Manager checkout used by hm; overridden at runtime by HM_FLAKE.";
    };
    profile = lib.mkOption {
      type = lib.types.str;
      default = config.home.username;
      description = "Home Manager flake output used by hm; overridden at runtime by HM_PROFILE.";
    };
  };

  config = {
    # home-manager and nix come from PATH: use the CLI installed by this profile.
    home.packages = [
      (pkgs.writeShellApplication {
        name = "hm";
        runtimeInputs = [
          pkgs.git
          pkgs.coreutils
        ];
        text = ''
          flake=${lib.escapeShellArg cfg.flakePath}
          profile=${lib.escapeShellArg cfg.profile}
          flake="''${HM_FLAKE:-$flake}"
          profile="''${HM_PROFILE:-$profile}"
          core_flake="''${HM_CORE_FLAKE:-}"
          core_args=()

          # Git transport includes tracked dirty files but excludes ignored secrets.
          # Keep the public lock portable: the local override is invocation-only.
          prepare_core() {
            [[ -n $core_flake ]] || return 0
            if [[ ! -f "$core_flake/flake.nix" ]]; then
              echo "hm: нет flake.nix в HM_CORE_FLAKE: $core_flake" >&2
              return 1
            fi
            core_flake="$(realpath -- "$core_flake")"
            local root
            root="$(git -C "$core_flake" rev-parse --show-toplevel)" || return
            if [[ $root != "$core_flake" || $core_flake == "$(realpath -- "$flake")" ]]; then
              echo "hm: HM_CORE_FLAKE должен указывать на отдельный корень Git checkout core" >&2
              return 1
            fi
            # Nix treats an unescaped space/#/? as a different flake reference.
            local LC_ALL=C encoded="" char escaped i
            for (( i=0; i<''${#core_flake}; i++ )); do
              char="''${core_flake:i:1}"
              case "$char" in
                [a-zA-Z0-9/._~-]) encoded+="$char" ;;
                *) printf -v escaped '%%%02X' "'$char"; encoded+="$escaped" ;;
              esac
            done
            core_args=(--override-input core "git+file://$encoded" --no-write-lock-file)
          }

          # Named GC roots, one symlink per mark. Duplicated in
          # scripts/_hm -- the completion resolves the same two defaults without
          # running this script, so the pair has to be changed together.
          marks_dir="''${HM_MARKS:-''${XDG_STATE_HOME:-$HOME/.local/state}/hm-marks}"

          usage() {
            cat <<EOF
          usage: hm <command> [args]

            status                 stage/show core, then HM (git add -A)
            commit [-t TAG] [msg]  commit core, then HM with the same message; tag HM (-n: none)
            push                   push core, then HM, including annotated tags (--follow-tags)
            build                  build the configuration without activating it
            news [args]            show Home Manager news for the selected configuration
            switch [MARK] [args]   activate, marking the result if MARK is given
            update [MARK] [args]   git pull --ff-only, then switch
            flake-update           update the flake inputs, commit them, switch
            generations            list generations with their marks and tags
            mark [-f] NAME [ID]    pin a generation under NAME (default: current)
            unmark NAME            drop the mark, unpinning the generation
            rollback [ID|NAME]     back one generation, or to an id or a mark
            diff [git diff args]   show core, then HM changes (git diff HEAD)
            diff-closures [ID|NAME] diff closures against the active generation

          flake: $flake  (override with \$HM_FLAKE)
          profile: $profile  (override with \$HM_PROFILE)
          core: ''${core_flake:-public pin}  (local checkout via \$HM_CORE_FLAKE)
          marks: $marks_dir  (override with \$HM_MARKS)
          EOF
          }

          if [[ ! -d "$flake" ]]; then
            echo "hm: $flake не существует — настройте programs.hm.flakePath или задайте \$HM_FLAKE" >&2
            exit 1
          fi

          # Where nix keeps this user's profiles. Same probe order as Home
          # Manager's own setHomeManagerPathVariables: the XDG location wins where
          # it exists, the pre-Nix-2.14 per-user directory is the fallback.
          profile_dir() {
            local xdg="''${XDG_STATE_HOME:-$HOME/.local/state}/nix/profiles"
            if [[ -d $xdg ]]; then
              echo "$xdg"
            else
              echo "''${NIX_STATE_DIR:-/nix/var/nix}/profiles/per-user/$USER"
            fi
          }

          # The generation id the profile currently points at.
          current_id() {
            local link
            link="$(readlink "$(profile_dir)/home-manager" 2> /dev/null || true)"
            link="''${link#home-manager-}"
            echo "''${link%-link}"
          }

          # The tag name a commit message boils down to. ASCII by design: git
          # takes UTF-8 refnames, but tr works on bytes, so a Cyrillic message
          # would come back one dash per byte -- dropping the non-ASCII words and
          # keeping the rest is the readable half of that trade. Dots go too,
          # which is what makes the ".." / leading-dot / trailing-".lock" refname
          # rules impossible to trip rather than something to check for.
          tag_slug() {
            local s
            s="$(printf '%s' "''${1,,}" | tr -c 'a-z0-9_-' '-' | tr -s '-')"

            # Cut on a word boundary. A refname has no length limit, but this one
            # is read in the label column of `hm generations`, next to the marks.
            if (( ''${#s} > 50 )); then
              s="''${s:0:50}"
              s="''${s%-*}"
            fi

            s="''${s#-}"
            s="''${s%-}"
            printf '%s' "$s"
          }

          # A generation's store path, from either a mark name or a numeric id.
          # Marks win: an id is only ever a number, so the two cannot collide.
          resolve_gen() {
            local arg="$1" dir
            dir="$(profile_dir)"

            if [[ -L "$marks_dir/$arg" ]]; then
              readlink -f "$marks_dir/$arg"
            elif [[ $arg =~ ^[0-9]+$ && -L "$dir/home-manager-$arg-link" ]]; then
              readlink -f "$dir/home-manager-$arg-link"
            else
              echo "hm: нет ни поколения, ни метки '$arg' — см. hm generations" >&2
              return 1
            fi
          }

          cmd="''${1:-}"
          shift || true

          case "$cmd" in
            status)
              git -C "$flake" rev-parse --git-dir > /dev/null
              prepare_core
              if [[ -n $core_flake ]]; then
                echo "=== core: $core_flake ==="
                git -C "$core_flake" add -A
                git -C "$core_flake" status
                echo "=== home-manager: $flake ==="
              fi
              git -C "$flake" add -A
              git -C "$flake" status
              ;;
            commit)
              tag=""
              no_tag=""
              while [[ "''${1:-}" == -* ]]; do
                case "$1" in
                  -t | --tag)
                    # hm/ is not decoration: `hm generations` resolves this column
                    # with `git tag --points-at --list 'hm/*'`, and without a
                    # namespace every version tag in the repository lands in it.
                    tag="hm/''${2:?hm commit: -t требует имя тега}"
                    shift 2
                    ;;
                  -n | --no-tag)
                    no_tag="1"
                    shift
                    ;;
                  *)
                    echo "hm commit: неизвестный флаг '$1'" >&2
                    exit 1
                    ;;
                esac
              done

              # A name given by hand and already taken is an error; the derived
              # name below is not held to that, see there.
              if [[ -n $tag ]] && git -C "$flake" rev-parse -q --verify "refs/tags/$tag" > /dev/null; then
                echo "hm: тег $tag уже занят ($(git -C "$flake" rev-parse --short "$tag"))" >&2
                exit 1
              fi

              # Whether a message was given decides the automatic tag, and $* is
              # about to lose that distinction to its default.
              msg_given=""
              if (( $# > 0 )); then
                msg_given="1"
              fi
              msg="''${*:-Обновление конфигурации Home Manager}"

              # Preflight both repositories before staging either. Separate Git
              # commits cannot be atomic; stop before HM if the core commit fails.
              git -C "$flake" rev-parse --git-dir > /dev/null
              prepare_core
              if [[ -n $core_flake ]]; then
                git -C "$core_flake" add -A
                if git -C "$core_flake" diff --cached --quiet; then
                  echo "core: нет изменений для коммита"
                else
                  git -C "$core_flake" status --short
                  git -C "$core_flake" commit -m "$msg"
                fi
              fi

              git -C "$flake" add -A
              committed=""
              if git -C "$flake" diff --cached --quiet; then
                echo "Нет изменений для коммита"
              else
                git -C "$flake" status --short
                if git -C "$flake" commit -m "$msg"; then
                  committed="1"
                else
                  result=$?
                  if [[ -n $core_flake ]]; then
                    echo "hm: commit home-manager не выполнен; core мог быть уже сохранён. Исправьте ошибку и повторите hm commit." >&2
                  fi
                  exit "$result"
                fi
              fi

              # The message is the tag name by default; -t only overrides it and
              # -n turns it off. Two cases stay untagged: a commit with no message
              # of its own, where the fallback text names nothing, and a run that
              # committed nothing, where the tag would land on a commit that is
              # not the one the message describes. Explicit -t keeps tagging HEAD
              # in that second case -- see below.
              if [[ -z $tag && -z $no_tag && -n $msg_given && -n $committed ]]; then
                slug="$(tag_slug "$msg")"
                if [[ -z $slug ]]; then
                  echo "hm: из сообщения не собралось имя тега — тег не поставлен" >&2
                else
                  # Repeated messages are normal ("minor"), and a name nobody
                  # asked for is no reason to fail: the collision is numbered
                  # instead, hm/minor, hm/minor-2, hm/minor-3.
                  tag="hm/$slug"
                  n=2
                  while git -C "$flake" rev-parse -q --verify "refs/tags/$tag" > /dev/null; do
                    tag="hm/$slug-$n"
                    n=$(( n + 1 ))
                  done
                fi
              fi

              # Tagged even when there was nothing to commit: the tag then marks
              # the current HEAD, which is what `hm commit -t` right after a plain
              # `hm commit` is asking for.
              if [[ -n $tag ]]; then
                git -C "$flake" tag -a "$tag" -m "$msg"
                echo "tag: $tag -> $(git -C "$flake" rev-parse --short HEAD) (в hm generations — после switch)"
              fi
              ;;
            push)
              if (( $# > 0 )); then
                echo "usage: hm push (использует настройки push каждого репозитория)" >&2
                exit 1
              fi
              git -C "$flake" rev-parse --git-dir > /dev/null
              prepare_core
              if [[ -n $core_flake ]]; then
                echo "=== core: $core_flake ==="
                git -C "$core_flake" push --follow-tags
                echo "=== home-manager: $flake ==="
              fi
              if git -C "$flake" push --follow-tags; then
                :
              else
                result=$?
                if [[ -n $core_flake ]]; then
                  echo "hm: push home-manager не выполнен; core уже отправлен. Исправьте ошибку и повторите hm push." >&2
                fi
                exit "$result"
              fi
              ;;
            build)
              prepare_core
              home-manager build --flake "$flake#$profile" "''${core_args[@]}"
              ;;
            news)
              prepare_core
              home-manager news --flake "$flake#$profile" "''${core_args[@]}" "$@"
              ;;
            switch)
              # A leading word that is not a flag is a mark for the generation
              # this switch produces. home-manager switch itself takes no
              # positional arguments -- its parser rejects them -- so there is
              # nothing to be ambiguous with.
              mark=""
              if [[ -n "''${1:-}" && "''${1:-}" != -* ]]; then
                mark="$1"
                shift

                # Checked before the build rather than after: a name collision is
                # a one-second answer, and a switch can be a ten-minute one.
                if [[ -e "$marks_dir/$mark" || -L "$marks_dir/$mark" ]]; then
                  echo "hm: метка $mark занята -> $(readlink -f "$marks_dir/$mark")" >&2
                  echo "    hm unmark $mark, либо hm mark -f $mark после switch" >&2
                  exit 1
                fi
              fi

              # -b is not optional on this host: Home Manager refuses to replace a
              # file it does not already own, and ~/.zshenv, ~/.ssh/config and
              # friends are exactly that on a machine migrating from hand-written
              # dotfiles. The timestamp keeps a second switch from colliding with
              # the backup left by the first.
              prepare_core
              home-manager switch \
                --flake "$flake#$profile" \
                "''${core_args[@]}" \
                -b "hm-backup-$(date +%Y%m%d-%H%M%S)" \
                "$@"

              # Only once activation succeeded -- a mark on a generation that
              # never came up is worse than no mark.
              if [[ -n $mark ]]; then
                "$0" mark "$mark"
              fi
              ;;
            update)
              git -C "$flake" pull --ff-only
              exec "$0" switch "$@"
              ;;
            flake-update)
              nix flake update --flake "$flake"

              # Committed here rather than left in the working tree, because a
              # switch on a dirty tree produces a generation named
              # <rev>-dirty (see flake.nix) that belongs to no commit -- and
              # updated inputs are exactly what one wants to roll back later.
              git -C "$flake" add flake.lock
              if ! git -C "$flake" diff --cached --quiet -- flake.lock; then
                git -C "$flake" commit -m "flake.lock: update inputs"

                # Tagged so `hm generations` shows when the inputs moved, e.g.
                # #hm/updated-2026-09-16-14-30. Only on a real commit: with
                # nothing new the tag would date a commit that is not an update.
                stamp="updated $(date '+%Y-%m-%d %H:%M')"
                tag="hm/$(tag_slug "$stamp")"
                git -C "$flake" tag -a "$tag" -m "$stamp"
                echo "tag: $tag -> $(git -C "$flake" rev-parse --short HEAD)"
              fi

              # "$0", not `hm`: this must reach the switch even when the profile
              # that puts hm on PATH is mid-rebuild.
              exec "$0" switch "$@"
              ;;
            generations)
              dir="$(profile_dir)"
              current="$(current_id)"

              # Marks are read once and indexed by store path. There can be more
              # marks than generations, and re-scanning the directory per
              # generation would be a readlink storm for a ten-line table.
              declare -A marks_of=()
              for m in "$marks_dir"/*; do
                [[ -L $m ]] || continue
                marks_of["$(readlink -f "$m")"]+=" @''${m##*/}"
              done

              rows=()
              for link in "$dir"/home-manager-*-link; do
                [[ -L $link ]] || continue

                id="''${link##*/home-manager-}"
                id="''${id%-link}"
                target="$(readlink -f "$link")"

                # The rev flake.nix stamped into the derivation name. Empty on
                # generations built before that existed.
                rev="''${target##*/}"
                rev="''${rev#*-home-manager-generation}"
                rev="''${rev#-}"

                labels="''${marks_of[$target]:-}"
                if [[ -n $rev ]]; then
                  while read -r tag; do
                    [[ -n $tag ]] && labels+=" #$tag"
                  done < <(git -C "$flake" tag --points-at "$rev" --list 'hm/*' 2> /dev/null || true)
                fi

                marker=" "
                [[ $id == "$current" ]] && marker="*"

                # stat, not `date -r`: the mtime wanted is the symlink's, and
                # date dereferences it into the store, where every path is
                # timestamped 1970.
                when="$(date -d "@$(stat -c %Y "$link")" '+%Y-%m-%d %H:%M')"

                # The label column is dropped rather than padded when empty, so
                # that a generation without marks leaves no trailing whitespace.
                if [[ -n ''${labels# } ]]; then
                  row="$(printf '%s %4s  %s  %-12s %s' \
                    "$marker" "$id" "$when" "''${rev:--}" "''${labels# }")"
                else
                  row="$(printf '%s %4s  %s  %s' "$marker" "$id" "$when" "''${rev:--}")"
                fi

                rows+=("$id"$'\t'"$row")
              done

              if (( ''${#rows[@]} == 0 )); then
                echo "hm: поколений нет — профиль $dir/home-manager пуст" >&2
                exit 1
              fi

              # Written out instead of built with %-16s: bash pads by bytes, and
              # a Cyrillic heading is two bytes per character, so every directive
              # here would leave the column short by its own length.
              echo "    id  дата              rev          метки"
              printf '%s\n' "''${rows[@]}" | sort -t$'\t' -k1,1nr | cut -f2-
              ;;
            mark)
              force=""
              if [[ "''${1:-}" == "-f" || "''${1:-}" == "--force" ]]; then
                force="1"
                shift
              fi

              name="''${1:-}"
              if [[ -z $name ]]; then
                echo "usage: hm mark [-f] <имя> [id]" >&2
                exit 1
              fi

              # The mark is a file name and a completion candidate; a slash, a
              # space or a leading dash breaks one or both.
              if [[ ! $name =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
                echo "hm: имя метки — ASCII [A-Za-z0-9._-], не начинается с дефиса" >&2
                exit 1
              fi

              dir="$(profile_dir)"
              id="''${2:-}"
              if [[ -n $id ]]; then
                link="$dir/home-manager-$id-link"
                if [[ ! -L $link ]]; then
                  echo "hm: нет поколения $id — см. hm generations" >&2
                  exit 1
                fi
              else
                link="$dir/home-manager"
                id="$(current_id)"
              fi

              if [[ -e "$marks_dir/$name" || -L "$marks_dir/$name" ]]; then
                if [[ -z $force ]]; then
                  echo "hm: метка $name занята -> $(readlink -f "$marks_dir/$name")" >&2
                  echo "    hm unmark $name, либо hm mark -f $name" >&2
                  exit 1
                fi
                rm -- "$marks_dir/$name"
              fi

              mkdir -p "$marks_dir"

              # --add-root is the whole point: the mark registers an indirect GC
              # root, so this generation now survives both
              # `home-manager expire-generations` and `nix-collect-garbage`.
              nix-store --add-root "$marks_dir/$name" --indirect -r "$(readlink -f "$link")" > /dev/null
              echo "mark: $name -> id $id ($(readlink -f "$link"))"
              ;;
            unmark)
              name="''${1:-}"
              if [[ -z $name ]]; then
                echo "usage: hm unmark <метка>" >&2
                exit 1
              fi
              if [[ ! -L "$marks_dir/$name" ]]; then
                echo "hm: нет метки $name" >&2
                exit 1
              fi

              rm -- "$marks_dir/$name"
              echo "unmark: $name (поколение больше не защищено от GC)"
              ;;
            rollback)
              if [[ -z "''${1:-}" ]]; then
                # One step back, profile pointer and all.
                home-manager rollback
                exit
              fi

              target="$(resolve_gen "$1")"

              # No nix-env here. An activate script invoked without
              # --driver-version defaults to version 0, and version 0 is the one
              # that sets the profile itself; the CLI passes 1 precisely to stop
              # it doing that after it has already done so. The result is a new
              # generation holding the old configuration, which is what keeps the
              # rollback itself undoable.
              "$target/activate"
              ;;
            diff)
              # HEAD includes both staged and unstaged changes, without touching
              # the index. Untracked files are not shown until added to Git.
              # Auto color keeps terminal output readable and pipes ANSI-free.
              prepare_core
              if [[ -n $core_flake ]]; then
                echo "=== core: $core_flake ==="
                git -C "$core_flake" diff --color=auto HEAD "$@"
                echo "=== home-manager: $flake ==="
              fi
              git -C "$flake" diff --color=auto HEAD "$@"
              ;;
            diff-closures)
              current_profile="$(profile_dir)/home-manager"

              if [[ -z "''${1:-}" ]]; then
                # What the next switch would change. --no-link so this does not
                # drop a ./result into whatever directory it was run from.
                prepare_core
                built="$(nix build --no-link --print-out-paths \
                  "''${core_args[@]}" "$flake#homeConfigurations.$profile.activationPackage")"
                nix store diff-closures "$current_profile" "$built"
              else
                nix store diff-closures "$(resolve_gen "$1")" "$current_profile"
              fi
              ;;
            "" | -h | --help)
              usage
              ;;
            *)
              echo "hm: неизвестная команда '$cmd'" >&2
              usage
              exit 1
              ;;
          esac
        '';
      })
    ];

    home.shellAliases = {
      hmstatus = "hm status";
      hmcommit = "hm commit";
      hmbuild = "hm build";
      hmswitch = "hm switch";
      hmflakeupdate = "hm flake-update";
    };
    programs.zsh.siteFunctions = lib.mkIf config.programs.zsh.enable {
      _hm = builtins.readFile ./scripts/_hm;
    };
  };
}
