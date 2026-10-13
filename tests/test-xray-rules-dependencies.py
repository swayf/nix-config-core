#!/usr/bin/env python3
"""Narrow dependency guards for the pure monitoring rules and promtool fixtures."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = "modules/nixos/xray-monitoring-rules.nix"
RUNNER = "scripts/test-xray-rules.nix"
FIXTURE = "scripts/test-xray-rules.json"
LOCAL_PATH = r"(?<![\w$])(?:\.\.?/)[A-Za-z0-9_./-]+"


class RulesDependencyTests(unittest.TestCase):
    def test_generator_takes_only_node_and_has_no_imports_or_resources(self):
        text = (ROOT / GENERATOR).read_text()
        self.assertTrue(text.startswith("{ node }:\n"))
        self.assertNotRegex(text, r"\b(?:import|imports|builtins|pkgs|lib|config|inputs|userConfig|shared)\b")
        self.assertEqual(re.findall(LOCAL_PATH, text), [])
        self.assertNotRegex(text, r"https?://|/nix/store/|/(?:home|Users)/")

    def test_runner_has_only_the_two_reviewed_resource_edges(self):
        path = ROOT / RUNNER
        text = path.read_text()
        edges = set(re.findall(LOCAL_PATH, text))
        self.assertEqual(edges, {"../modules/nixos/xray-monitoring-rules.nix", "./test-xray-rules.json"})
        for edge in edges:
            target = (path.parent / edge).resolve()
            self.assertTrue(target.is_relative_to(ROOT))
            self.assertTrue(target.is_file())
        self.assertNotRegex(text, r"\b(?:fetchurl|fetchFromGitHub|fetchGit|inputs|userConfig|shared)\b")
        self.assertEqual(re.findall(r"\bimport\s+(\S+)", text),
                         ["../modules/nixos/xray-monitoring-rules.nix"])
        self.assertEqual(re.findall(r"\bbuiltins\.readFile\s+(\S+)", text),
                         ["./test-xray-rules.json;"])

    def test_runner_uses_only_pinned_promtool_and_generated_json(self):
        text = (ROOT / RUNNER).read_text()
        self.assertIn("nativeBuildInputs = [ pkgs.prometheus.cli ];", text)
        self.assertIn("format = pkgs.formats.json { };", text)
        self.assertEqual(re.findall(r"^\s*promtool (.+)$", text, re.MULTILINE),
                         ["check rules ${case.rules}", "test rules ${case.tests}"])
        self.assertNotRegex(text, r"\b(?:curl|wget|ssh|nixos-rebuild|systemctl)\b")

    def test_fixture_is_inline_promtool_data_without_external_files(self):
        fixture = json.loads((ROOT / FIXTURE).read_text())
        self.assertEqual(set(fixture), {"evaluation_interval", "tests"})
        self.assertEqual(len(fixture["tests"]), 7)
        for case in fixture["tests"]:
            self.assertLessEqual(set(case), {"name", "interval", "input_series", "alert_rule_test"})
            for series in case.get("input_series", []):
                self.assertEqual(set(series), {"series", "values"})
            for test in case["alert_rule_test"]:
                self.assertEqual(set(test), {"eval_time", "alertname", "exp_alerts"})
                for alert in test["exp_alerts"]:
                    self.assertEqual(set(alert), {"exp_labels", "exp_annotations"})
        self.assertNotRegex(json.dumps(fixture), r"https?://|/nix/store/|/(?:home|Users)/")


if __name__ == "__main__":
    unittest.main()
