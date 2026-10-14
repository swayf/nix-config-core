#!/usr/bin/env bash
# Shared host/container delivery; preserve skills and local overrides.
set -euo pipefail
source=$1
python=${2:-python3}
for file in index.ts core.mjs; do
  if [ ! -f "$source/$file" ]; then
    printf 'Pi: missing lazy-capabilities source %s/%s\n' "$source" "$file" >&2
    exit 1
  fi
done
mkdir -p "$HOME/.pi/agent" "$HOME/.pi/profiles/"{general,code,wiki}
for agent_dir in "$HOME/.pi/agent" "$HOME"/.pi/profiles/*; do
  [ -d "$agent_dir" ] || continue
  # Do not install through aliases into another profile or an external directory.
  [ ! -L "$agent_dir" ] && [ ! -L "$agent_dir/extensions" ] || continue
  mkdir -p "$agent_dir/extensions"
  target="$agent_dir/extensions/lazy-capabilities"
  if [ -L "$target" ] || [ ! -e "$target" ]; then
    ln -sfnT "$source" "$target"
  else
    printf 'Pi: preserving local extension %s\n' "$target" >&2
    continue
  fi
  # Exclude only native discovery. The loader reads these directories on demand;
  # the shared skill remains available to other agents and is never moved.
  "$python" - "$agent_dir" <<'PY'
import json
import os
from pathlib import Path
import sys

settings = Path(sys.argv[1]) / "settings.json"
if settings.is_symlink():
    print(f"Pi: preserving linked settings {settings}", file=sys.stderr)
    sys.exit(0)
try:
    original = settings.read_bytes() if settings.exists() else None
    data = json.loads(original) if original is not None else {}
    if not isinstance(data, dict):
        raise ValueError("settings must be an object")
    skills = data.get("skills", [])
    if not isinstance(skills, list) or not all(isinstance(item, str) for item in skills):
        raise ValueError("skills must be a string array")
except (ValueError, OSError) as error:
    print(f"Pi: preserving invalid settings {settings}: {error}", file=sys.stderr)
    sys.exit(0)
exclusions = ["-~/.agents/skills/terminal-browser", "-skills/terminal-browser"]
updated = [*skills, *(item for item in exclusions if item not in skills)]
if updated == skills:
    sys.exit(0)
if original is not None:
    backup = settings.with_name("settings.json.before-lazy-browser")
    try:
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "wb") as stream:
            stream.write(original)
data["skills"] = updated
settings.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
settings.chmod(0o600)
PY
done
