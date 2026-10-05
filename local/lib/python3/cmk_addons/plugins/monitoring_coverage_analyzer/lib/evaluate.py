#!/usr/bin/env python3
"""Shared evaluation of a host's raw coverage data.

Used by the GUI page "Analyze monitoring coverage" AND by the check plugin
"Checkmk Monitoring Coverage", so that both apply the same rules
(Setup rule "Monitoring coverage analysis", see rulesets/) in the same
way. The analysis itself computes unfiltered and returns a list of items
per host; status, coverage and texts are only produced here.

Item (dict, JSON-serializable):
  kind:     "monitored" | "open" | "candidate" | "monitored_generic"
            (monitored check the rules file does not know, e.g. from an
            MKP; counted like "monitored")
  token:    internal name ("mssql", "generic:xyz")
  title:    display name
  plugins:  available / active check plugins
  evidence: evidence (texts)
  state:    short text, e.g. "running, not monitored"
  hint:     action hint (optional)
  sources:  data sources (optional, generic matches)

No dependency on cmk.gui or cmk.agent_based - stdlib only.
"""
from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, NamedTuple

GENERIC_INFO = "info"
GENERIC_WARN = "warn"
GENERIC_OFF = "off"


def generic_mode(params: Mapping[str, Any] | None) -> str:
    """Mode for generic (fuzzy) candidates from the Setup rule.

    Default (no rule/no value): WARN, i.e. like other findings. The
    rule can disable them ("off") or show them as info only ("info").
    """
    mode = (params or {}).get("fuzzy_search", GENERIC_WARN)
    return mode if mode in (GENERIC_OFF, GENERIC_INFO, GENERIC_WARN) else GENERIC_WARN


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
    # Counters for the overall coverage across all hosts (GUI page)
    monitored_count: int = 0
    total_count: int = 0


def _compile(pattern: object) -> re.Pattern[str] | None:
    if not isinstance(pattern, str) or not pattern.strip():
        return None
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        # Invalid regex: the rule field has no effect (the Setup GUI already
        # checks the syntax when saving).
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
        # An entry without a single (valid) pattern would match
        # everything - it is ignored.
        if rule.subsystem or rule.plugin or rule.evidence:
            rules.append(rule)
    return rules


def _any_match(pattern: re.Pattern[str], values: Iterable[str]) -> bool:
    return any(pattern.search(str(v)) for v in values)


def matching_ignore_rule(item: Mapping[str, Any], rules: Sequence[IgnoreRule]) -> IgnoreRule | None:
    """First rule whose set patterns ALL match the item."""
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
    """From min_group names on, collapse names with a common prefix (up to
    the last '_') into '<prefix>*', e.g. esx_vsphere_vm_cpu, ..._name ->
    'esx_vsphere_vm_*'. Otherwise the full list."""
    names = list(plugins)
    if len(names) >= min_group:
        prefix = os.path.commonprefix(names)
        prefix = prefix[: prefix.rfind("_") + 1]
        if len(prefix) > 1:
            return prefix + "*"
    return ", ".join(names)


def _item_line(item: Mapping[str, Any]) -> str:
    # sources (optional, generic matches): matching agent plug-ins /
    # special agents as data source of the check plug-ins.
    sources = item.get("sources") or ""
    return "%s: %s – available plug-in(s): %s%s [detected via %s]" % (
        item.get("title", ""),
        item.get("state", ""),
        _short_plugins(item.get("plugins") or []),
        f" – data source: {sources}" if sources else "",
        "; ".join(item.get("evidence") or []),
    )


