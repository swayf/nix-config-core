#!/usr/bin/env python3
"""Transport a caller-owned, hash-anchored notice set; never infer legal approval."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath


def relative(value):
    if not isinstance(value, str):
        raise ValueError('Expected a relative path string')
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(p in ('', '.', '..') for p in value.split('/')) or '\\' in value:
        raise ValueError('Unsafe relative path: ' + value)
    return path


def assemble(manifest, texts, binary, source):
    """Validate the complete set before making either output; preserve exact bytes."""
    manifest, texts = Path(manifest), Path(texts)
    binary, source = Path(binary), Path(source)
    if texts.is_symlink() or not texts.is_dir():
        raise ValueError('Expected a regular input directory')
    raw = manifest.read_bytes()
    data = json.loads(raw)
    if data.get('schema_version') != 1 or not isinstance(data.get('files'), list) or not data['files']:
        raise ValueError('Expected schema_version 1 and a nonempty files list')
    payload = {'manifest.json': raw}
    for item in data['files']:
        name, stored = str(relative(item['path'])), relative(item['stored'])
        if name in payload:
            raise ValueError('Duplicate or reserved output path: ' + name)
        path = texts
        for part in stored.parts:
            path = path / part
            if path.is_symlink():
                raise ValueError('Symlink input: ' + str(path))
        if not path.is_file():
            raise ValueError('Missing regular input: ' + str(path))
        content = path.read_bytes()
        if len(content) != item['bytes'] or hashlib.sha256(content).hexdigest() != item['sha256']:
            raise ValueError('Notice byte mismatch: ' + name)
        payload[name] = content
    names = set(payload)
    for name in names:
        if any(str(p) in names for p in PurePosixPath(name).parents if str(p) != '.'):
            raise ValueError('File/directory overlap: ' + name)
    if binary == source or binary in source.parents or source in binary.parents:
        raise ValueError('Outputs must be disjoint')
    for output in (binary, source):
        if output.exists() or output.is_symlink():
            raise ValueError('Refusing existing output: ' + str(output))
        for parent in output.parents:
            if parent.is_symlink():
                raise ValueError('Symlink output parent: ' + str(parent))
    for output in (binary, source):
        output.mkdir(parents=True)
        for name, content in sorted(payload.items()):
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    return {'files_per_layout': len(payload), 'bytes_per_layout': sum(map(len, payload.values()))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest', 'texts'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--binary', type=Path)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--package', help='Use Nix out/source environment outputs')
    args = parser.parse_args()
    if args.package:
        if args.binary or args.source or len(relative(args.package).parts) != 1:
            parser.error('--package requires a single name and no explicit output paths')
        args.binary = Path(os.environ['out']) / 'share/licenses' / args.package / 'third-party'
        args.source = Path(os.environ['source']) / 'third-party-notices' / args.package
    elif not args.binary or not args.source:
        parser.error('Supply --package or both --binary and --source')
    print(json.dumps(assemble(args.manifest, args.texts, args.binary, args.source), sort_keys=True))


if __name__ == '__main__':
    main()
