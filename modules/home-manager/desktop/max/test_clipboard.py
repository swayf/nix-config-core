"""Protocol and format tests; run with python3 -m unittest discover -s this-dir."""

import base64
import io
import queue
import types
import unittest
from unittest.mock import Mock, patch

import clipboard


class ClipboardTests(unittest.TestCase):
    def test_slow_container_startup_keeps_waiting_past_sixty_seconds(self):
        run = Mock()
        run.poll.return_value = None
        stopped = Mock()
        stopped.is_set.return_value = False
        results = [types.SimpleNamespace(returncode=0, stdout=b"false\n")] * 40
        results.append(types.SimpleNamespace(returncode=0, stdout=b"true\n"))
        with patch.object(clipboard.subprocess, "run", side_effect=results) as probe, \
             patch.object(clipboard.time, "monotonic", side_effect=range(0, 200, 3)):
            self.assertTrue(clipboard.wait_for_container("podman", run, stopped))
        self.assertEqual(stopped.wait.call_count, 40)
        self.assertEqual(probe.call_args.args[0][1], "inspect")

    def test_startup_wait_stops_when_container_exits_or_user_stops(self):
        for status, cancelled in ((1, False), (None, True)):
            run = Mock()
            run.poll.return_value = status
            stopped = Mock()
            stopped.is_set.return_value = cancelled
            with patch.object(clipboard.subprocess, "run") as probe:
                self.assertFalse(clipboard.wait_for_container("podman", run, stopped))
                probe.assert_not_called()

    def test_utf8_roundtrip_preserves_newlines(self):
        data = "Привет\n\n".encode()
        frame = {"target": "UTF8_STRING", "data": base64.b64encode(data).decode(), "epoch": 1}
        self.assertEqual(clipboard.decode(frame), data)

    def test_binary_png_roundtrip(self):
        data = b"\x89PNG\r\n\x1a\n\x00\xff"
        frame = {"target": "image/png", "data": base64.b64encode(data).decode(), "epoch": 1}
        self.assertEqual(clipboard.decode(frame), data)

    def test_rejects_other_formats_and_invalid_encoding(self):
        for frame in (
            [],
            {"target": "UTF8_STRING"},
            {"target": "UTF8_STRING", "data": None, "epoch": 1},
            {"target": "text/uri-list", "data": "", "epoch": 1},
            {"target": "UTF8_STRING", "data": "!", "epoch": 1},
            {"target": "UTF8_STRING", "data": "/w==", "epoch": 1},
            {"target": "UTF8_STRING", "data": "", "epoch": True},
            {"target": "UTF8_STRING", "data": "", "epoch": -1},
        ):
            with self.subTest(frame=frame), self.assertRaises(ValueError):
                clipboard.decode(frame)

    def test_size_limit(self):
        with patch.object(clipboard, "MAX_BYTES", 3), self.assertRaises(ValueError):
            clipboard.decode({"target": "UTF8_STRING", "data": "YWJjZA==", "epoch": 1})

    def test_snapshot_prefers_image_over_text(self):
        data = {"TARGETS": b"UTF8_STRING\nimage/png\n", "image/png": b"PNG"}
        with patch.object(clipboard, "capture", side_effect=lambda target, *args: data[target]):
            self.assertEqual(clipboard.snapshot(), ("image/png", b"PNG"))

    def test_snapshot_normalizes_text(self):
        data = {"TARGETS": b"text/plain;charset=utf-8\n", "text/plain;charset=utf-8": b"text\n"}
        with patch.object(clipboard, "capture", side_effect=lambda target, *args: data[target]):
            self.assertEqual(clipboard.snapshot(), ("UTF8_STRING", b"text\n"))

    def test_snapshot_checks_focus_before_each_selection_read(self):
        with patch.object(clipboard, "capture") as capture:
            self.assertIsNone(clipboard.snapshot(lambda: False))
            capture.assert_not_called()
        with patch.object(clipboard, "capture", return_value=b"UTF8_STRING\n") as capture:
            permitted = Mock(side_effect=[True, False])
            self.assertIsNone(clipboard.snapshot(permitted))
            capture.assert_called_once_with("TARGETS", 65536)

    def test_supervisor_gates_both_directions_and_rotates_epochs(self):
        run = Mock(returncode=0)
        run.done = False
        run.poll.side_effect = lambda: 0 if run.done else None
        guest, host = Mock(), Mock()
        frame = lambda epoch: {"target": "UTF8_STRING", "data": "", "epoch": epoch}
        frames = [("guest", {"ready": True}), ("host", {"ready": True}),
                  ("host", frame(0)), ("host", frame(2)), ("guest", frame(2)),
                  ("host", frame(2)), ("host", frame(4))]
        events = Mock()

        def get(**kwargs):
            event = frames.pop(0)
            if not frames:
                run.done = True
            return event

        events.get.side_effect = get
        with patch.object(clipboard.subprocess, "Popen", side_effect=[run, guest, host]), \
             patch.object(clipboard, "wait_for_container", return_value=True), \
             patch.object(clipboard, "weston_host_pid", return_value=123), \
             patch.object(clipboard, "focus_state", side_effect=[
                 (False, None), (True, 7), (True, 7), (False, 8),
                 (True, 9), (True, 9), (True, 9)]), \
             patch.object(clipboard.queue, "Queue", return_value=events), \
             patch.object(clipboard.threading, "Thread"), \
             patch.object(clipboard.signal, "signal"), \
             patch.object(clipboard.os, "set_blocking"), \
             patch.object(clipboard, "send") as send:
            self.assertEqual(clipboard.supervise("podman", ["run"]), 0)
        data = [(call.args[0], call.args[1]) for call in send.call_args_list
                if "target" in call.args[1]]
        self.assertEqual(data, [(guest, frame(2)), (guest, frame(4))])
        controls = [call.args[1] for call in send.call_args_list if call.args[0] is guest
                    and "active" in call.args[1]]
        self.assertEqual(controls, [
            {"active": False, "epoch": 1, "focus_serial": None},
            {"active": True, "epoch": 2, "focus_serial": 7},
            {"active": False, "epoch": 3, "focus_serial": None},
            {"active": True, "epoch": 4, "focus_serial": 9},
        ])

    def test_unsupported_selection_is_not_read_as_a_file(self):
        with patch.object(clipboard, "capture", return_value=b"text/uri-list\n"):
            self.assertIsNone(clipboard.snapshot())

    def test_host_focus_query_is_fail_closed(self):
        for output, code, expected in (
            ("(true, uint32 7)\n", 0, (True, 7)),
            ("(false, uint32 8)\n", 0, (False, 8)),
            ("(true, 9)\n", 0, (True, 9)),
            ("(true, uint32 7)\n", 1, (False, None)),
            ("true", 0, (False, None)),
        ):
            with patch.object(clipboard.subprocess, "run", return_value=types.SimpleNamespace(
                    returncode=code, stdout=output)):
                self.assertEqual(clipboard.focus_state(123), expected)
        for error in (OSError(), clipboard.subprocess.TimeoutExpired("gdbus", 1)):
            with patch.object(clipboard.subprocess, "run", side_effect=error):
                self.assertEqual(clipboard.focus_state(123), (False, None))
        with patch.object(clipboard.subprocess, "run") as probe:
            self.assertEqual(clipboard.focus_state(None), (False, None))
            probe.assert_not_called()

    def test_pid_is_discovered_on_host_not_from_window_title(self):
        with patch.object(clipboard.subprocess, "run", return_value=types.SimpleNamespace(
                returncode=0, stdout="HPID COMMAND\n123 weston\n124 Xwayland\n")) as probe:
            self.assertEqual(clipboard.weston_host_pid("podman"), 123)
            self.assertEqual(probe.call_args.args[0], ["podman", "top", "hm-max", "hpid", "comm"])
        for output in ("HPID COMMAND\n", "1 weston\n2 weston\n", "1 max\n"):
            with patch.object(clipboard.subprocess, "run", return_value=types.SimpleNamespace(
                    returncode=0, stdout=output)):
                self.assertIsNone(clipboard.weston_host_pid("podman"))

    def test_reject_transfers_when_unfocused_or_from_old_focus_session(self):
        frame = {"target": "UTF8_STRING", "data": "", "epoch": 2}
        with patch.object(clipboard, "focus_state", return_value=(True, 10)) as query:
            self.assertTrue(clipboard.can_forward(frame, 2, 10, 123))
            self.assertFalse(clipboard.can_forward(frame, 3, 10, 123))
            self.assertFalse(clipboard.can_forward(frame, 2, None, 123))
            self.assertEqual(query.call_count, 1)
        for state in ((False, 10), (True, 11), (False, None)):
            with patch.object(clipboard, "focus_state", return_value=state):
                self.assertFalse(clipboard.can_forward(frame, 2, 10, 123))

    def test_control_schema(self):
        self.assertEqual(clipboard.control_state({"active": True, "epoch": 1, "focus_serial": 7}),
                         (True, 1, 7))
        self.assertEqual(clipboard.control_state({"active": False, "epoch": 2, "focus_serial": None}),
                         (False, 2, None))
        for frame in ({"active": True}, {"active": True, "epoch": 1, "focus_serial": None},
                      {"active": False, "epoch": 1, "focus_serial": 7},
                      {"active": True, "epoch": True, "focus_serial": 7}):
            with self.assertRaises(ValueError):
                clipboard.control_state(frame)

    def test_protocol_eof_and_validation(self):
        events = queue.Queue()
        process = types.SimpleNamespace(stdout=io.BytesIO(b'{"ready":true}\n'))
        clipboard.read_frames(process, "host", events)
        self.assertEqual(events.get_nowait(), ("host", {"ready": True}))
        self.assertEqual(events.get_nowait(), ("host", None))
        process.stdout = io.BytesIO(b'{"target":"text/uri-list","data":""}\n')
        clipboard.read_frames(process, "host", events)
        self.assertEqual(events.get_nowait(), ("host", None))


if __name__ == "__main__":
    unittest.main()
