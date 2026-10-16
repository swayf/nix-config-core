# modules/shell/zsh.nix
#
# Home Manager owns the shell.
#
# `dotDir` points at $XDG_CONFIG_HOME/zsh rather than $HOME. That is where the
# upstream default is heading -- the module emits a warning telling you to pick
# one explicitly -- and it is what makes this switch non-destructive: Home
# Manager writes ~/.zshenv, that sets ZDOTDIR, and zsh reads
# ~/.config/zsh/.zshrc from then on. The old hand-written ~/.zshrc is not
# clobbered and not read; it stays on disk as a reference until deleted.
#
# ~/.zshenv itself does have to be created, so the first switch needs a backup
# extension if one is already there:
#
#     home-manager switch --flake . -b hm-backup
#
# `hm switch` (modules/shell/hm.nix) always passes one.
#
# Everything below that is not an option is a port of Manjaro's
# /usr/share/zsh/manjaro-zsh-config and -prompt, which stop being sourced once
# Home Manager writes the rc file. They are pacman-installed and exist on no
# other host, which is the reason to reproduce them rather than source them.
# What did NOT come across, and why:
#
#   powerlevel10k               -> programs.starship
#   zsh-syntax-highlighting,
#   zsh-autosuggestions,
#   zsh-history-substring-search-> the options below, same plugins from nixpkgs
#   alias ls='ls $LS_OPTIONS'   -> programs.eza
#   alias x='startx ~/.xinitrc' -> only ever defined on a non-UTF-8 tty whose
#                                  parent is `login`; irrelevant under GNOME
#   LESS_TERMCAP_* (man colors) -> MANPAGER=bat, modules/shell/environment.nix
{
  config,
  lib,
  pkgs,
  ...
}:

let
  # kubectl's zsh completion, generated once at build time.
  #
  # `source <(kubectl completion zsh)` in an rc file runs kubectl on every
  # single shell start; kubectl takes ~100 ms to print those 2000 lines. Putting
  # the same script in fpath costs nothing at run time, and compinit is already
  # walking fpath anyway.
  #
  # Generated from pkgs.kubectl without installing it -- only the completion
  # text is taken, so the kubectl actually invoked stays the system one and no
  # client/server version pin is introduced.
  kubectlCompletions = pkgs.runCommand "kubectl-completion-zsh" { } ''
    mkdir -p "$out"
    ${lib.getExe pkgs.kubectl} completion zsh > "$out/_kubectl"
  '';

  # Powerline separators and Nerd Font icons sit in the Unicode Private Use
  # Area. A PUA character has no name, renders as a blank or a tofu box in any
  # font that has not been patched, and survives a copy-paste only if every
  # tool on the way is UTF-8 clean -- pasting this file through one that is not
  # silently deletes the separators and leaves a prompt that looks merely
  # mis-coloured rather than obviously broken.
  #
  # Spelling them by codepoint costs one function and makes the file greppable,
  # reviewable in a plain editor, and safe to move around. Nix has no \u escape
  # of its own; JSON does, and fromJSON runs at evaluation time.
  glyph = codepoint: builtins.fromJSON ''"\u${codepoint}"'';

  # The original Powerline set (U+E0A0-E0B3), present in every patched font.
  sepRight = glyph "e0b0"; # solid wedge pointing right, segment -> segment
  sepLeft = glyph "e0b2"; # its mirror, for the right-hand prompt

  # Nerd Font icons. Devicons (e7xx), Font Logos (f3xx) and Font Awesome (f0xx)
  # all kept their codepoints through the Nerd Fonts v2 -> v3 renumbering, so
  # these are stable across font versions.
  iconBranch = glyph "e0a0"; # powerline VCS branch
  iconGit = glyph "e702"; # git logo
  iconFolder = glyph "f07c"; # open folder
  iconManjaro = glyph "f312";
  iconNixos = glyph "f313";
  iconLinux = glyph "f17c"; # tux, for anything else

  # Segment backgrounds, as ANSI palette names rather than hex. powerlevel10k's
  # rainbow style did the same, and it is why the prompt followed the terminal
  # theme instead of fighting it.
  osBg = "bright-black";
  dirBg = "blue";
  gitBg = "green";
  errBg = "red";

  # U+2718 HEAVY BALLOT X. Not a PUA codepoint -- it has a name, is in every
  # font worth using, and survives a paste -- so it is written out rather than
  # spelled through `glyph`, the same way `truncation_symbol` writes `…`.
  iconCross = "✘";
