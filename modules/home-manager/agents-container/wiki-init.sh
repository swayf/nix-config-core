#!/usr/bin/env bash
# Explicit paths and seed files only; never run upstream setup or index content.
set -euo pipefail
if [ "$#" -ne 9 ]; then
  echo 'usage: wiki-init.sh PROFILE ASSETS QMD_SKILL INSTRUCTIONS CONFIG_DIR LEGACY_CONFIG_DIR QMD_DIR CONFIG_SEED INDEX_SEED' >&2
  exit 2
fi
paths=()
for path in "$@"; do
  if [ -z "$path" ]; then
    echo 'wiki-init.sh: paths must not be empty' >&2
    exit 2
  fi
  # Absolute pathnames keep links valid with relative arguments, without
  # resolving source symlinks (atomic source replacements remain visible).
  [[ "$path" = /* ]] || path="$PWD/$path"
  paths+=("$path")
done
profile=${paths[0]}
assets=${paths[1]}
qmd_skill=${paths[2]}
instructions=${paths[3]}
config_dir=${paths[4]}
legacy_config_dir=${paths[5]}
qmd_dir=${paths[6]}
config_seed=${paths[7]}
index_seed=${paths[8]}
skills="$profile/wiki-skills"
mkdir -p "$skills"

# Keep managed wiki assets outside Pi's automatically discovered skills/ tree.
# Migrate only our links; user skills and overrides remain untouched.
for target in "$profile/skills"/*; do
  [ -L "$target" ] || continue
  case "$(readlink "$target")" in
    "$assets"/skills/*|"$qmd_skill")
      destination="$skills/$(basename "$target")"
      if [ ! -e "$destination" ] && [ ! -L "$destination" ]; then
        mv "$target" "$destination"
      elif [ -L "$destination" ] && [ "$(readlink "$destination")" = "$(readlink "$target")" ]; then
        rm "$target"
      fi
      ;;
  esac
done

if [ -L "$profile/AGENTS.md" ] && [ "$(readlink "$profile/AGENTS.md")" = "$instructions" ]; then
  rm "$profile/AGENTS.md"
fi
if [ -f "$instructions" ]; then
  target="$profile/wiki-instructions.md"
  if [ -L "$target" ] && [ "$(readlink "$target")" = "$instructions" ] ||
    { [ ! -e "$target" ] && [ ! -L "$target" ]; }; then
    ln -sfn "$instructions" "$target"
  fi
fi

# Remove retired managed skills, but preserve user directories/files/links.
for target in "$skills"/*; do
  [ -L "$target" ] || continue
  case "$(readlink "$target")" in
    "$assets"/skills/*)
      [ -f "$assets/skills/$(basename "$target")/SKILL.md" ] || rm "$target"
      ;;
  esac
done
for source in "$assets"/skills/*; do
  [ -f "$source/SKILL.md" ] || continue
  target="$skills/$(basename "$source")"
  override="$profile/skills/$(basename "$source")"
  [ ! -e "$override" ] && [ ! -L "$override" ] || continue
  if { [ ! -e "$target" ] && [ ! -L "$target" ]; } ||
    { [ -L "$target" ] && [ "$(readlink "$target")" = "$source" ]; }; then
    ln -sfn "$source" "$target"
  fi
done

target="$skills/qmd-cli"
if [ ! -e "$profile/skills/qmd-cli" ] && [ ! -L "$profile/skills/qmd-cli" ] &&
  { { [ ! -e "$target" ] && [ ! -L "$target" ]; } ||
    { [ -L "$target" ] && [ "$(readlink "$target")" = "$qmd_skill" ]; }; }; then
  ln -sfn "$qmd_skill" "$target"
fi

# Retain primary/legacy precedence, but never discover paths in HOME/XDG.
# Existing configs (including dangling links) remain caller-owned. Seed bytes
# are copied as data, without shell expansion, sourcing or interpretation.
if [ -d "$legacy_config_dir" ] && [ ! -e "$config_dir" ] && [ ! -L "$config_dir" ]; then
  config_dir="$legacy_config_dir"
fi
config="$config_dir/config"
if [ ! -e "$config" ] && [ ! -L "$config" ]; then
  # A missing caller seed must not leave an empty file blocking a later retry.
  [ -f "$config_seed" ] && [ -r "$config_seed" ] || { echo 'wiki-init.sh: config seed is not readable' >&2; exit 1; }
  if [ ! -e "$qmd_dir/index.yml" ] && [ ! -L "$qmd_dir/index.yml" ]; then
    [ -f "$index_seed" ] && [ -r "$index_seed" ] || { echo 'wiki-init.sh: index seed is not readable' >&2; exit 1; }
  fi
  mkdir -p "$config_dir"
  cat "$config_seed" > "$config"
  # The index is seeded only alongside a newly seeded config, as before.
  if [ ! -e "$qmd_dir/index.yml" ] && [ ! -L "$qmd_dir/index.yml" ]; then
    mkdir -p "$qmd_dir"
    cat "$index_seed" > "$qmd_dir/index.yml"
  fi
fi
