#!/usr/bin/env python3
"""Gemeinsame Auswertung der Coverage-Rohdaten eines Hosts.

Wird von der GUI-Seite "Analyze monitoring coverage" UND vom Check-Plugin
"Checkmk Monitoring Coverage" benutzt, damit beide dieselben Regeln
(Setup-Regel "Monitoring coverage analysis", siehe rulesets/) gleich
anwenden. Die Analyse selbst rechnet ungefiltert und liefert je Host eine
Liste von Items; Status, Coverage und Texte entstehen erst hier.

Item (dict, JSON-serialisierbar):
  kind:     "monitored" | "open" | "candidate"
  token:    interner Name ("mssql", "generic:xyz")
  title:    Anzeigename
  plugins:  verfuegbare / aktive Check-Plugins
  evidence: Belege (Texte)
  state:    Kurztext, z.B. "running, not monitored"
  hint:     Handlungshinweis (optional)

Keine Abhaengigkeit von cmk.gui oder cmk.agent_based - nur stdlib.
"""
from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, NamedTuple

GENERIC_INFO = "info"
GENERIC_WARN = "warn"


class IgnoreRule(NamedTuple):
    subsystem: re.Pattern[str] | None
    plugin: re.Pattern[str] | None
    evidence: re.Pattern[str] | None
    comment: str


class Evaluation(NamedTuple):
    status: str
    coverage_pct: int
    fraction_text: str
    findings: str
    unmonitored_lines: list[str]
    candidate_lines: list[str]
    ignored_lines: list[str]
    monitored_lines: list[str]
    # Zaehler fuer die Gesamt-Coverage ueber alle Hosts (GUI-Seite)
    monitored_count: int = 0
    total_count: int = 0


def _compile(pattern: object) -> re.Pattern[str] | None:
    if not isinstance(pattern, str) or not pattern.strip():
        return None
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        # Ungueltige Regex: Regel-Feld wirkt nicht (die Setup-GUI prueft
        # die Syntax bereits beim Speichern).
        return None


def parse_ignore_rules(params: Mapping[str, Any] | None) -> list[IgnoreRule]:
    rules: list[IgnoreRule] = []
    for entry in (params or {}).get("ignore") or []:
        if not isinstance(entry, Mapping):
            continue
        rule = IgnoreRule(
            subsystem=_compile(entry.get("subsystem")),
            plugin=_compile(entry.get("plugin")),
            evidence=_compile(entry.get("evidence")),
            comment=str(entry.get("comment") or "").strip(),
        )
        # Eintrag ohne ein einziges (gueltiges) Muster wuerde alles
        # treffen - wird ignoriert.
        if rule.subsystem or rule.plugin or rule.evidence:
            rules.append(rule)
    return rules


def _any_match(pattern: re.Pattern[str], values: Iterable[str]) -> bool:
    return any(pattern.search(str(v)) for v in values)


def matching_ignore_rule(item: Mapping[str, Any], rules: Sequence[IgnoreRule]) -> IgnoreRule | None:
    """Erste Regel, deren gesetzte Muster ALLE auf das Item passen."""
    for rule in rules:
        if rule.subsystem and not _any_match(
            rule.subsystem, (item.get("title", ""), item.get("token", ""))
        ):
            continue
        if rule.plugin and not _any_match(rule.plugin, item.get("plugins") or []):
            continue
        if rule.evidence and not _any_match(rule.evidence, item.get("evidence") or []):
            continue
        return rule
    return None


def _short_plugins(plugins: Sequence[str], limit: int = 5) -> str:
    names = list(plugins)
    text = ", ".join(names[:limit])
    return text + (f", ... (+{len(names) - limit})" if len(names) > limit else "")


def _compact_plugins(plugins: Sequence[str], min_group: int = 3) -> str:
    """Ab min_group Namen mit gemeinsamem Praefix (bis zum letzten '_')
    als '<praefix>*' zusammenfassen, z.B. esx_vsphere_vm_cpu, ..._name ->
    'esx_vsphere_vm_*'. Sonst die volle Liste."""
    names = list(plugins)
    if len(names) >= min_group:
        prefix = os.path.commonprefix(names)
        prefix = prefix[: prefix.rfind("_") + 1]
        if len(prefix) > 1:
            return prefix + "*"
    return ", ".join(names)


