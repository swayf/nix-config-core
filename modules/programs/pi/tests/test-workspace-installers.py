#!/usr/bin/env python3
"""Synthetic workspace delivery contracts; temporary HOME, no Pi or network."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PI = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), PI / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WorkspaceInstallers(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pi-synthetic-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home with spaces'
        self.package = self.root / 'fixture-package'
        self.package.mkdir()
        for name in ('index.ts', 'core.mjs', 'core.test.mjs', 'SKILL.md', 'README.md'):
            (self.package / name).write_text('synthetic fixture\n')
        self.extension = self.root / 'fixture.ts'
        self.extension.write_text('export default function () {}\n')
        self.rubric = self.root / 'fixture.md'
        self.rubric.write_text('---\nname: packet-reviewer\n---\nSynthetic rubric.\n')
        self.seed = self.root / 'fixture.json'
        self.seed.write_text('[{"name":"synthetic"}]\n')
        self.profiles = [self.home / '.pi/agent'] + [self.home / '.pi/profiles' / name
                        for name in ('general', 'code', 'wiki', 'custom profile')]
        self.originals = {}
        for profile in self.profiles:
            profile.mkdir(parents=True)
            for name in ('settings.json', 'auth.json'):
                path = profile / name
                path.write_text('{"keep":"synthetic"}\n')
                self.originals[path] = path.read_bytes()

    def install(self, name, *args):
        subprocess.run(['bash', str(PI / ('install-' + name + '.sh')), *map(str, args)],
                       env={**os.environ, 'HOME': str(self.home)}, cwd=self.root,
                       check=True, capture_output=True, timeout=20)

    def unchanged(self):
        for path, data in self.originals.items():
            self.assertEqual(path.read_bytes(), data)

    def test_simple_links_repeat_upgrade_preserve_files_and_repair_dangling(self):
        for name in ('bell', 'statusline', 'compact-prompts', 'compact-tools'):
            with self.subTest(installer=name):
                local = self.profiles[1] / 'extensions' / (name + '.ts')
                local.parent.mkdir(exist_ok=True)
                local.write_text('local override\n')
                dangling = self.profiles[2] / 'extensions' / (name + '.ts')
                dangling.parent.mkdir(exist_ok=True)
                dangling.symlink_to(self.root / 'missing')
                for _ in range(2):
                    self.install(name, self.extension)
                    self.assertEqual(local.read_text(), 'local override\n')
                    self.assertEqual(dangling.readlink(), self.extension)
                    for profile in (self.profiles[0], self.profiles[3], self.profiles[4]):
                        self.assertEqual((profile / 'extensions' / (name + '.ts')).readlink(), self.extension)
                self.unchanged()

    def test_seed_is_writable_independent_and_existing_presets_are_kept(self):
        local = self.profiles[1] / 'model-presets.json'
        local.write_text('["local"]\n')
        for _ in range(2):
            self.install('model-presets', self.extension, self.seed)
        self.assertEqual(local.read_text(), '["local"]\n')
        for profile in (self.profiles[0], self.profiles[2], self.profiles[3], self.profiles[4]):
            path = profile / 'model-presets.json'
            self.assertFalse(path.is_symlink())
            self.assertEqual(path.read_bytes(), self.seed.read_bytes())
            self.assertTrue(path.stat().st_mode & 0o200)
        self.unchanged()

    def test_brainstorming_preserves_local_skill(self):
        local = self.profiles[1] / 'skills/brainstorming'
        local.mkdir(parents=True)
        (local / 'SKILL.md').write_text('local\n')
        for _ in range(2):
            self.install('brainstorming', self.package)
        self.assertEqual((local / 'SKILL.md').read_text(), 'local\n')
        for profile in (self.profiles[0], self.profiles[2], self.profiles[3], self.profiles[4]):
            self.assertEqual((profile / 'skills/brainstorming').readlink(), self.package)
        self.unchanged()

    def test_wiki_delivery_excludes_wiki_and_preserves_local_directory(self):
        local = self.profiles[4] / 'extensions/wiki-ask'
        local.mkdir(parents=True)
        (local / 'index.ts').write_text('local\n')
        for _ in range(2):
            self.install('wiki-ask', self.package)
        self.assertFalse((self.profiles[3] / 'extensions/wiki-ask').exists())
        self.assertEqual((local / 'index.ts').read_text(), 'local\n')
        for profile in self.profiles[:3]:
            self.assertEqual((profile / 'extensions/wiki-ask').readlink(), self.package)
        self.unchanged()

    def test_code_only_links_keep_unknown_and_refresh_managed_links(self):
        for name, source, relative, managed in (
            ('packet-reviewer', self.rubric, 'agents/packet-reviewer.md', '/etc/agents/pi-agents/packet-reviewer.md'),
            ('context-handoff', self.package, 'extensions/context-handoff', '/etc/agents/pi-extensions/context-handoff'),
        ):
            with self.subTest(installer=name):
                target = self.profiles[2] / relative
                target.parent.mkdir(exist_ok=True)
                target.symlink_to(self.root / 'unknown-missing')
                self.install(name, source)
                self.assertEqual(target.readlink(), self.root / 'unknown-missing')
                target.unlink()
                target.symlink_to(managed)
                for _ in range(2):
                    self.install(name, source)
                    self.assertEqual(target.readlink(), source)
                for profile in (self.profiles[0], self.profiles[1], self.profiles[3], self.profiles[4]):
                    self.assertFalse((profile / relative).exists())
                self.unchanged()

    def test_lazy_exclusions_are_idempotent_and_other_settings_survive(self):
        for _ in range(2):
            self.install('lazy-capabilities', self.package, sys.executable)
        for profile in self.profiles:
            saved = json.loads((profile / 'settings.json').read_text())
            self.assertEqual(saved['keep'], 'synthetic')
            self.assertEqual(saved['skills'], ['-~/.agents/skills/terminal-browser', '-skills/terminal-browser'])
            self.assertEqual((profile / 'auth.json').read_bytes(), self.originals[profile / 'auth.json'])

    def test_disable_bell_preserves_settings_and_dangling_links(self):
        helper = load('disable-bell')
        linked = self.profiles[4] / 'settings.json'
        linked.unlink()
        linked.symlink_to(self.root / 'missing-settings')
        for _ in range(2):
            helper.install(self.home)
        self.assertEqual(linked.readlink(), self.root / 'missing-settings')
        for profile in self.profiles[:4]:
            data = json.loads((profile / 'settings.json').read_text())
            self.assertEqual(data, {'keep': 'synthetic', 'extensions': ['-extensions/bell.ts']})
            self.assertEqual((profile / 'auth.json').read_bytes(), self.originals[profile / 'auth.json'])

    def test_empty_web_overlay_installs_package_without_private_defaults(self):
        helper = load('install-web-access')
        for _ in range(2):
            helper.install(self.home, '/nix/store/synthetic-pi-web-access-0.35.0', {}, self.home / '.config')
        for profile in self.profiles:
            data = json.loads((profile / 'settings.json').read_text())
            self.assertEqual(data['keep'], 'synthetic')
            self.assertEqual(data['packages'], ['/nix/store/synthetic-pi-web-access-0.35.0'])
            self.assertEqual(json.loads((profile / 'web-search.json').read_text()), {})
            self.assertEqual((profile / 'auth.json').read_bytes(), self.originals[profile / 'auth.json'])


if __name__ == '__main__':
    unittest.main()
