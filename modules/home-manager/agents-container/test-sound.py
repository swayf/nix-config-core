#!/usr/bin/env python3
"""Playback-only protocol with synthetic host files; no bundled audio/policy."""
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location("sound", Path(__file__).with_name("sound.py"))
sound = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sound)


class SoundTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.audio = self.base / "my ready sound.wav"
        self.audio.write_bytes(b"test")
        self.config = self.base / "sounds.json"
        self.config.write_text(json.dumps({"ready": str(self.audio)}))
        self.server = sound.SoundServer(str(self.base / "socket"), self.config, "/host/bin/paplay")
        self.addCleanup(self.server.server_close)
        self.player = Mock()
        self.player.poll.return_value = None
        self.player.wait.return_value = 0
        self.popen = patch.object(sound.subprocess, "Popen", return_value=self.player).start()
        self.addCleanup(patch.stopall)

    def test_done_and_request_select_distinct_host_files(self):
        other = self.base / "request.wav"
        other.write_bytes(b"synthetic request")
        paths = {"done": str(self.audio), "request": str(other)}
        self.config.write_text(json.dumps(paths))
        for event, path in paths.items():
            self.player.poll.return_value = 0
            self.server.started = time.monotonic() - 1
            self.assertEqual(self.server.play({"event": event}), {"ok": True})
            self.assertEqual(self.popen.call_args.args[0][-1], path)
        self.assertEqual(self.popen.call_count, 2)

    def test_only_host_mapping_selects_file_and_player_options(self):
        self.assertEqual(self.server.play({"event": "ready"}), {"ok": True})
        self.popen.assert_called_once_with(
            ["/host/bin/paplay", "--client-name=Pi sounds", "--", str(self.audio)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def test_rejects_commands_options_paths_and_unknown_names(self):
        for request in [None, [], "ready", {}, {"event": []}, {"event": "--help"},
                        {"event": "/etc/passwd"}, {"event": "ready\n"}, {"event": "a" * 33},
                        {"event": "ready", "path": str(self.audio)},
                        {"event": "ready", "command": "sh"}, {"event": "unknown"}]:
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.server.play(request)
        self.popen.assert_not_called()

    def test_config_reload_without_restart(self):
        self.server.play({"event": "ready"})
        self.player.poll.return_value = 0
        self.server.started = time.monotonic() - 1
        other = self.base / "error.ogg"
        other.write_bytes(b"test")
        self.config.write_text(json.dumps({"error": str(other)}))
        with self.assertRaisesRegex(ValueError, "not configured"):
            self.server.play({"event": "ready"})
        self.assertEqual(self.server.play({"event": "error"}), {"ok": True})
        self.assertEqual(self.popen.call_args.args[0][-1], str(other))

    def test_missing_disabled_relative_or_invalid_config_fails_closed(self):
        for config in ['{"ready": null}', '{"ready": "relative.wav"}', '{}', '[]', '{',
                       json.dumps({"ready": str(self.base / "absent.wav")})]:
            self.config.write_text(config)
            with self.subTest(config=config), self.assertRaises(ValueError):
                self.server.play({"event": "ready"})
        self.config.unlink()
        with self.assertRaises(ValueError):
            self.server.play({"event": "ready"})
        self.popen.assert_not_called()

    def test_overlapping_and_rate_spammed_sounds_do_not_spawn_more_players(self):
        self.server.play({"event": "ready"})
        self.assertEqual(self.server.play({"event": "ready"}), {"ok": True, "skipped": True})
        self.player.poll.return_value = 0
        self.assertEqual(self.server.play({"event": "ready"}), {"ok": True, "skipped": True})
        self.popen.assert_called_once()

    def test_hung_player_is_killed_and_reaped(self):
        self.server.play({"event": "ready"})
        self.server.started = time.monotonic() - sound.MAX_PLAY_SECONDS - 1
        self.server.service_actions()
        self.player.kill.assert_called_once()
        self.player.wait.assert_called_once()
        self.assertIsNone(self.server.playback)

    def test_shutdown_kills_and_reaps_playback(self):
        self.server.play({"event": "ready"})
        self.server.server_close()
        self.player.kill.assert_called_once()
        self.player.wait.assert_called_once()

    def test_real_socket_protocol_bounds_and_error_recovery(self):
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        try:
            def request(data):
                with socket.socket(socket.AF_UNIX) as client:
                    client.settimeout(2)
                    client.connect(str(self.base / "socket"))
                    client.sendall(data)
                    return json.loads(client.makefile("rb").readline())
            for bad in [b"garbage\n", b"[]\n", b'x' * 257 + b'\n',
                        b'{"event":"ready","args":["--record"]}\n']:
                self.assertFalse(request(bad)["ok"])
            self.assertTrue(request(b'{"event":"ready"}\n')["ok"])
            self.popen.assert_called_once()
        finally:
            self.server.shutdown()
            thread.join()


if __name__ == "__main__":
    unittest.main()
