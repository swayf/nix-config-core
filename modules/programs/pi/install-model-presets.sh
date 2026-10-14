#!/usr/bin/env bash
# Shared by Home Manager activation and the persistent agents container home.
set -euo pipefail
extension=$1
defaults=$2

mkdir -p "$HOME/.pi/agent" "$HOME/.pi/profiles/"{general,code,wiki}
# Migrate the former shared list by copying it, never by linking profiles.
# Keep the old file as a backup; the extension no longer reads it.
seed="$defaults"
legacy="$HOME/.pi/model-presets.json"
if [ -f "$legacy" ] && [ -r "$legacy" ]; then
  seed="$legacy"
fi
for agent_dir in "$HOME/.pi/agent" "$HOME"/.pi/profiles/*; do
  [ -d "$agent_dir" ] || continue
  # Seed writable independent lists once; preserve edits and user-owned links.
  config="$agent_dir/model-presets.json"
  if [ ! -e "$config" ] && [ ! -L "$config" ]; then
    cp "$seed" "$config"
    chmod u+w "$config"
  fi
  mkdir -p "$agent_dir/extensions"
  target="$agent_dir/extensions/model-presets.ts"
  if [ -L "$target" ] || [ ! -e "$target" ]; then
    ln -sfnT "$extension" "$target"
  else
    printf 'Pi: preserving local extension %s\n' "$target" >&2
  fi
done
