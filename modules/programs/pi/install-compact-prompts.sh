#!/usr/bin/env bash
# Shared by Home Manager and the persistent agents container home.
set -euo pipefail
extension=$1

mkdir -p "$HOME/.pi/agent" "$HOME/.pi/profiles/"{general,code,wiki}
for agent_dir in "$HOME/.pi/agent" "$HOME"/.pi/profiles/*; do
  [ -d "$agent_dir" ] || continue
  mkdir -p "$agent_dir/extensions"
  target="$agent_dir/extensions/compact-prompts.ts"
  if [ -L "$target" ] || [ ! -e "$target" ]; then
    ln -sfnT "$extension" "$target"
  else
    printf 'Pi: preserving local extension %s\n' "$target" >&2
  fi
done
