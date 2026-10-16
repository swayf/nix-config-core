# modules/shell/colors.nix
#
# Every colour a file listing prints, in one place: LS_COLORS (read by ls, by
# eza and by the zsh completion menu) and eza's own theme.yml (the columns
# LS_COLORS has no codes for -- permissions, owner, date, git status).
#
# WHY IT EXISTS
#
# Measured off a screenshot of `eza -la` in this terminal, whose background is
# #222222. Contrast ratios, WCAG formula, text this size wants 4.5:1:
#
#   file name         #ffffff   15.9:1   fine
#   size              #26a269    4.9:1   fine
#   owner name        #a2734c    3.9:1   thin
#   permission bits   #65674c    2.4:1   thin
#   `-` filler        #5e5c64    2.4:1   thin
#   directory name    #12488b    1.8:1   the glyph shape is a guess
#   date column       #12488b    1.6:1   the glyph shape is a guess
#
# Two independent causes, and neither is the terminal being "too dark" -- the
# same background carries white filenames at 15.9:1:
#
#   * dircolors' database was written for a terminal that renders bold as
#     *bright*, where `di=01;34` lands in the bright blue slot -- around
#     #51a1ff in a palette of this family, near 7:1. This one renders bold as
#     bold, so `01;34` stays the normal slot, and blue is the darkest entry
#     this palette has.
#
#   * eza's built-in theme paints its metadata columns in normal-intensity
#     palette colours: `date = Blue`, `punctuation = DarkGray.bold`,
#     `user_you = Yellow.bold`. Same dark blue, again.
#
# The two fixes mirror the two causes. For LS_COLORS: promote every `01;3x` to
# its bright `01;9x` twin -- exactly what a bold-is-bright terminal would have
# done, done explicitly so it no longer depends on that setting. For eza's own
# columns: pin them to fixed hexes from the xterm-256 cube, which no palette
# can darken, keeping each hue where it was and moving only its luminance.
#
# Nothing here touches the file names themselves. They were already the
# brightest thing on the line and should stay the thing the eye lands on.
{
  pkgs,
  lib,
  ...
}:

let
  # `eval "$(dircolors -b)"` in an rc file runs dircolors on every single shell
  # start to print a fixed 1.9 KB. Nothing in it depends on the session, so it
  # is a file built once, not a subprocess.
  #
  # The database is dircolors' own (`-p`), edited on the way through, rather
  # than a hand-written one: `dircolors -b FILE` takes *only* what FILE lists
  # -- it does not merge with the built-in defaults -- so a file holding just
  # the overrides would silently drop the other ~150 entries.
  #
  #   $ printf 'DIR 01;94\n' | dircolors -b -   # -> three entries, not 163
  #
  # The first expression is the bold-to-bright promotion described above; it
  # catches DIR, LINK, EXEC, SOCK and every `.tar`/`.jpg`-style extension in
  # one rule. The rest are the entries dircolors paints on a coloured
  # background, where the promotion does not apply and the pair is unreadable
  # or merely ugly:
  #
  #   OTHER_WRITABLE  34;42     dark blue on green, the worst pair shipped
  #   FIFO            40;33     dark yellow on black
  #   BLK CHR         40;33;01  same, bold
  #   ORPHAN          40;31;01  dark red on black
  #
  # ORPHAN is the one eza also reads (`or`, a broken symlink); the other three
  # reach ls and the zsh completion menu only -- eza has no code for them.
  #
  # The promotion still borrows the terminal's own bright slots, so it follows
  # the theme rather than fighting it. If a future theme's bright blue is dark
  # too, the escape hatch is one more expression pinning the hex the way the
  # eza block below does:
  #
  #   -e 's/^DIR[[:space:]].*/DIR 01;38;5;110/'   # #87afd7, 6.9:1
  lsColors = pkgs.runCommand "ls-colors.zsh" { } ''
    ${pkgs.coreutils}/bin/dircolors -p \
      | ${pkgs.gnused}/bin/sed -E \
          -e 's/^([^ #][^ ]*)([[:space:]]+)01;3([1-7])/\1\201;9\3/' \
          -e 's/^FIFO[[:space:]].*/FIFO 93/' \
          -e 's/^(BLK|CHR)[[:space:]].*/\1 01;93/' \
          -e 's/^ORPHAN[[:space:]].*/ORPHAN 01;91/' \
          -e 's/^OTHER_WRITABLE[[:space:]].*/OTHER_WRITABLE 01;94;04/' \
      > database

    ${pkgs.coreutils}/bin/dircolors -b database > "$out"
  '';
