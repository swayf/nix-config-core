#!/usr/bin/env python3
"""Test writable VS Code session settings without activating or changing HOME."""
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("session", ROOT / "modules/home-manager/desktop/vscode/session.py")
session = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(session)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.defaults = self.root / "defaults"
        self.defaults.mkdir()
        self.user = self.root / "Code"
        for profile in ("default", "pycode", "angcode", "odcode"):
            (self.defaults / profile).write_text(json.dumps({"profile": profile}))

    def target(self, profile="default"):
        directory = self.user / "User"
        if profile != "default":
            directory = directory / "profiles" / profile
        return directory / "settings.json"

    def test_restore_all_profiles_as_writable_files(self):
        session.restore(self.defaults, self.user)
        for source in self.defaults.iterdir():
            target = self.target(source.name)
            self.assertFalse(target.is_symlink())
            self.assertEqual(target.read_bytes(), source.read_bytes())
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            target.write_text("session change")
        session.restore(self.defaults, self.user)
        self.assertEqual(self.target().read_bytes(), (self.defaults / "default").read_bytes())

    def test_migrate_store_symlink_without_modifying_source(self):
        source = self.defaults / "default"
        source.chmod(0o444)
        target = self.target()
        target.parent.mkdir(parents=True)
        target.symlink_to(source)
        session.restore(self.defaults, self.user)
        target.write_text("changed")
        self.assertFalse(target.is_symlink())
        self.assertEqual(json.loads(source.read_text()), {"profile": "default"})

    def test_migrate_dangling_symlink(self):
        target = self.target()
        target.parent.mkdir(parents=True)
        target.symlink_to(self.root / "missing")
        session.restore(self.defaults, self.user)
        self.assertFalse(target.is_symlink())
        self.assertTrue(target.is_file())

    def test_queries_and_alternate_directories_bypass_reset(self):
        for arguments in (["--version"], ["--list-extensions"], ["--status"],
                          ["--user-data-dir", "/tmp/other"], ["--user-data-dir=/tmp/other"]):
            self.assertTrue(session.bypass_reset(arguments), arguments)
        for arguments in ([], ["--new-window"], ["--profile", "pycode"],
                          ["--wait", "a file.py"], ["--diff", "a", "b"]):
            self.assertFalse(session.bypass_reset(arguments), arguments)

    def test_process_detection_ignores_zombies_and_other_commands(self):
        proc = self.root / "proc"
        proc.mkdir()
        entry = proc / "123"
        entry.mkdir()
        (entry / "comm").write_text("code\n")
        (entry / "status").write_text("State:\tS (sleeping)\n")
        self.assertTrue(session.editor_running(proc))
        (entry / "status").write_text("State:\tZ (zombie)\n")
        self.assertFalse(session.editor_running(proc))
        (entry / "comm").write_text("python3\n")
        self.assertFalse(session.editor_running(proc))
        (entry / "comm").write_text("code\n")
        (entry / "status").write_text("State:\tS (sleeping)\n")
        with patch.object(session.os, "getuid", return_value=os.getuid() + 1):
            self.assertFalse(session.editor_running(proc))

    def test_warm_launch_keeps_settings_and_forwards_arguments(self):
        session.restore(self.defaults, self.user)
        self.target().write_text("session change")
        editor = self.root / "editor"
        editor.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\nexit 7\n")
        editor.chmod(0o700)
        with patch.object(session, "editor_running", return_value=True):
            self.assertEqual(session.launch(self.defaults, self.user, str(editor),
                                            ["--profile", "pycode", "a file.py"]), 7)
        self.assertEqual(self.target().read_text(), "session change")

    def test_concurrent_cold_launches_reset_only_once_and_do_not_block_wait(self):
        marker = self.root / "running"
        log = self.root / "log"
        editor = self.root / "editor"
        editor.write_text(f'''#!{os.sys.executable}
from pathlib import Path
import time
import sys
time.sleep(0.1)
target = Path({str(self.target())!r})
with Path({str(log)!r}).open("a") as stream:
    stream.write(target.read_text() + "\\n")
target.write_text("session change")
Path({str(marker)!r}).touch()
if "--wait" in sys.argv:
    time.sleep(1)
''')
        editor.chmod(0o700)
        ctx = multiprocessing.get_context("fork")
        with patch.object(session, "editor_running", side_effect=marker.exists):
            first = ctx.Process(target=session.launch, args=(self.defaults, self.user, str(editor), ["--wait"]))
            second = ctx.Process(target=session.launch, args=(self.defaults, self.user, str(editor), []))
            first.start()
            deadline = time.monotonic() + 5
            while not self.target().exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            second.start()
            try:
                second.join(timeout=5)
                self.assertEqual(second.exitcode, 0)
                self.assertTrue(first.is_alive(), "--wait must not lock out other windows")
                first.join(timeout=5)
                self.assertEqual(first.exitcode, 0)
            finally:
                for child in (first, second):
                    if child.is_alive():
                        child.terminate()
                    child.join()
        self.assertEqual(log.read_text().splitlines(), ['{"profile": "default"}', "session change"])


if __name__ == "__main__":
    unittest.main()