def evaluate(items: Sequence[Mapping[str, Any]], params: Mapping[str, Any] | None) -> Evaluation:
    params = params or {}
    rules = parse_ignore_rules(params)
    mode = generic_mode(params)

    monitored = [i for i in items if i.get("kind") in ("monitored", "monitored_generic")]
    open_items: list[Mapping[str, Any]] = []
    candidates: list[Mapping[str, Any]] = []
    ignored_lines: list[str] = []
    for item in items:
        kind = item.get("kind")
        if kind not in ("open", "candidate"):
            continue
        # Fuzzy search disabled by rule: omit candidates completely
        # (not even under "Ignored").
        if kind == "candidate" and mode == GENERIC_OFF:
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

    # Generic candidates only count like open findings in WARN mode.
    counting_open = open_items + (candidates if mode == GENERIC_WARN else [])
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
        # e.g. "plug-in deployed, all services disabled by rule (12)"
        else "%s: %s" % (i.get("title", ""), i.get("state", ""))
        for i in by_title(monitored)
    ]

    findings_parts = []
    for item in by_title(counting_open):
        hint = item.get("hint") or ""
        # Generic matches: "<state>. <hint>" (there the hint is a short
        # reference to the details, not an instruction).
        if hint and item.get("kind") == "candidate":
            findings_parts.append("%s: %s. %s" % (item.get("title", ""), item.get("state", ""), hint))
            continue
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


# Source lines of the analysis (kept in the cache and in support data). Only
# two of them are shown to the user, all others are diagnostics.
_NO_AGENT_DATA_PREFIX = "Agent sections: unavailable via "
_PACKAGES_ONLY_PREFIX = "Installed packages without runtime evidence (info only): "
# "<source> (<error>)", source e.g. "get-agent-output @cached" or
# "remote get-agent-output @cached (site <id>)"
_NO_AGENT_DATA_RE = re.compile(r"^(?:remote )?get-agent-output @cached(?: \(site [^)]*\))? \((.*)\)$")


def no_agent_data_reason(source_lines: Sequence[str]) -> str | None:
    """Error text if no agent data was available for the host, else None."""
    for line in source_lines:
        if line.startswith(_NO_AGENT_DATA_PREFIX):
            rest = line[len(_NO_AGENT_DATA_PREFIX):]
            match = _NO_AGENT_DATA_RE.match(rest)
            return match.group(1) if match else rest
    return None


def findings_text(evaluation: Evaluation, source_lines: Sequence[str]) -> str:
    """Findings incl. a reference to missing agent data (the status stays
    as evaluated: a host without agent data is not a coverage problem)."""
    if no_agent_data_reason(source_lines) is None:
        return evaluation.findings
    if evaluation.status == "OK":
        return "No open findings, but currently no agent data available. See details."
    return f"{evaluation.findings} | No agent data available. See details."


def visible_source_lines(source_lines: Sequence[str]) -> tuple[list[str], list[str]]:
    """(notes, ignored) for display: missing agent data as a note (the result
    of the host is incomplete), installed packages without a running service
    as ignored lines (one per subsystem)."""
    notes: list[str] = []
    ignored: list[str] = []
    reason = no_agent_data_reason(source_lines)
    if reason is not None:
        notes.append(f"No agent data available, the result of this host is incomplete. ({reason})")
    for line in source_lines:
        if line.startswith(_PACKAGES_ONLY_PREFIX):
            for entry in line[len(_PACKAGES_ONLY_PREFIX):].split("; "):
                title, sep, packages = entry.rpartition(" (")
                if not sep:
                    title, packages = entry, ""
                packages = packages.rstrip(")")
                ignored.append(
                    f"{title}: package installed ({packages}), not running – not counted"
                    if packages
                    else f"{title}: package installed, not running – not counted"
                )
    return notes, ignored


def detail_sections(
    evaluation: Evaluation, source_lines: Sequence[str], mode: str = GENERIC_WARN
) -> list[tuple[str, list[str]]]:
    """Detail sections in fixed order; empty ones are omitted."""
    candidate_heading = (
        "Candidates (generic match):"
        if mode == GENERIC_WARN
        else "Candidates (generic match, info only):"
    )
    notes, packages_only = visible_source_lines(source_lines)
    return [
        (heading, list(lines))
        for heading, lines in (
            ("Note:", notes),
            ("Unmonitored:", evaluation.unmonitored_lines),
            (candidate_heading, evaluation.candidate_lines),
            ("Ignored:", [*evaluation.ignored_lines, *packages_only]),
            ("Already monitored:", evaluation.monitored_lines),
        )
        if lines
    ]


def detail_lines(
    evaluation: Evaluation, source_lines: Sequence[str], mode: str = GENERIC_WARN
) -> list[str]:
    out: list[str] = []
    for heading, lines in detail_sections(evaluation, source_lines, mode):
        if out:
            out.append("")
        out.append(heading)
        out.extend(lines)
    return out