def _item_line(item: Mapping[str, Any]) -> str:
    return "%s: %s – available plug-in(s): %s [detected via %s]" % (
        item.get("title", ""),
        item.get("state", ""),
        _short_plugins(item.get("plugins") or []),
        "; ".join(item.get("evidence") or []),
    )


def evaluate(items: Sequence[Mapping[str, Any]], params: Mapping[str, Any] | None) -> Evaluation:
    params = params or {}
    rules = parse_ignore_rules(params)
    generic_mode = params.get("generic_candidates", GENERIC_INFO)

    monitored = [i for i in items if i.get("kind") == "monitored"]
    open_items: list[Mapping[str, Any]] = []
    candidates: list[Mapping[str, Any]] = []
    ignored_lines: list[str] = []
    for item in items:
        kind = item.get("kind")
        if kind not in ("open", "candidate"):
            continue
        rule = matching_ignore_rule(item, rules)
        if rule is not None:
            line = _item_line(item)
            if rule.comment:
                line += f" – ignored by rule: {rule.comment}"
            else:
                line += " – ignored by rule"
            ignored_lines.append(line)
        elif kind == "open":
            open_items.append(item)
        else:
            candidates.append(item)

    # Generische Kandidaten zaehlen nur im WARN-Modus wie offene Findings.
    counting_open = open_items + (candidates if generic_mode == GENERIC_WARN else [])
    total = len(monitored) + len(counting_open)
    monitored_n = len(monitored)
    coverage_pct = 100 if total == 0 else round(100 * monitored_n / total)
    fraction_text = (
        "%d/%d monitorable subsystems monitored" % (monitored_n, total)
        if total
        else "no monitorable subsystems detected"
    )

    def by_title(seq: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        return sorted(seq, key=lambda i: str(i.get("title", "")).lower())

    unmonitored_lines = [_item_line(i) for i in by_title(open_items)]
    candidate_lines = [_item_line(i) for i in by_title(candidates)]
    monitored_lines = [
        "%s: monitored (via %s)" % (i.get("title", ""), _compact_plugins(i.get("plugins") or []))
        if i.get("state", "monitored") == "monitored"
        # z.B. "plug-in deployed, all services disabled by rule (12)"
        else "%s: %s" % (i.get("title", ""), i.get("state", ""))
        for i in by_title(monitored)
    ]

    findings_parts = []
    for item in by_title(counting_open):
        hint = item.get("hint") or ""
        findings_parts.append(
            "%s: %s%s" % (item.get("title", ""), item.get("state", ""), f" ({hint})" if hint else "")
        )
    if findings_parts:
        status = "WARN"
        findings = " | ".join(findings_parts)
    else:
        status = "OK"
        findings = "No open findings."
    return Evaluation(
        status=status,
        coverage_pct=coverage_pct,
        fraction_text=fraction_text,
        findings=findings,
        unmonitored_lines=unmonitored_lines,
        candidate_lines=candidate_lines,
        ignored_lines=ignored_lines,
        monitored_lines=monitored_lines,
        monitored_count=monitored_n,
        total_count=total,
    )


def detail_sections(
    evaluation: Evaluation, source_lines: Sequence[str], generic_mode: str = GENERIC_INFO
) -> list[tuple[str, list[str]]]:
    """Detail-Abschnitte in fester Reihenfolge; leere entfallen."""
    candidate_heading = (
        "Candidates (generic match):"
        if generic_mode == GENERIC_WARN
        else "Candidates (generic match, info only):"
    )
    return [
        (heading, list(lines))
        for heading, lines in (
            ("Unmonitored:", evaluation.unmonitored_lines),
            (candidate_heading, evaluation.candidate_lines),
            ("Ignored:", evaluation.ignored_lines),
            ("Already monitored:", evaluation.monitored_lines),
            ("Sources:", source_lines),
        )
        if lines
    ]


def detail_lines(
    evaluation: Evaluation, source_lines: Sequence[str], generic_mode: str = GENERIC_INFO
) -> list[str]:
    out: list[str] = []
    for heading, lines in detail_sections(evaluation, source_lines, generic_mode):
        if out:
            out.append("")
        out.append(heading)
        out.extend(lines)
    return out
