#!/usr/bin/env python3
# Checkmk Monitoring Coverage - Agent-based Check-Plugin (Check API v2)
#
# Expansion stage 2.0.0 of the GUI page "monitoring_coverage_analyzer" (see
# PLAN_piggyback_background_job.md): evaluates the piggyback section
# "checkmk_monitoring_coverage", which is written per host by the GUI page
# (see local/share/check_mk/web/plugins/pages/monitoring_coverage_analyzer.py,
# functions _write_piggyback_data()/_run_piggyback_full()/
# _run_piggyback_refresh()) under the piggyback source
# "monitoring_coverage_analyzer".
#
# Deliberately a standalone plugin (own namespace
# "monitoring_coverage_analyzer" under cmk_addons_plugins/, NOT under
# the namespace of the reference project "monitoring_coverage") with
# check plugin/section "checkmk_monitoring_coverage", service
# "Checkmk Monitoring Coverage".
#
# 0.9.0-b11: "2" suffix removed everywhere (file, section, check plugin,
# function names; service name already in b10). The reference project
# monitoring_coverage was discarded - compatibility/coexistence no longer
# needed. Existing autochecks "checkmk_monitoring_coverage2" become
# "Unimplemented check" after the update and must be replaced via
# discovery.
#
# TWO SEPARATE TIMESTAMPS in the service output are MANDATORY
# (transparency principle, plan decision 11, no exceptions):
#   - "Content last computed: ..." - time of the last REAL
#     full run (Livestatus query + rule evaluation, new content).
#   - "Piggyback transfer last refreshed: ..." - time of the last
#     refresh tick (only store_piggyback_raw_data called again with the same
#     content + new message_timestamp).
# Both lines ALWAYS appear together.
#
# 0.9.0-b21: status, coverage and texts are computed here from the unfiltered
# items of the section (lib/evaluate.py, shared with the GUI page) - so
# ignore rules and the mode for generic candidates (Setup rule
# "Monitoring coverage analysis") take effect right after
# "Activate changes", without a new analysis run.
from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any

from cmk.agent_based.v2 import (
    AgentSection,
    CheckPlugin,
    CheckResult,
    DiscoveryResult,
    Metric,
    Result,
    Service,
    State,
    StringTable,
)

from cmk_addons.plugins.monitoring_coverage_analyzer.lib import evaluate as _ev

SECTION_NAME = "checkmk_monitoring_coverage"


def _parse_monitoring_coverage(string_table: StringTable) -> Mapping[str, Any] | None:
    """Parses the JSON line of the sep(0) section. Robust against
    multiple lines (only the first parsed JSON line is used -
    store_piggyback_raw_data() writes exactly one line per refresh/full run
    anyway, see _build_piggyback_payload())."""
    for row in string_table:
        raw = "".join(row) if isinstance(row, list) else str(row)
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    return None


agent_section_checkmk_monitoring_coverage = AgentSection(
    name=SECTION_NAME,
    parse_function=_parse_monitoring_coverage,
)


def discover_monitoring_coverage(section: Mapping[str, Any]) -> DiscoveryResult:
    yield Service()


def _format_age(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 3600:
        return f"{seconds / 60:.0f}min"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    return f"{hours}h {minutes}min"


def check_monitoring_coverage(params: Mapping[str, Any], section: Mapping[str, Any]) -> CheckResult:
    now = time.time()
    items = section.get("items")
    if isinstance(items, list):
        evaluation = _ev.evaluate(items, params)
        status = evaluation.status
        coverage_pct: Any = evaluation.coverage_pct
        fraction_text = evaluation.fraction_text
        findings = evaluation.findings
        detail_lines: Any = _ev.detail_lines(
            evaluation,
            [str(x) for x in section.get("source_lines") or []],
            _ev.generic_mode(params),
        )
    else:
        # Section from an older analysis run (before b21): finished result,
        # rules only take effect after the next run.
        status = str(section.get("status", "UNKNOWN"))
        coverage_pct = section.get("coverage_pct")
        fraction_text = str(section.get("fraction_text", ""))
        findings = str(section.get("findings", ""))
        detail_lines = section.get("detail_lines") or []

    state_map = {"OK": State.OK, "WARN": State.WARN, "CRIT": State.CRIT}
    state = state_map.get(status, State.UNKNOWN)

    summary_parts = [fraction_text] if fraction_text else []
    if findings and status != "OK":
        summary_parts.append(findings)
    summary = " | ".join(summary_parts) if summary_parts else f"status={status}"

    yield Result(state=state, summary=summary)

    if isinstance(coverage_pct, (int, float)):
        yield Metric("coverage_percent", float(coverage_pct), boundaries=(0, 100))

    if isinstance(detail_lines, list) and detail_lines:
        yield Result(
            state=State.OK,
            notice="\n".join(str(line) for line in detail_lines),
        )

    # Plan decision 11: TWO SEPARATE timestamp lines, ALWAYS together,
    # English wording exactly as specified in the plan.
    last_full_run = section.get("last_full_run_timestamp")
    last_refresh = section.get("last_piggyback_refresh_timestamp")

    if isinstance(last_full_run, (int, float)):
        full_run_txt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_full_run))
        full_run_age = _format_age(now - last_full_run)
        yield Result(
            state=State.OK,
            notice=f"Content last computed: {full_run_txt} ({full_run_age} ago)",
        )
    else:
        yield Result(state=State.OK, notice="Content last computed: unknown")

    if isinstance(last_refresh, (int, float)):
        refresh_txt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_refresh))
        refresh_age = _format_age(now - last_refresh)
        yield Result(
            state=State.OK,
            notice=(
                f"Piggyback transfer last refreshed: {refresh_txt} "
                f"({refresh_age} ago, technical only)"
            ),
        )
    else:
        yield Result(
            state=State.OK,
            notice="Piggyback transfer last refreshed: unknown (technical only)",
        )


check_plugin_checkmk_monitoring_coverage = CheckPlugin(
    name="checkmk_monitoring_coverage",
    service_name="Checkmk Monitoring Coverage",
    discovery_function=discover_monitoring_coverage,
    check_function=check_monitoring_coverage,
    check_ruleset_name="checkmk_monitoring_coverage",
    check_default_parameters={},
)
