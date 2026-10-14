#!/usr/bin/env bash
# Code profile only, shared by Home Manager and the persistent agents home.
set -euo pipefail
# Resolve relative invocations before creating a link in another directory.
package=$(cd -- "$1" && pwd -P)
for file in index.ts core.mjs README.md; do
  if [ ! -f "$package/$file" ]; then
    printf 'Pi: missing context-handoff package file: %s/%s\n' "$package" "$file" >&2
    exit 1
  fi
done

agent_dir="$HOME/.pi/profiles/code"
# Never write through an aliased profile or extensions directory.
for directory in "$HOME/.pi" "$HOME/.pi/profiles" "$agent_dir" "$agent_dir/extensions"; do
  if [ -L "$directory" ] || { [ -e "$directory" ] && [ ! -d "$directory" ]; }; then
    printf 'Pi: preserving aliased/non-directory path %s\n' "$directory" >&2
    exit 0
  fi
done
mkdir -p "$agent_dir/extensions"
target="$agent_dir/extensions/context-handoff"
if [ -L "$target" ]; then
  old=$(readlink "$target")
  case "$old" in
    "$package"|/etc/agents/pi-extensions/context-handoff|/nix/store/*-context-handoff) ;;
    *) printf 'Pi: preserving local extension %s\n' "$target" >&2; exit 0 ;;
  esac
elif [ -e "$target" ]; then
  printf 'Pi: preserving local extension %s\n' "$target" >&2
  exit 0
fi
ln -sfnT "$package" "$target"
