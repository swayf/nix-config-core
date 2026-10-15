"""Link profile appends from caller-selected shared and fallback directories."""

import argparse
from pathlib import Path


def install(profiles, shared, defaults):
    # Keep pathnames (not resolved inodes) so atomic edits remain visible.
    profiles, shared, defaults = (Path(path).absolute() for path in (profiles, shared, defaults))
    if not profiles.is_dir():
        return
    for profile in sorted(profiles.iterdir()):
        if not profile.is_dir():
            continue
        target = profile / "APPEND_SYSTEM.md"
        shared_file = shared / profile.name / target.name
        default_file = defaults / profile.name / target.name
        source = shared_file if shared_file.is_file() else default_file
        if not source.is_file():
            continue
        # Upgrade only our known links, never local files or user-owned links,
        # including dangling user links. Link the pathname, not the file inode,
        # so editor atomic saves are visible without reinitialization.
        if target.is_symlink():
            if target.readlink() not in (shared_file, default_file):
                continue
            if target.readlink() == source:
                continue
            target.unlink()
        elif target.exists():
            continue
        target.symlink_to(source)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profiles", type=Path)
    parser.add_argument("shared", type=Path)
    parser.add_argument("defaults", type=Path)
    args = parser.parse_args()
    install(args.profiles, args.shared, args.defaults)
