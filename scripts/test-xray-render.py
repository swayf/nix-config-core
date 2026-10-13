#!/usr/bin/env python3
"""Offline renderer tests with synthetic keys only; no SOPS decryption/network."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RENDERER = ROOT / "modules/nixos/xray-render.py"
R = load("render", ROOT / "modules/nixos/xray-render.py")
COMMAND = ([os.environ["XRAY_RENDER_COMMAND"]] if os.environ.get("XRAY_RENDER_COMMAND")
           else [sys.executable, str(RENDERER)])


def run_cli(*args):
    environment = os.environ.copy()
    if os.environ.get("XRAY_RENDER_COMMAND"):
        # The packaged CLI must supply its runtime tools, not borrow the test PATH.
        environment["PATH"] = ""
    return subprocess.run(COMMAND + list(args), env=environment, capture_output=True, text=True)


PUBLIC = {"serverName": "target.test", "port": 443, "statsPort": 10085,
          "tag": "fixture-vless", "clients": ["alice", "bob"]}
# RFC 7748 test vector, not a production keypair.
MATERIAL = {"private_key": "dwdtCnMYpX08FsFyUbJmRd9ML4frwJkqsXf7pR25LCo",
            "public_key": "hSDwCYkwp1R0i33ctD73Wg2_Og0mOBr066SpjqqbTmo",
            "short_id": "0123456789abcdef",
            "clients": {"alice": {"uuid": "00000000-0000-4000-8000-000000000001"},
                        "bob": {"uuid": "00000000-0000-4000-8000-000000000002"}}}


class RenderTests(unittest.TestCase):
    def test_stable_credentials_and_stats_only(self):
        original = copy.deepcopy(MATERIAL)
        output = R.render(PUBLIC, MATERIAL)
        self.assertEqual(MATERIAL, original)
        self.assertEqual(output, R.render(PUBLIC, MATERIAL))
        self.assertEqual(output["api"]["services"], ["StatsService"])
        self.assertEqual(output["inbounds"][0]["listen"], "127.0.0.1")
        inbound = output["inbounds"][1]
        self.assertEqual(inbound["streamSettings"]["realitySettings"]["privateKey"], MATERIAL["private_key"])
        self.assertEqual([c["email"] for c in inbound["settings"]["clients"]], PUBLIC["clients"])
        self.assertTrue(all(c["flow"] == "xtls-rprx-vision" for c in inbound["settings"]["clients"]))
        self.assertEqual(output["log"]["access"], "none")

    def test_remove_and_return_keeps_uuid(self):
        disabled = PUBLIC | {"clients": ["alice"]}
        self.assertEqual(len(R.render(disabled, MATERIAL)["inbounds"][1]["settings"]["clients"]), 1)
        restored = R.render(PUBLIC, MATERIAL)["inbounds"][1]["settings"]["clients"]
        self.assertEqual(restored[1]["id"], MATERIAL["clients"]["bob"]["uuid"])

    def test_public_routing_is_preserved_not_mutated(self):
        config = PUBLIC | {"routing": {"rules": [{"type": "field", "domain": ["example.test"], "outboundTag": "direct"}]}}
        original = copy.deepcopy(config)
        self.assertEqual(R.render(config, MATERIAL)["routing"]["rules"][1], config["routing"]["rules"][0])
        self.assertEqual(config, original)

    def test_sniffing_and_runtime_warp_are_preserved_not_mutated(self):
        warp = {"secretKey": "AQ" + "A" * 41 + "=", "address": ["172.16.0.2/32"],
                "noKernelTun": True, "reserved": [1, 2, 3],
                "peers": [{"endpoint": "warp.test:2408", "publicKey": "AQ" + "A" * 41 + "="}]}
        config = PUBLIC | {"sniffing": {"enabled": True, "destOverride": ["http", "tls"]},
                           "outbounds": [{"tag": "direct", "protocol": "freedom"},
                                         {"tag": "warp", "protocol": "wireguard"}]}
        original, original_warp = copy.deepcopy(config), copy.deepcopy(warp)
        output = R.render(config, MATERIAL, warp)
        self.assertEqual(output["inbounds"][1]["sniffing"], config["sniffing"])
        self.assertEqual(output["outbounds"][1]["settings"], warp)
        self.assertEqual(config, original)
        self.assertEqual(warp, original_warp)
        with self.assertRaises(ValueError):
            R.render(config, MATERIAL)
        with self.assertRaises(ValueError):
            R.render(PUBLIC, MATERIAL, warp)
        bad_public = copy.deepcopy(config)
        bad_public["outbounds"][1]["settings"] = warp
        with self.assertRaises(ValueError):
            R.render(bad_public, MATERIAL, warp)
        for change in [{"noKernelTun": False}, {"secretKey": "BAD"}, {"reserved": [1, 2]},
                       {"reserved": [1, 2, 256]}, {"address": []}, {"mtu": True}, {"peers": []}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                R.render(config, MATERIAL, warp | change)

    def test_invalid_secret_or_public_config_rejected(self):
        for changes in [{"private_key": "SECRET-invalid"}, {"public_key": "B" * 43},
                        {"short_id": "zzz"}, {"clients": {}},
                        {"clients": {n: {"uuid": "not-a-uuid"} for n in PUBLIC["clients"]}},
                        {"clients": {n: MATERIAL["clients"]["alice"] for n in PUBLIC["clients"]}}]:
            with self.subTest(changes=changes), self.assertRaises((ValueError, KeyError)):
                R.render(PUBLIC, MATERIAL | changes)
        for changes in [{"port": True}, {"clients": ["alice", "alice"]}, {"serverName": "bad/value"}]:
            with self.assertRaises(ValueError):
                R.render(PUBLIC | changes, MATERIAL)

    def test_cli_does_not_echo_secret_or_replace_previous_file_on_error(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "public").write_text(json.dumps(PUBLIC))
            (base / "material").write_text(json.dumps(MATERIAL | {"private_key": "NEVER_PRINT_THIS"}))
            (base / "output").write_text("old")
            result = run_cli("--settings", str(base / "public"), "--material", str(base / "material"),
                             "--output", str(base / "output"))
            self.assertEqual(result.returncode, 1)
            self.assertNotIn("NEVER_PRINT_THIS", result.stdout + result.stderr)
            self.assertEqual((base / "output").read_text(), "old")

    def test_cli_atomic_private_output_with_and_without_warp(self):
        warp = {"secretKey": "AQ" + "A" * 41 + "=", "address": ["192.0.2.2/32"],
                "noKernelTun": True,
                "peers": [{"endpoint": "warp.test:2408", "publicKey": "AQ" + "A" * 41 + "="}]}
        for with_warp in (False, True):
            with self.subTest(warp=with_warp), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                settings = PUBLIC | ({"outbounds": [{"tag": "warp", "protocol": "wireguard"}]}
                                     if with_warp else {})
                (base / "public").write_text(json.dumps(settings))
                (base / "material").write_text(json.dumps(MATERIAL))
                output = base / "output"
                output.write_text("previous")
                output.chmod(0o644)
                args = ["--settings", str(base / "public"), "--material", str(base / "material"),
                        "--output", str(output)]
                if with_warp:
                    (base / "warp").write_text(json.dumps(warp))
                    args += ["--warp", str(base / "warp")]
                expected_files = set(base.iterdir())
                for _ in range(2):
                    result = run_cli(*args)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout + result.stderr, "")
                    self.assertEqual(json.loads(output.read_text()),
                                     R.render(settings, MATERIAL, warp if with_warp else None))
                    self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
                    self.assertEqual(set(base.iterdir()), expected_files)

    def test_cli_missing_openssl_fails_without_replacing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "public").write_text(json.dumps(PUBLIC))
            (base / "material").write_text(json.dumps(MATERIAL))
            (base / "output").write_text("previous")
            result = subprocess.run([sys.executable, str(RENDERER),
                                     "--settings", str(base / "public"), "--material", str(base / "material"),
                                     "--output", str(base / "output")],
                                    env={"PATH": ""}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stderr, "Xray configuration/material rejected\n")
            self.assertEqual(result.stdout, "")
            self.assertEqual((base / "output").read_text(), "previous")


if __name__ == "__main__":
    unittest.main()
