# Copyright (C) 2026 Alexander Wilms, Christian Wirtz
# SPDX-License-Identifier: GPL-2.0-only
"""Consistency of the rules file (monitoring_coverage_analyzer_rules.json)."""

from __future__ import annotations

import json
import re
import unittest

from _harness import RULES_FILE, load_page_module

_CONDITION_KINDS = ("section", "systemd", "process", "winservice", "label", "package")
_DETECT_KEYS = {"runtime", "direct", "unless", "not_on_os", "monitored_elsewhere", "piggyback_tokens"}
_CONDITION_LISTS = ("runtime", "direct", "unless")


def _pattern(condition: str) -> str:
    kind, _sep, rest = condition.partition(":")
    if kind == "section":
        # section:<sec>[/<sub>]:data | section:<sec>[/<sub>]:contains:<regex>
        _ref, _sep, mode_rest = rest.partition(":")
        mode, _sep, pattern = mode_rest.partition(":")
        return pattern if mode == "contains" else ""
    if kind == "label":
        return rest.partition(":")[2]
    return rest


class RulesFileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with open(RULES_FILE, encoding="utf-8") as handle:
            cls.data = json.load(handle)

    def test_loads_without_error(self) -> None:
        rules = load_page_module()._load_rules()
        self.assertTrue(rules.titles)
        self.assertTrue(rules.detect)

    def test_no_schema_version_and_known_top_level_keys(self) -> None:
        keys = {k for k in self.data if not k.startswith("_")}
        self.assertNotIn("schema_version", keys)
        self.assertEqual(
            keys,
            {
                "aliases", "titles", "hints", "stop_tokens", "detect", "section_data",
                "no_data_lines", "section_ignore", "generic_ignore_families", "empty_ok",
                "agent_builtin_families",
            },
        )

    def test_detect_and_hints_have_a_title(self) -> None:
        titles = self.data["titles"]
        self.assertEqual([t for t in self.data["detect"] if t not in titles], [])
        self.assertEqual([t for t in self.data["hints"] if t not in titles], [])

    def test_alias_targets_are_titled_or_stop_tokens(self) -> None:
        known = set(self.data["titles"]) | set(self.data["stop_tokens"])
        self.assertEqual(sorted({v for v in self.data["aliases"].values() if v not in known}), [])

    def test_aliases_are_lowercase(self) -> None:
        self.assertEqual([k for k in self.data["aliases"] if k != k.lower()], [])

    def test_detect_entries_are_well_formed(self) -> None:
        for token, spec in self.data["detect"].items():
            with self.subTest(token=token):
                keys = {k for k in spec if not k.startswith("_")}
                self.assertLessEqual(keys, _DETECT_KEYS)
                self.assertTrue(keys & {"runtime", "direct"}, "needs runtime or direct evidence")
                for key in _CONDITION_LISTS:
                    for condition in spec.get(key, []):
                        self.assertIn(condition.partition(":")[0], _CONDITION_KINDS, condition)
                        re.compile(_pattern(condition))
                for pattern in spec.get("monitored_elsewhere", []):
                    re.compile(pattern)
                for os_name in spec.get("not_on_os", []):
                    self.assertEqual(os_name, os_name.lower())

    def test_other_regexes_compile(self) -> None:
        for pattern in self.data["section_data"].values():
            re.compile(pattern)
        for pattern in self.data["no_data_lines"]:
            re.compile(pattern)


if __name__ == "__main__":
    unittest.main()
