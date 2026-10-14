#!/usr/bin/env bash
# Shared by Home Manager and the persistent agents container home.
set -euo pipefail
skill=$1

# Refuse a broken source before creating or replacing any links.
if [ ! -f "$skill/SKILL.md" ]; then
  printf 'Pi: missing brainstorming skill %s/SKILL.md\n' "$skill" >&2
  exit 1
fi

mkdir -p "$HOME/.pi/agent" "$HOME/.pi/profiles/"{general,code,wiki}
for agent_dir in "$HOME/.pi/agent" "$HOME"/.pi/profiles/*; do
  [ -d "$agent_dir" ] || continue
  mkdir -p "$agent_dir/skills"
  target="$agent_dir/skills/brainstorming"
  if [ -L "$target" ] || [ ! -e "$target" ]; then
    ln -sfnT "$skill" "$target"
  else
    printf 'Pi: preserving local skill %s\n' "$target" >&2
  fi
done
