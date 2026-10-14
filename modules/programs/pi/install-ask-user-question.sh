#!/usr/bin/env bash
# Shared by Home Manager activation and the persistent agents container home.
set -euo pipefail
package=$1

# Ensure the standard profiles exist, then include every custom profile too.
mkdir -p "$HOME/.pi/agent" "$HOME/.pi/profiles/"{general,code,wiki}
for agent_dir in "$HOME/.pi/agent" "$HOME"/.pi/profiles/*; do
  [ -d "$agent_dir" ] || continue
  mkdir -p "$agent_dir/extensions"
  target="$agent_dir/extensions/rpiv-ask-user-question"
  if [ ! -e "$target" ] || [ -L "$target" ]; then
    ln -sfn "$package" "$target"
  else
    printf 'Pi: preserving local extension %s\n' "$target" >&2
  fi
done
