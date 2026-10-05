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


MONITORED_GENERIC = {"kind": "monitored_generic", "token": "generic:acmecloud", "title": "ACME Cloud",
                     "plugins": ["acmecloud_info"], "evidence": [], "state": "monitored"}


class EvaluateTest(unittest.TestCase):
    def test_monitored_generic_is_listed_and_counted(self) -> None:
        result = ev.evaluate([OPEN, MONITORED, MONITORED_GENERIC], None)
        self.assertEqual((result.monitored_count, result.total_count), (2, 3))
        self.assertIn("ACME Cloud: monitored (via acmecloud_info)", result.monitored_lines)
        self.assertEqual(ev.evaluate([MONITORED_GENERIC], None).fraction_text,
                         "1/1 monitorable subsystems monitored")

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


SOURCES = [
    "Evidence per subsystem: check_command:5, agent_section:4",
    "Installed packages without runtime evidence (info only): MySQL / MariaDB (mysql-common); "
    "PostgreSQL (postgresql-client-16, postgresql-common)",
    "Inventory packages: 608 (HW/SW inventory, info only)",
    "Agent sections: 36 via get-agent-output @cached",
]
NO_AGENT = ["Agent sections: unavailable via get-agent-output @cached (no cached agent output)"]
NO_AGENT_REMOTE = ["Agent sections: unavailable via remote get-agent-output @cached (site remote1) "
                   "(Failed to fetch data from host1: Error('[Errno 113] No route to host'))"]


class DetailSectionsTest(unittest.TestCase):
    def test_no_sources_section(self) -> None:
        headings = [h for h, _l in ev.detail_sections(ev.evaluate([OPEN, MONITORED], None), SOURCES)]
        self.assertEqual(headings, ["Unmonitored:", "Ignored:", "Already monitored:"])
        text = "\n".join(ev.detail_lines(ev.evaluate([MONITORED], None), SOURCES))
        for hidden in ("Evidence per subsystem", "Inventory packages", "Agent sections: 36", "Sources:"):
            self.assertNotIn(hidden, text)

    def test_packages_without_runtime_are_ignored_lines(self) -> None:
        sections = dict(ev.detail_sections(ev.evaluate([MONITORED], None), SOURCES))
        self.assertEqual(sections["Ignored:"], [
            "MySQL / MariaDB: package installed (mysql-common), not running – not counted",
            "PostgreSQL: package installed (postgresql-client-16, postgresql-common), not running – not counted",
        ])

    def test_missing_agent_data_stays_visible(self) -> None:
        sections = ev.detail_sections(ev.evaluate([], None), NO_AGENT)
        self.assertEqual(sections[0][0], "Note:")
        self.assertEqual(sections[0][1], ["No agent data available, the result of this host is incomplete. "
                                          "(no cached agent output)"])
        remote = ev.detail_sections(ev.evaluate([], None), NO_AGENT_REMOTE)
        self.assertEqual(remote[0][1], ["No agent data available, the result of this host is incomplete. "
                                        "(Failed to fetch data from host1: Error('[Errno 113] No route to host'))"])

    def test_missing_agent_data_in_findings(self) -> None:
        self.assertEqual(ev.findings_text(ev.evaluate([MONITORED], None), NO_AGENT),
                         "No open findings, but currently no agent data available. See details.")
        warn = ev.evaluate([OPEN], None)
        self.assertEqual(ev.findings_text(warn, NO_AGENT), warn.findings + " | No agent data available. See details.")
        self.assertEqual(ev.findings_text(warn, SOURCES), warn.findings)


if __name__ == "__main__":
    unittest.main()
