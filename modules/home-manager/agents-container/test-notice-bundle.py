"""Byte transport tests, not a completeness or legal-compatibility assessment."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('bundle', Path(__file__).with_name('notice-bundle.py'))
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


class NoticeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.texts = self.root / 'texts'
        self.texts.mkdir()
        self.raw = b'Copyright fixture\nMIT AND Unicode-fixture\n'
        (self.texts / 'blob.txt').write_bytes(self.raw)
        self.record = {'path': 'component/LICENSE', 'stored': 'blob.txt', 'bytes': len(self.raw),
                       'sha256': hashlib.sha256(self.raw).hexdigest()}
        self.data = {'schema_version': 1, 'acceptance': {'complete_bundle': False},
                     'files': [self.record]}
        self.manifest = self.root / 'manifest.json'
        self.binary = self.root / 'binary'
        self.source = self.root / 'source'

    def assemble(self):
        self.manifest.write_text(json.dumps(self.data))
        return bundle.assemble(self.manifest, self.texts, self.binary, self.source)

    def reject(self):
        with self.assertRaises(ValueError):
            self.assemble()
        self.assertFalse(self.binary.exists())
        self.assertFalse(self.source.exists())

    def test_both_layouts_exact_bytes_and_declarations(self):
        receipt = self.assemble()
        self.assertEqual(receipt['files_per_layout'], 2)
        for output in (self.binary, self.source):
            self.assertEqual((output / self.record['path']).read_bytes(), self.raw)
            self.assertEqual((output / 'manifest.json').read_bytes(), self.manifest.read_bytes())
            self.assertFalse(json.loads((output / 'manifest.json').read_bytes())['acceptance']['complete_bundle'])
        with self.assertRaises(ValueError):
            self.assemble()

    def test_corruption_size_and_missing_fail_before_output(self):
        for raw in (b'X' + self.raw[1:], self.raw[:-1]):
            (self.texts / 'blob.txt').write_bytes(raw)
            self.reject()
        (self.texts / 'blob.txt').unlink()
        self.reject()

    def test_duplicate_reserved_and_parent_overlap(self):
        for name in ('component/LICENSE', 'manifest.json', 'component'):
            self.data['files'] = [self.record, {**self.record, 'path': name}]
            self.reject()

    def test_unsafe_paths_in_either_field(self):
        for field in ('path', 'stored'):
            for path in ('', '/absolute', '../escape', './dot', 'a//b', 'a/../b', 'a\\b'):
                self.data['files'] = [{**self.record, field: path}]
                self.reject()

    def test_symlink_file_and_parent(self):
        (self.texts / 'link').symlink_to(self.texts / 'blob.txt')
        self.data['files'] = [{**self.record, 'stored': 'link'}]
        self.reject()
        (self.texts / 'dir').symlink_to(self.texts, target_is_directory=True)
        self.data['files'] = [{**self.record, 'stored': 'dir/blob.txt'}]
        self.reject()

    def test_invalid_last_entry_creates_neither_layout(self):
        self.data['files'].append({**self.record, 'path': 'other/LICENSE', 'sha256': '0' * 64})
        self.reject()

    def test_disjoint_outputs_and_symlink_parent(self):
        self.source = self.binary / 'child'
        self.reject()
        self.source = self.root / 'source'
        actual = self.root / 'actual'
        actual.mkdir()
        link = self.root / 'link'
        link.symlink_to(actual, target_is_directory=True)
        self.binary = link / 'binary'
        self.reject()
        self.assertEqual(list(actual.iterdir()), [])

    def test_no_defaults_or_empty_payload(self):
        for data in ({}, {'schema_version': 2, 'files': [self.record]}, {'schema_version': 1, 'files': []}):
            self.data = copy.deepcopy(data)
            self.reject()


if __name__ == '__main__':
    unittest.main()
