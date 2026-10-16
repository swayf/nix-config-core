#!/usr/bin/env python3
"""Test the Handy shim without injecting keys or touching the host clipboard."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SHIM = Path(__file__).with_name("dotool.sh")


class DotoolTests(unittest.TestCase):
    def run_shim(self, text, focus="(true,)", opts="", query="1", failure=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mocks = {
                "gdbus": 'printf "%s\\n" "$*" >> "$CALLS"; printf "%s\\n" "$FOCUS"; exit "$FAILURE"',
                "dotool": 'printf "%s\\n" "$*" > "$ARGS"; cat',
                "sleep": 'printf "%s\\n" "$*" > "$DELAY"',
            }
            for name, body in mocks.items():
                tool = root / name
                tool.write_text("#!/bin/sh\n" + body + "\n")
                tool.chmod(0o755)
            env = dict(os.environ, PATH=f"{root}:{os.environ['PATH']}", FOCUS=focus,
                       FAILURE=str(int(failure)), CALLS=str(root / "calls"),
                       ARGS=str(root / "args"), DELAY=str(root / "delay"),
                       opts=opts, query_kitty=query, real_dotool=str(root / "dotool"))
            result = subprocess.run(["bash", "-euo", "pipefail", str(SHIM), "--test-arg"],
                                    input=text, text=True, capture_output=True, env=env, check=True)
            read = lambda name: (root / name).read_text() if (root / name).exists() else ""
            self.assertEqual(read("args"), "--test-arg\n")
            return result.stdout, read("calls"), read("delay")

    def test_kitty_text_paste(self):
        output, calls, delay = self.run_shim("key ctrl+v\n")
        self.assertEqual(output, "key ctrl+shift+v\n")
        self.assertIn("org.home_manager.HandyFocus.IsKitty --timeout 1", calls)
        self.assertEqual(delay, "1\n")

    def test_other_apps_and_unavailable_extension_keep_ctrl_v(self):
        for focus, failure in [("(false,)", False), ("", True), ("(true,)", True), ("invalid", False)]:
            with self.subTest(focus=focus, failure=failure):
                output, _, delay = self.run_shim("key ctrl+v\n", focus=focus, failure=failure)
                # gdbus failures normally produce no stdout, but must not trust
                # even a true result from an unsuccessful query.
                self.assertEqual(output, "key ctrl+v\n")
                self.assertEqual(delay, "1\n")

    def test_remap_applies_after_shortcut_selection(self):
        for focus, expected in [("(true,)", "key alt+shift+v\n"), ("(false,)", "key alt+v\n")]:
            output, _, _ = self.run_shim("key ctrl+v\n", focus=focus, opts="['altwin:ctrl_alt_win']")
            self.assertEqual(output, expected)

    def test_direct_input_and_multiline_commands_are_not_rewritten(self):
        for text in ["type Привет ctrl+v\n", "type key ctrl+v\n", "key ctrl+v\ntype ctrl alt super\n"]:
            output, calls, delay = self.run_shim(text)
            self.assertEqual(output, text)
            self.assertEqual(calls, "")
            self.assertEqual(delay, "")

    def test_explicit_chords_keep_existing_remap_and_delay(self):
        for text, expected in [("key ctrl+shift+v\n", "key alt+shift+v\n"),
                               ("key shift+insert\n", "key shift+insert\n")]:
            output, calls, delay = self.run_shim(text, opts="altwin:ctrl_alt_win")
            self.assertEqual(output, expected)
            self.assertEqual(calls, "")
            self.assertEqual(delay, "1\n")

    def test_non_gnome_does_not_query_extension(self):
        output, calls, _ = self.run_shim("key ctrl+v\n", query="0")
        self.assertEqual(output, "key ctrl+v\n")
        self.assertEqual(calls, "")


if __name__ == "__main__":
    unittest.main()
