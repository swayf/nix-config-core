# Shared Home Manager helper; profiles select their output and checkout path.
# backend = "nixos" serves a host where Home Manager is a NixOS module: the
# git commands stay, build/switch/generations go to nixos-rebuild.
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
    coreFlakePath = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Optional local core checkout used by hm; HM_CORE_FLAKE overrides it, including an empty value to disable it.";
    };
    profile = lib.mkOption {
      type = lib.types.str;
      default = config.home.username;
      description = "Home Manager flake output used by hm; overridden at runtime by HM_PROFILE.";
    };
    backend = lib.mkOption {
      type = lib.types.enum [
        "standalone"
        "nixos"
      ];
      default = "standalone";
      description = ''
        standalone: hm builds and activates homeConfigurations.<profile> with the
        home-manager CLI. nixos: Home Manager is a NixOS module; build, switch,
        rollback, generations and diff-closures act on
        nixosConfigurations.<nixosHost> and the system profile.
      '';
    };
    nixosHost = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "nixosConfigurations output used by hm when backend is nixos.";
    };
    bootLabel = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        backend nixos: hm switch passes the commit subject (as an ASCII slug)
        and the commit time of a clean checkout in HM_BOOT_LABEL and evaluates
        with --impure. The NixOS configuration decides how to use the variable.
      '';
    };
  };

  config = {
    assertions = [
      {
        assertion = cfg.backend != "nixos" || cfg.nixosHost != null;
        message = "programs.hm.backend = \"nixos\" requires programs.hm.nixosHost.";
      }
    ];

    # home-manager, nix and nixos-rebuild come from PATH: use the CLI installed
    # by this profile or the system.
    home.packages = [
      (pkgs.writeShellApplication {
        name = "hm";
        runtimeInputs = [
          pkgs.git
          pkgs.coreutils
        ] ++ (if cfg.backend == "nixos" then [ pkgs.jq pkgs.util-linux ] else [ ]);
        text = ''
          backend=${lib.escapeShellArg cfg.backend}
          host=${lib.escapeShellArg (if cfg.nixosHost == null then "" else cfg.nixosHost)}
          boot_label_enabled=${if cfg.bootLabel then "1" else "\"\""}
          flake=${lib.escapeShellArg cfg.flakePath}
          profile=${lib.escapeShellArg cfg.profile}
          flake="''${HM_FLAKE:-$flake}"
          profile="''${HM_PROFILE:-$profile}"
          core_flake=${lib.escapeShellArg (if cfg.coreFlakePath == null then "" else cfg.coreFlakePath)}
          core_flake="''${HM_CORE_FLAKE-$core_flake}"
          core_args=()
          build_flake="$flake"
          system_profile=/nix/var/nix/profiles/system

          # Nix treats an unescaped space/#/? as a different flake reference.
          git_uri() {
            local path="$1" LC_ALL=C encoded="" char escaped i
            for (( i=0; i<''${#path}; i++ )); do
              char="''${path:i:1}"
              case "$char" in
                [a-zA-Z0-9/._~-]) encoded+="$char" ;;
                *) printf -v escaped '%%%02X' "'$char"; encoded+="$escaped" ;;
              esac
            done
            printf 'git+file://%s' "$encoded"
          }

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
          }

          # Overriding a root input makes Nix drop its shortRev/dirtyShortRev.
          # Keep the user's flake as a Git input of a disposable wrapper instead:
          # only the wrapper loses its revision, not the labelled generations.
          # Unlike --reference-lock-file, these flags pass through the HM CLI.
          prepare_build() {
            build_args=("$@")
            prepare_core
            [[ -n $core_flake ]] || return 0
            core_wrapper="$(mktemp -d "''${TMPDIR:-/tmp}/hm-core-flake.XXXXXXXXXX")"
            trap 'rm -f -- "$core_wrapper/flake.nix"; rmdir -- "$core_wrapper"' EXIT
            printf '{ inputs.home.url = "%s"; outputs = { home, ... }: home.outputs; }\n' \
              "$(git_uri "$(realpath -- "$flake")")" > "$core_wrapper/flake.nix"
            build_flake="$core_wrapper"
            core_args=(--override-input home/core "$(git_uri "$core_flake")" --no-write-lock-file)
            # User input paths are still relative to the original HM flake.
            local i
            for (( i=0; i<''${#build_args[@]}; i++ )); do
              case "''${build_args[i]}" in
                --override-input | --update-input)
                  if [[ -n "''${build_args[i+1]:-}" && "''${build_args[i+1]}" != -* ]]; then
                    build_args[i+1]="home/''${build_args[i+1]}"
                  fi
                  ;;
              esac
            done
          }

          # nixos-rebuild is exec'd, so a temporary wrapper flake would outlive
          # nothing that removes it: the local core replaces the root input
          # directly. The NixOS configuration does not read self.rev.
          prepare_nixos() {
            prepare_core
            [[ -n $core_flake ]] || return 0
            core_args=(--override-input core "$(git_uri "$core_flake")" --no-write-lock-file)
          }

          # A Git flake leaves untracked files out of the build.
          warn_untracked() {
            local repo untracked
            for repo in "$flake" ''${core_flake:+"$core_flake"}; do
              mapfile -t untracked < <(git -C "$repo" ls-files --others --exclude-standard 2> /dev/null || true)
              if (( ''${#untracked[@]} > 0 )); then
                echo "hm: в $repo есть untracked-файлы, в сборку они не попадут (hm add добавит):" >&2
                printf '  %s\n' "''${untracked[@]}" >&2
              fi
            done
          }

          # The HEAD commit subject as a slug and the commit time HH:MM, for the
          # boot menu. The commit time, not the switch time: a repeated switch
          # of one commit builds the same system, not a new generation. A NixOS
          # label allows only [a-zA-Z0-9:_.-]. Nothing when a tree is dirty: the
          # build is then not that commit.
          boot_label() {
            local slug stamp
            if [[ -n "$(git -C "$flake" status --porcelain --untracked-files=no)" ]]; then
              return 0
            fi
            if [[ -n $core_flake && -n "$(git -C "$core_flake" status --porcelain --untracked-files=no)" ]]; then
              return 0
            fi
            stamp="$(git -C "$flake" log -1 --format=%cd --date=format-local:%H:%M)"
            slug="$(subject_slug "$(git -C "$flake" log -1 --format=%s)")"
            if [[ -z $slug ]]; then
              printf '%s' "$stamp"
              return 0
            fi
            printf '%s' "$slug-$stamp"
          }

          # A system generation's store path from its numeric id.
          resolve_system_gen() {
            if [[ $1 =~ ^[0-9]+$ && -L "$system_profile-$1-link" ]]; then
              readlink -f "$system_profile-$1-link"
            else
              echo "hm: нет системного поколения '$1' — см. hm generations" >&2
              return 1
            fi
          }

          # Named GC roots, one symlink per mark. Duplicated in
          # scripts/_hm -- the completion resolves the same two defaults without
          # running this script, so the pair has to be changed together.
          marks_dir="''${HM_MARKS:-''${XDG_STATE_HOME:-$HOME/.local/state}/hm-marks}"

          usage() {
            cat <<EOF
          usage: hm <command> [args]

          git:
            status                 show core, then HM (git status, index unchanged)
            add                    stage core, then HM (git add -A)
            diff [git diff args]   show core, then HM changes (git diff HEAD)
            commit [-t TAG] [msg]  stage and commit core, then HM with the same message; -t tags HM
            push                   push core, then HM, including annotated tags (--follow-tags)
          EOF
            if [[ $backend == nixos ]]; then
              cat <<EOF

          build and activate (NixOS $host, Home Manager inside the system):
            build [args]           nixos-rebuild build
            switch [args]          nixos-rebuild switch (boot label from the commit subject)
            pull [args]            git pull --ff-only, then switch
            update [args]          update the flake inputs, commit them, switch
            diff-closures [ID]     closure diff: next build, or generation ID, vs the running system

          generations:
            generations [args]     system generations with their boot labels
            rollback [ID]          back one system generation, or to ID
          EOF
            else
              cat <<EOF

          build and activate (standalone Home Manager):
            build                  build the configuration without activating it
            switch [MARK] [args]   activate, marking the result if MARK is given
            pull [MARK] [args]     git pull --ff-only, then switch
            update [MARK] [args]   update the flake inputs, commit them, switch
            diff-closures [ID|NAME] closure diff: next build, or ID/mark, vs the active generation
            news [args]            show Home Manager news for the selected configuration

          generations:
            generations            list generations with marks, tags and commit subject
            rollback [ID|NAME]     back one generation, or to an id or a mark
            mark [-f] NAME [ID]    pin a generation under NAME (default: current)
            unmark NAME            drop the mark, unpinning the generation
          EOF
            fi
            cat <<EOF

          plugins:
            <name> [args]          run hm-<name> from PATH (with HM_FLAKE, HM_PROFILE)

          flake: $flake  (override with \$HM_FLAKE)
          EOF
            if [[ $backend == nixos ]]; then
              echo "host: $host  (nixosConfigurations)"
            else
              echo "profile: $profile  (override with \$HM_PROFILE)"
            fi
            echo "core: ''${core_flake:-public pin}  (local checkout via \$HM_CORE_FLAKE)"
            if [[ $backend != nixos ]]; then
              echo "marks: $marks_dir  (override with \$HM_MARKS)"
            fi
          }

          # Standalone-only commands get one answer on NixOS instead of reaching
          # the home-manager CLI, which would build a second, standalone HOME.
          standalone_only() {
            echo "hm $1: только для standalone Home Manager; здесь он часть NixOS $host." >&2
            echo "Справка: hm --help" >&2
            exit 1
          }

          # A mark names a standalone generation; system generations carry the
          # commit subject in their boot label instead.
          reject_mark() {
            if [[ -n "''${1:-}" && "$1" != -* ]]; then
              echo "hm $cmd: метки поколений только в standalone; подпись берётся из темы коммита, '$1' не принят" >&2
              exit 1
            fi
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

          # The boot-label part a commit subject boils down to. ASCII by design:
          # tr works on bytes, so a Cyrillic subject would come back one dash per
          # byte -- dropping the non-ASCII words and keeping the rest is the
          # readable half of that trade.
          subject_slug() {
            local s
            s="$(printf '%s' "''${1,,}" | tr -c 'a-z0-9_-' '-' | tr -s '-')"

            # Cut on a word boundary: the label is read in the boot menu.
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
              # Read-only: --no-optional-locks keeps git from rewriting the index
              # stat cache, untracked files are only reported. Staging is hm add.
              git -C "$flake" rev-parse --git-dir > /dev/null
              prepare_core
              if [[ -n $core_flake ]]; then
                echo "=== core: $core_flake ==="
                git --no-optional-locks -C "$core_flake" status
                echo "=== home-manager: $flake ==="
              fi
              git --no-optional-locks -C "$flake" status
              warn_untracked
              ;;
            add)
              if (( $# > 0 )); then
                echo "usage: hm add (git add -A в core, затем в HM)" >&2
                exit 1
              fi
              git -C "$flake" rev-parse --git-dir > /dev/null
              prepare_core
              if [[ -n $core_flake ]]; then
                git -C "$core_flake" add -A
              fi
              git -C "$flake" add -A
              ;;
            commit)
              tag=""
              while [[ "''${1:-}" == -* ]]; do
                case "$1" in
                  -t | --tag)
                    # hm/ is not decoration: `hm generations` resolves this column
                    # with `git tag --points-at --list 'hm/*'`, and without a
                    # namespace every version tag in the repository lands in it.
                    tag="hm/''${2:?hm commit: -t требует имя тега}"
                    shift 2
                    ;;
                  *)
                    echo "hm commit: неизвестный флаг '$1'" >&2
                    exit 1
                    ;;
                esac
              done

              # A tag only on request; an already taken name is an error.
              if [[ -n $tag ]] && git -C "$flake" rev-parse -q --verify "refs/tags/$tag" > /dev/null; then
                echo "hm: тег $tag уже занят ($(git -C "$flake" rev-parse --short "$tag"))" >&2
                exit 1
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
              if git -C "$flake" diff --cached --quiet; then
                echo "Нет изменений для коммита"
              else
                git -C "$flake" status --short
                if git -C "$flake" commit -m "$msg"; then
                  :
                else
                  result=$?
                  if [[ -n $core_flake ]]; then
                    echo "hm: commit home-manager не выполнен; core мог быть уже сохранён. Исправьте ошибку и повторите hm commit." >&2
                  fi
                  exit "$result"
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
              if [[ $backend == nixos ]]; then
                prepare_nixos
                warn_untracked
                exec nixos-rebuild build --flake "$flake#$host" "''${core_args[@]}" "$@"
              fi
              prepare_build
              warn_untracked
              home-manager build --flake "$build_flake#$profile" "''${core_args[@]}"
              ;;
            news)
              [[ $backend == nixos ]] && standalone_only "$cmd"
              prepare_build "$@"
              home-manager news --flake "$build_flake#$profile" "''${core_args[@]}" "''${build_args[@]}"
              ;;
            switch)
              if [[ $backend == nixos ]]; then
                reject_mark "$@"
                prepare_nixos
                warn_untracked
                label_args=()
                if [[ -n $boot_label_enabled ]]; then
                  label="$(boot_label)"
                  if [[ -n $label ]]; then
                    echo "hm: метка в меню загрузки: $label" >&2
                    export HM_BOOT_LABEL="$label"
                    label_args=(--impure)
                  else
                    unset HM_BOOT_LABEL
                  fi
                fi
                # --sudo: built as the user (root would refuse a Git checkout it
                # does not own), elevated only to activate.
                exec nixos-rebuild switch --sudo --flake "$flake#$host" \
                  "''${core_args[@]}" "''${label_args[@]}" "$@"
              fi

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
              prepare_build "$@"
              warn_untracked
              home-manager switch \
                --flake "$build_flake#$profile" \
                "''${core_args[@]}" \
                -b "hm-backup-$(date +%Y%m%d-%H%M%S)" \
                "''${build_args[@]}"

              # Only once activation succeeded -- a mark on a generation that
              # never came up is worse than no mark.
              if [[ -n $mark ]]; then
                "$0" mark "$mark"
              fi
              ;;
            pull)
              # Checked before the pull: a switch that will refuse the mark should
              # not leave a moved checkout behind.
              [[ $backend == nixos ]] && reject_mark "$@"
              git -C "$flake" pull --ff-only
              exec "$0" switch "$@"
              ;;
            update)
              [[ $backend == nixos ]] && reject_mark "$@"
              nix flake update --flake "$flake"

              # Committed here rather than left in the working tree, because a
              # switch on a dirty tree produces a generation named
              # <rev>-dirty (see flake.nix) that belongs to no commit -- and
              # updated inputs are exactly what one wants to roll back later.
              # The subject labels the generation in hm generations and the boot menu.
              git -C "$flake" add flake.lock
              if ! git -C "$flake" diff --cached --quiet -- flake.lock; then
                git -C "$flake" commit -m "flake.lock: update inputs"
              fi

              # "$0", not `hm`: this must reach the switch even when the profile
              # that puts hm on PATH is mid-rebuild.
              exec "$0" switch "$@"
              ;;
            generations)
              if [[ $backend == nixos ]]; then
                # nixos-rebuild's table shows system.nixos.version, not the boot
                # label: the label is in <generation>/nixos-version, tags first,
                # the version last. Any argument (--json, ...) gets the plain
                # nixos-rebuild output.
                if (( $# > 0 )); then
                  exec nixos-rebuild list-generations "$@"
                fi
                {
                  printf '%s\t' Generation Build-date "NixOS version" Kernel \
                    Specialisation Current
                  printf '%s\n' "Метка"
                  nixos-rebuild list-generations --json \
                    | jq -r '.[] | [.generation, .date, .nixosVersion, .kernelVersion,
                        (.specialisations | join(",") | if . == "" then "-" else . end),
                        .current] | @tsv' \
                    | while IFS=$'\t' read -r gen date version kernel spec current; do
                        label="$(cat "$system_profile-$gen-link/nixos-version" 2> /dev/null || true)"
                        label="''${label%-"$version"}"
                        if [[ -z $label || $label == "$version" ]]; then
                          label="-"
                        fi
                        printf '%s\t' "$gen" "$date" "$version" "$kernel" \
                          "$spec" "$current"
                        printf '%s\n' "$label"
                      done
                } | column -t -s $'\t'
                exit 0
              fi
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

                # The commit subject is the generation's caption. A -dirty or
                # unknown rev names no commit, so it gets none.
                labels="''${marks_of[$target]:-}"
                if [[ -n $rev ]]; then
                  while read -r tag; do
                    [[ -n $tag ]] && labels+=" #$tag"
                  done < <(git -C "$flake" tag --points-at "$rev" --list 'hm/*' 2> /dev/null || true)
                  subject="$(git -C "$flake" log -1 --format=%s "$rev^{commit}" -- 2> /dev/null || true)"
                  [[ -n $subject ]] && labels+=" $subject"
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
              echo "    id  дата              rev          метки и тема"
              printf '%s\n' "''${rows[@]}" | sort -t$'\t' -k1,1nr | cut -f2-
              ;;
            mark)
              [[ $backend == nixos ]] && standalone_only "$cmd"
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
              [[ $backend == nixos ]] && standalone_only "$cmd"
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
              if [[ $backend == nixos ]]; then
                if [[ -z "''${1:-}" ]]; then
                  exec nixos-rebuild switch --rollback --sudo
                fi
                resolve_system_gen "$1" > /dev/null
                # What nixos-rebuild --rollback does, for a chosen generation:
                # move the profile, then activate and make it the boot default.
                sudo "$(command -v nix-env)" --profile "$system_profile" --switch-generation "$1"
                exec sudo "$system_profile/bin/switch-to-configuration" switch
              fi
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
              if [[ $backend == nixos ]]; then
                if [[ -z "''${1:-}" ]]; then
                  prepare_nixos
                  built="$(nix build --no-link --print-out-paths "''${core_args[@]}" \
                    "$flake#nixosConfigurations.\"$host\".config.system.build.toplevel")"
                  exec nix store diff-closures /run/current-system "$built"
                fi
                exec nix store diff-closures "$(resolve_system_gen "$1")" /run/current-system
              fi
              current_profile="$(profile_dir)/home-manager"

              if [[ -z "''${1:-}" ]]; then
                # What the next switch would change. --no-link so this does not
                # drop a ./result into whatever directory it was run from.
                prepare_build
                built="$(nix build --no-link --print-out-paths \
                  "''${core_args[@]}" "$build_flake#homeConfigurations.$profile.activationPackage")"
                nix store diff-closures "$current_profile" "$built"
              else
                nix store diff-closures "$(resolve_gen "$1")" "$current_profile"
              fi
              ;;
            "" | -h | --help)
              usage
              ;;
            *)
              # Plugins, as in git: `hm foo args` runs `hm-foo args` from PATH, so
              # a private checkout can add commands without patching this file.
              # The name check keeps `hm ../x` or `hm 'a b'` from reaching PATH.
              if [[ $cmd =~ ^[a-z0-9][a-z0-9-]*$ ]] && plugin="$(command -v "hm-$cmd")"; then
                export HM_FLAKE="$flake" HM_PROFILE="$profile"
                exec "$plugin" "$@"
              fi
              echo "hm: неизвестная команда '$cmd'" >&2
              usage
              exit 1
              ;;
          esac
        '';
      })
    ];

    programs.zsh.siteFunctions = lib.mkIf config.programs.zsh.enable {
      _hm = builtins.replaceStrings [ "@backend@" ] [ cfg.backend ] (builtins.readFile ./scripts/_hm);
    };
  };
}
