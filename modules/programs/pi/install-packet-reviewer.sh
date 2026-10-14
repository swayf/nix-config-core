#!/usr/bin/env bash
# Code profile only, shared by Home Manager and the persistent agents home.
set -euo pipefail
source=$1
if [ ! -f "$source" ] || ! grep -Fxq 'name: packet-reviewer' "$source"; then
  printf 'Pi: missing or invalid packet-reviewer definition: %s\n' "$source" >&2
  exit 1
fi
# Keep absolute image paths stable across rebuilds; resolve relative callers.
if [[ "$source" != /* ]]; then
  source="$(cd -- "$(dirname -- "$source")" && pwd)/$(basename -- "$source")"
fi

agent_dir="$HOME/.pi/profiles/code"
# Never write through an aliased profile or agents directory.
for directory in "$HOME/.pi" "$HOME/.pi/profiles" "$agent_dir" "$agent_dir/agents"; do
  if [ -L "$directory" ] || { [ -e "$directory" ] && [ ! -d "$directory" ]; }; then
    printf 'Pi: preserving aliased/non-directory path %s\n' "$directory" >&2
    exit 0
  fi
done
mkdir -p "$agent_dir/agents"
target="$agent_dir/agents/packet-reviewer.md"
if [ -L "$target" ]; then
  old=$(readlink "$target")
  case "$old" in
    "$source"|/etc/agents/pi-agents/packet-reviewer.md|/nix/store/*-packet-reviewer.md) ;;
    *) printf 'Pi: preserving local agent %s\n' "$target" >&2; exit 0 ;;
  esac
elif [ -e "$target" ]; then
  printf 'Pi: preserving local agent %s\n' "$target" >&2
  exit 0
fi
ln -sfnT "$source" "$target"
