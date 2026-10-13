#!/usr/bin/env python3
"""Packaged exporter smoke: loopback HTTP with an unavailable StatsService only."""
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request

COMMAND = os.environ["XRAY_EXPORTER_COMMAND"]


class ExporterSmokeTests(unittest.TestCase):
    def test_packaged_version_with_empty_path(self):
        self.assertTrue(Path(COMMAND).is_absolute())
        result = subprocess.run(
            [COMMAND, "--version"], env={"PATH": ""},
            capture_output=True, text=True, timeout=5, check=True,
        )
        self.assertIn("Xray Exporter 1.0.0-", result.stdout + result.stderr)

    def test_scrape_with_unavailable_stats_service(self):
        # A bound but non-listening socket reserves an unreachable API endpoint.
        with socket.socket() as endpoint, tempfile.TemporaryDirectory() as home:
            endpoint.bind(("127.0.0.1", 0))
            api_port = endpoint.getsockname()[1]
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                http_port = listener.getsockname()[1]
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            base = f"http://127.0.0.1:{http_port}"
            with tempfile.TemporaryFile(mode="w+") as log:
                process = subprocess.Popen(
                    [COMMAND, f"--listen=127.0.0.1:{http_port}",
                     f"--xray-endpoint=127.0.0.1:{api_port}",
                     "--with-user-metrics", "--log-path=disabled", "--scrape-timeout=1"],
                    env={"PATH": "", "HOME": home}, cwd=home,
                    stdout=log, stderr=subprocess.STDOUT, text=True,
                )
                try:
                    deadline = time.monotonic() + 10
                    while True:
                        self.assertIsNone(process.poll(), "exporter exited before HTTP readiness")
                        try:
                            with opener.open(base + "/health", timeout=1) as response:
                                self.assertEqual(response.status, 200)
                                self.assertEqual(response.read(), b"OK")
                            break
                        except (urllib.error.URLError, TimeoutError):
                            if time.monotonic() >= deadline:
                                self.fail("exporter HTTP readiness timed out")
                            time.sleep(0.05)
                    for _ in range(2):
                        with opener.open(base + "/scrape", timeout=5) as response:
                            self.assertEqual(response.status, 200)
                            metrics = response.read().decode()
                        self.assertRegex(metrics, r"(?m)^xray_up 0$")
                        self.assertIsNone(process.poll(), "failed scrape stopped the exporter")
                finally:
                    if process.poll() is None:
                        process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                        self.fail("exporter did not stop after SIGTERM")
                    finally:
                        log.seek(0)
                        print(log.read(), flush=True)
                self.assertEqual(process.returncode, 0)


if __name__ == "__main__":
    unittest.main()
