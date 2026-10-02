"""Evaluation of host items with the Setup rule (lib/evaluate.py)."""

from __future__ import annotations

import unittest

from _harness import load_evaluate_module

ev = load_evaluate_module()

OPEN = {"kind": "open", "token": "mysql", "title": "MySQL", "plugins": ["mysql_capacity"],
        "evidence": ["process 'mysqld'"], "state": "running, not monitored", "hint": "deploy"}
MONITORED = {"kind": "monitored", "token": "apache", "title": "Apache", "plugins": ["apache_status"],
             "evidence": [], "state": "monitored"}
CANDIDATE = {"kind": "candidate", "token": "generic:acme", "title": "ACME", "plugins": ["acme_health"],
             "evidence": ["process 'acmed'"], "state": "possible match (fuzzy search)", "hint": ""}


class EvaluateTest(unittest.TestCase):
    def test_open_finding_is_warn(self) -> None:
        result = ev.evaluate([OPEN, MONITORED], None)
        self.assertEqual(result.status, "WARN")
        self.assertEqual((result.monitored_count, result.total_count, result.coverage_pct), (1, 2, 50))

    def test_only_monitored_is_ok(self) -> None:
        result = ev.evaluate([MONITORED], None)
        self.assertEqual((result.status, result.coverage_pct), ("OK", 100))

    def test_nothing_detected_is_ok(self) -> None:
        result = ev.evaluate([], None)
        self.assertEqual((result.status, result.coverage_pct), ("OK", 100))
        self.assertEqual(result.fraction_text, "no monitorable subsystems detected")

    def test_ignore_rule_by_subsystem(self) -> None:
        params = {"ignore": [{"subsystem": "^MySQL$", "comment": "test DB"}]}
        result = ev.evaluate([OPEN], params)
        self.assertEqual(result.status, "OK")
        self.assertEqual(len(result.ignored_lines), 1)
        self.assertIn("test DB", result.ignored_lines[0])

    def test_ignore_rule_needs_all_patterns(self) -> None:
        params = {"ignore": [{"subsystem": "MySQL", "evidence": "postgres"}]}
        self.assertEqual(ev.evaluate([OPEN], params).status, "WARN")

    def test_ignore_rule_without_pattern_is_dropped(self) -> None:
        self.assertEqual(ev.parse_ignore_rules({"ignore": [{"comment": "matches nothing"}]}), [])

    def test_fuzzy_modes(self) -> None:
        self.assertEqual(ev.evaluate([CANDIDATE], None).status, "WARN")
        info = ev.evaluate([CANDIDATE], {"fuzzy_search": ev.GENERIC_INFO})
        self.assertEqual((info.status, len(info.candidate_lines)), ("OK", 1))
        off = ev.evaluate([CANDIDATE], {"fuzzy_search": ev.GENERIC_OFF})
        self.assertEqual((off.status, off.candidate_lines, off.ignored_lines), ("OK", [], []))


if __name__ == "__main__":
    unittest.main()