in
{
  # Ahead of the 550 block in modules/shell/zsh.nix on purpose: the
  # `list-colors` zstyle there reads $LS_COLORS at the moment it is set, so the
  # variable has to exist first.
  programs.zsh.initContent = lib.mkOrder 500 ''
    source ${lsColors}
  '';

  # eza's own columns. Written to ~/.config/eza/theme.yml, which eza layers
  # over its built-in theme -- every key left out below keeps eza's value, so
  # this file is the diff, not a replacement theme.
  #
  # LS_COLORS is applied *after* theme.yml and wins, but only for the nine
  # codes eza reads from it (di, ex, fi, pi, so, bd, cd, ln, or). Those belong
  # to the derivation above; everything below is eza-only and cannot collide.
  #
  # One behavioural side effect, from eza's own code: a theme.yml switches the
  # base off `default_theme(colour_scale)` and onto the plain default, so
  # `--color-scale=size` would no longer tint sizes by magnitude. Identical to
  # today's output, which is that flag unset -- worth knowing before reaching
  # for it.
  #
  # Hex values are xterm-256 cube entries (#d7af87 is 180, #87d787 is 114 and
  # so on), so a terminal without truecolour degrades to the nearest index
  # rather than to something arbitrary. The ratio after each is against
  # #222222.
  programs.eza.theme = {
    # eza already separates the owner's triad from group's and other's by
    # weight alone. Keeping that split and making it a luminance step too: the
    # owner's bits are the bright set, group and other the muted one. Hues are
    # eza's -- amber reads, rose writes, green executes.
    perms = {
      user_read = {
        foreground = "#d7af87";
        is_bold = true;
      }; # 7.9:1
      user_write = {
        foreground = "#d78787";
        is_bold = true;
      }; # 5.8:1
      user_execute_file = {
        foreground = "#87d787";
        is_bold = true;
        is_underline = true;
      }; # 9.2:1, underline is eza's own mark for it
      user_execute_other = {
        foreground = "#87d787";
        is_bold = true;
      };

      group_read = {
        foreground = "#af875f";
      }; # 4.9:1
      group_write = {
        foreground = "#af8787";
      }; # 5.0:1
      group_execute = {
        foreground = "#87af87";
      }; # 6.5:1

      other_read = {
        foreground = "#af875f";
      };
      other_write = {
        foreground = "#af8787";
      };
      other_execute = {
        foreground = "#87af87";
      };

      # setuid/setgid, the `s` in place of an `x`. Purple in eza too; this is
      # the readable purple.
      special_user_file = {
        foreground = "#af87af";
      }; # 5.2:1
      special_other = {
        foreground = "#af87af";
      };

      # The `@` marking an extended attribute. Uncoloured by default, which
      # leaves it in the filename's white -- louder than the flag deserves.
      attribute = {
        foreground = "#8a8a8a";
      }; # 4.6:1
    };

    # The current user's name/group on every line of every listing is the least informative
    # column there is, and eza prints it in bold yellow. Grey, and a step
    # behind the group.
    #
    # user_other and user_root are left alone deliberately: eza gives them no
    # colour, so they inherit the terminal foreground (15.9:1) and a file that
    # is *not* yours keeps standing out.
    users = {
      user_you = {
        foreground = "#949494";
      }; # 5.2:1, was bold yellow at 3.9:1
      group_yours = {
        foreground = "#808080";
      }; # 4.0:1
    };

    date = {
      foreground = "#949494";
    }; # 5.2:1, was Blue at 1.6:1

    # The `-` standing in for a size a directory does not have, and the dots
    # between date fields. Dim on purpose -- it is filler, and 3.5:1 is enough
    # to see that the column is empty rather than misread.
    punctuation = {
      foreground = "#767676";
    }; # 3.5:1

    # `eza -l --header`. Underlined by default and otherwise unstyled.
    header = {
      foreground = "#d0d0d0";
      is_bold = true;
      is_underline = true;
    }; # 10.3:1

    # eza's fallback for a name LS_COLORS says nothing about. The database
    # above covers archives, images and audio by extension and wins where it
    # has an entry; this is what is left -- source, documents, build files,
    # build output -- which is most of what a project directory contains, and
    # eza painted it in the same normal-intensity slots (`source` was bold
    # yellow, 3.9:1).
    file_type = {
      source = {
        foreground = "#d7af87";
      }; # 7.9:1
      build = {
        foreground = "#d7af87";
        is_bold = true;
        is_underline = true;
      };
      compiled = {
        foreground = "#af875f";
      }; # 4.9:1
      document = {
        foreground = "#87af87";
      }; # 6.5:1
      compressed = {
        foreground = "#d78787";
      }; # 5.8:1
      crypto = {
        foreground = "#87d787";
        is_bold = true;
      }; # 9.2:1
      image = {
        foreground = "#af87af";
      }; # 5.2:1
      video = {
        foreground = "#af87af";
        is_bold = true;
      };
      music = {
        foreground = "#87afaf";
      }; # 6.7:1
      lossless = {
        foreground = "#87d7d7";
      }; # 9.7:1
      # Editor backups and `.tmp`: the one case where being hard to read is
      # the point.
      temp = {
        foreground = "#6c6c6c";
      }; # 3.0:1
    };

    # The two-character git column that `programs.eza.git = true` turns on
    # (modules/programs/cli.nix). `modified` was the same 1.6:1 blue as the
    # date, in a column two characters wide -- the least legible thing eza
    # printed.
    git = {
      new = {
        foreground = "#87d787";
      }; # 9.2:1
      modified = {
        foreground = "#d7af87";
      }; # 7.9:1
      deleted = {
        foreground = "#d78787";
      }; # 5.8:1
      renamed = {
        foreground = "#87afd7";
      }; # 6.9:1
      typechange = {
        foreground = "#af87af";
      }; # 5.2:1
      ignored = {
        foreground = "#6c6c6c";
      }; # 3.0:1, deliberately the quietest entry here
      conflicted = {
        foreground = "#d78787";
        is_bold = true;
      };
    };
  };
}