in
{
  programs.zsh = {
    enable = true;
    dotDir = "${config.xdg.configHome}/zsh";

    enableCompletion = true;

    # `-u` is not optional on a multi-user Nix install, and its absence is not
    # a warning -- it kills completion outright.
    #
    # compaudit rejects any directory in fpath that a group can write to. On a
    # daemon install /nix/store is `drwxrwxr-t root nixbld`, so every
    # completion shipped through the store is flagged, including the ones Home
    # Manager itself adds from $NIX_PROFILES. compinit then asks a question no
    # one is there to answer and aborts:
    #
    #     zsh compinit: insecure directories, run compaudit for list.
    #     Ignore insecure directories and continue [y] or abort compinit [n]?
    #     compinit: initialization aborted
    #
    # After that `$_comps` is empty -- no git completion, no kubectl, nothing.
    # `-i` would skip the flagged directories instead, which on this machine
    # means skipping all of them; `-u` uses them. The store is root-owned and
    # sticky, and nixbld is the daemon's own build users.
    completionInit = "autoload -Uz compinit && compinit -u -d ${config.xdg.cacheHome}/zsh/zcompdump";

    autosuggestion = {
      enable = true;
      # Manjaro's value for a 256-colour terminal. The plugin's own default is
      # fg=8, which several themes render as good as invisible.
      highlight = "fg=244";
    };

    syntaxHighlighting.enable = true;
    historySubstringSearch.enable = true;

    defaultKeymap = "emacs";
    autocd = true;

    history = {
      path = "${config.xdg.stateHome}/zsh/history";
      size = 100000;
      save = 100000;

      # HIST_STAMPS in the old rc file was an oh-my-zsh variable, and nothing
      # outside oh-my-zsh read it. This is the zsh option that actually writes
      # timestamps into the history file.
      extended = true;

      append = true; # appendhistory
      ignoreAllDups = true; # histignorealldups
      ignoreSpace = true; # histignorespace

      # The one deliberate departure from the Manjaro config, which used
      # inc_append_history: commands become visible to already-open terminals,
      # not just to ones started later. Set to false to get the old behaviour.
      share = true;
    };

    initContent = lib.mkMerge [
      # ---- before compinit (570) -------------------------------------------
      # fpath has to be complete before compinit runs, and the zstyles are
      # conventionally set here too.
      (lib.mkOrder 550 ''
        fpath+=(${kubectlCompletions})

        # compinit writes its dump here and does not create the directory; the
        # same is true of the completion cache below. Home Manager does the
        # same mkdir for HISTFILE a few lines further down.
        mkdir -p ${config.xdg.cacheHome}/zsh

        # Manjaro used the case-insensitive matcher only. This is a superset:
        # case-, hyphen- and underscore-insensitive, plus partial-word.
        # It also covers HYPHEN_INSENSITIVE, an oh-my-zsh variable the old
        # ~/.zshrc set and nothing read.
        zstyle ':completion:*' matcher-list \
          'm:{a-zA-Z-_}={A-Za-z_-}' 'r:|=*' 'l:|=* r:|=*'

        # $LS_COLORS is set by the block modules/shell/colors.nix contributes
        # at order 500, ahead of this one -- the zstyle copies the value here
        # and now, so an empty variable would mean an uncoloured menu.
        zstyle ':completion:*' list-colors "''${(s.:.)LS_COLORS}"
        zstyle ':completion:*' rehash true
        zstyle ':completion:*' menu select
        zstyle ':completion:*' accept-exact '*(N)'
        zstyle ':completion:*' use-cache on
        zstyle ':completion:*' cache-path ${config.xdg.cacheHome}/zsh/completion
      '')

      # ---- main body (1000) ------------------------------------------------
      ''
        # Options section, from manjaro-zsh-config. The history-related ones
        # are set through programs.zsh.history above instead.
        setopt correct          # offer a correction for a mistyped command
        setopt extendedglob     # regex-ish globbing
        setopt nocaseglob       # case insensitive globbing
        setopt rcexpandparam    # array expansion with parameters
        setopt nocheckjobs      # do not warn about running jobs when exiting
        setopt numericglobsort  # sort numbered filenames numerically
        setopt nobeep

        # zsh's default WORDCHARS minus `/` and `&`, so ctrl+w and the
        # word-motions below stop at a path component instead of swallowing the
        # whole path.
        #
        # Written out rather than carried over as manjaro-zsh-config's
        # `''${WORDCHARS//\/[&.;]}`, which reads as "remove / & . and ;" but
        # substitutes the two-character sequence `/&` -- `.` and `;` stay. The
        # literal below is byte-identical to what that expression produces and
        # says what it does.
        WORDCHARS='*?_-.[]~=;!#$%^(){}<>'

        autoload -Uz colors zcalc
        colors

        # Keybindings section, verbatim from manjaro-zsh-config: escape
        # sequences per terminal, with terminfo consulted where it has an
        # entry. Nothing here has a more modern spelling -- these are the codes
        # the keys actually send.
        bindkey '^[[7~' beginning-of-line                  # Home
        bindkey '^[[H'  beginning-of-line                  # Home
        if [[ -n "''${terminfo[khome]}" ]]; then
          bindkey "''${terminfo[khome]}" beginning-of-line
        fi
        bindkey '^[[8~' end-of-line                        # End
        bindkey '^[[F'  end-of-line                        # End
        if [[ -n "''${terminfo[kend]}" ]]; then
          bindkey "''${terminfo[kend]}" end-of-line
        fi
        bindkey '^[[2~' overwrite-mode                     # Insert
        bindkey '^[[3~' delete-char                        # Delete
        bindkey '^[[C'  forward-char
        bindkey '^[[D'  backward-char
        bindkey '^[[5~' history-beginning-search-backward  # PageUp
        bindkey '^[[6~' history-beginning-search-forward   # PageDown

        bindkey '^[Oc'    forward-word                     # ctrl+right
        bindkey '^[Od'    backward-word                    # ctrl+left
        bindkey '^[[1;5D' backward-word
        bindkey '^[[1;5C' forward-word
        bindkey '^H'      backward-kill-word               # ctrl+backspace
        bindkey '^[[Z'    undo                             # shift+tab

        # Terminal window/tab title and OSC 7, replacing the ~150 lines of
        # mzc_termsupport_* and zsh_urlencode in manjaro-zsh-config. Same two
        # effects: the tab shows what is running, and "open new tab" lands in
        # the current directory rather than in $HOME.
        #
        # The encoder is one substitution rather than a per-character loop:
        # (#m) exposes the matched character as $MATCH, and [##16] prints its
        # byte value in hex. LC_ALL=C makes the match iterate over bytes, which
        # is what gives correct multi-byte output for a path like ~/Документы.
        autoload -Uz add-zsh-hook

        _hm_urlencode() {
          emulate -L zsh -o extendedglob
          local LC_ALL=C
          print -rn -- "''${1//(#m)[^A-Za-z0-9_.!~*\'()\/-]/%''${(l:2::0:)$(([##16]#MATCH))}}"
        }

        _hm_osc7() {
          printf '\e]7;file://%s%s\a' "$HOST" "$(_hm_urlencode "$PWD")"
        }

        _hm_title_precmd() {
          print -Pn '\e]2;%n@%m: %~\a\e]1;%15<..<%~%<<\a'
        }

        _hm_title_preexec() {
          print -Pn "\e]2;''${1//\%/%%}\a\e]1;''${1//\%/%%}\a"
        }

        add-zsh-hook precmd  _hm_osc7
        add-zsh-hook precmd  _hm_title_precmd
        add-zsh-hook preexec _hm_title_preexec

        # ahmetb/kubectl-aliases: ~800 generated aliases, kept as a plain file
        # because that project generates it rather than a human writing it.
        [[ -r ''${HOME}/.kubectl_aliases ]] && source ''${HOME}/.kubectl_aliases
      ''

      # ---- after zsh-history-substring-search (1250) ------------------------
      # The module binds the raw ^[[A / ^[[B sequences itself. These are the
      # terminfo-derived ones Manjaro also bound, for terminals that send
      # something else.
      (lib.mkOrder 1300 ''
        if [[ -n "''${terminfo[kcuu1]}" ]]; then
          bindkey "''${terminfo[kcuu1]}" history-substring-search-up
        fi
        if [[ -n "''${terminfo[kcud1]}" ]]; then
          bindkey "''${terminfo[kcud1]}" history-substring-search-down
        fi
      '')
    ];
  };

  # The prompt. Replaces USE_POWERLINE + powerlevel10k from manjaro-zsh-prompt,
  # including its terminal-capability probe -- starship degrades on a dumb or
  # 8-colour terminal on its own, so the p10k.zsh / p10k-portable.zsh fork in
  # that file has no equivalent here and needs none.
  programs.starship = {
    enable = true;

    settings = {
      # An explicit `format` is the whole point of this block. Starship's
      # default is `$all`, which renders every module that detects itself --
      # and on this machine that meant a prompt like
      #
      #   ☸ arn:aws:eks:region:000000000000:cluster/example \
      #   workspace on  main on ☁️  (region)
      #
      # None of the noise was configured here. `aws` turns itself on as soon as
      # credentials are visible (AWS_ACCESS_KEY_ID or ~/.aws/credentials) and
      # then prints the region; `nodejs` and `python` fire on any package.json
      # or *.py lying around, so even $HOME grew `via  v26.7.0 via 🐍 v3.14.6`.
      #
      # Listing the modules explicitly drops all of them at once -- a module
      # absent from `format` and `right_format` is never rendered -- which is
      # shorter and more durable than a `disabled = true` per offender.
      #
      # No `$line_break` in the format and `add_newline = false`: one line per
      # prompt, the way the powerlevel10k config this replaced looked. Set
      # add_newline back to true for a blank separator between commands.
      #
      # No `$character` either: the left prompt stops at the closing wedge and
      # the trailing space is the gap the cursor sits in. `$character` is what
      # powerlevel10k's *lean* style ends with; rainbow, which this imitates,
      # ends at the last block and reports a failed command as a red `✘ <code>`
      # on the right instead -- see the `status` module below. The trailing
      # space is deliberate; without it the cursor lands on the wedge's tip.
      format = "$os$directory$git_branch$git_status ";
      right_format = "$status$cmd_duration$kubernetes";
      add_newline = false;

      # The powerlevel10k "rainbow" look: connected coloured blocks, each one
      # closed by a wedge in its own background colour over the next segment's.
      # Starship has no preset for it, so every transition is written by hand --
      # a segment's format ends with the separator that leads into the segment
      # after it, which is why the colours below are paired the way they are.
      #
      # The chain has to end in the colour of whichever segment turned out to
      # be last -- green in a repository, blue outside one -- and starship has
      # no `if` across modules. Two of its built-ins are git-aware, though, and
      # together they cover both cases at no runtime cost:
      #
      #   directory  -- `repo_root_format` replaces `format` inside a repo, so
      #                 `format` closes the blue segment for good and
      #                 `repo_root_format` hands over to green instead.
      #   git_status -- literal text in its `format` is emitted whenever the
      #                 shell is in a repo, even with nothing to report, so the
      #                 closing green wedge can live there and disappears on
      #                 its own outside one. The `(...)` group keeps the status
      #                 characters from leaving a stray space when clean.
      #
      # Whichever of the two fires, it is the last thing the left prompt
      # prints -- nothing follows it but the space in `format`.
      os = {
        disabled = false;
        format = "[ $symbol ](fg:bright-white bg:${osBg})[${sepRight}](fg:${osBg} bg:${dirBg})";
        # Starship's built-in symbols are emoji (🥭 for Manjaro, ❄️ for NixOS).
        # These are the Nerd Font equivalents, so the segment matches the rest.
        symbols = {
          Manjaro = iconManjaro;
          NixOS = iconNixos;
          Linux = iconLinux;
        };
      };

      directory = {
        truncation_length = 3;
        # Show where in the tree the repo actually is, not just its root
        # directory -- `~/.config/home-manager` rather than a bare
        # `home-manager` that reads the same from every checkout.
        truncate_to_repo = false;
        truncation_symbol = "…/";

        style = "fg:white bg:${dirBg}";
        before_repo_root_style = "fg:white bg:${dirBg}";
        # p10k printed the last path component brighter than its parents.
        # `repo_root_style` is the closest equivalent starship has, and it lands
        # on the same word whenever the current directory is a repository root.
        #
        # Setting it is also what switches `repo_root_format` on: leave the
        # style out and starship keeps using plain `format` inside repositories
        # too, which silently costs the conditional the chain depends on.
        repo_root_style = "bold fg:bright-white bg:${dirBg}";

        # Used outside a repository, where this is the last coloured segment --
        # so it closes on the terminal background rather than handing over.
        format = "[ ${iconFolder} $path ]($style)[${sepRight}](fg:${dirBg})";
        # Used inside one. The icon has to be repeated because this replaces
        # `format` wholesale; the separator leads into the git segment.
        repo_root_format = "[ ${iconFolder} $before_root_path]($before_repo_root_style)[$repo_root]($repo_root_style)[$path ]($style)[${sepRight}](fg:${dirBg} bg:${gitBg})";
      };

      git_branch.format = "[ ${iconGit} ${iconBranch} $branch ](fg:black bg:${gitBg})";
      git_status.format = "([$all_status$ahead_behind ](fg:black bg:${gitBg}))[${sepRight}](fg:${gitBg})";

      # What `$character` used to be here for: turning red after a failed
      # command. This says the same thing louder and with the number attached,
      # and it does it on the right, where a wide prompt cannot be pushed
      # around by it.
      #
      # Renders only on failure -- starship's status module is silent on exit
      # 0, so a successful prompt is unchanged. `$status` is the raw code, so
      # a Ctrl-C shows as `✘ 130`.
      status = {
        disabled = false;
        symbol = iconCross;
        # Same left-opening block as the kubernetes segment below.
        format = "[${sepLeft}](fg:${errBg})[ $symbol $status ](fg:bright-white bg:${errBg})";
      };

      # Off by default in starship and worth having here: the old rc file had
      # six hand-rolled aliases whose only job was answering "which context and
      # namespace am I in". It lives in right_format so a 30-character cluster
      # name can never push the command line around.
      kubernetes = {
        disabled = false;
        # Mirror of the left-hand chain: a left-pointing wedge opens the block
        # instead of a right-pointing one closing it.
        format = "[${sepLeft}](fg:purple)[ $symbol$context( \\($namespace\\)) ](fg:black bg:purple)";

        # EKS sets current-context to the cluster's full ARN, which is ~60
        # characters of account id and region. Nothing in it is worth prompt
        # space except the cluster name, so the ARN is matched and reduced to
        # the named capture group. Contexts that do not match (minikube, kind,
        # a plain kubeconfig) are left alone and print as-is.
        contexts = [
          {
            context_pattern = "arn:aws:eks:[\\w-]+:\\d+:cluster/(?P<cluster>[\\w-]+)";
            context_alias = "$cluster";
          }
        ];
      };

      # The kubernetes module reads and parses the kubeconfig on every prompt;
      # starship's default 500 ms budget is tight for a large one.
      command_timeout = 1000;
      cmd_duration = {
        min_time = 2000;
        format = "[ took $duration ](fg:yellow)";
      };
    };
  };
}
