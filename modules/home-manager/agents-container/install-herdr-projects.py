#!/usr/bin/env python3
"""Seed writable Plus project/worktree templates once, without opening panes."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile
import tomllib


MARKER = ".workspace-templates-v1"


def copy_new(path, content):
    """Publish a complete file exclusively; preserve files and dangling symlinks."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            os.chmod(temporary, 0o644)
            try:
                os.link(temporary, path)
            except FileExistsError:
                return False
        finally:
            temporary.unlink(missing_ok=True)
    return True


def install(source, config_dir):
    source, config_dir = Path(source), Path(config_dir)
    marker = config_dir / MARKER
    if marker.exists() or marker.is_symlink():
        return []
    # Parse every input before creating anything in the user's configuration.
    templates = []
    for directory in ("projects", "worktrees"):
        files = sorted((source / directory).glob("*.toml"))
        if not files:
            raise ValueError(f"No templates in {source / directory}")
        for path in files:
            content = path.read_bytes()
            tomllib.loads(content.decode())
            templates.append((Path(directory) / path.name, content))
    added = []
    for relative, content in templates:
        target = config_dir / relative
        if copy_new(target, content):
            added.append(target)
    # Deleting/editing an installed template remains a user choice on rebuild.
    copy_new(marker, b"Initial workspace templates installed; user edits and deletions are preserved.\n")
    return added


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--config-dir", type=Path, help="Override Plus's reported config directory")
    args = parser.parse_args()
    config_dir = args.config_dir
    if config_dir is None:
        config_dir = Path(subprocess.check_output(
            ["herdr", "plugin", "config-dir", "cloudmanic.herdr-plus"], text=True
        ).strip())
    for path in install(args.source, config_dir):
        print(f"Installed Herdr Plus template: {path}")


if __name__ == "__main__":
    main()
