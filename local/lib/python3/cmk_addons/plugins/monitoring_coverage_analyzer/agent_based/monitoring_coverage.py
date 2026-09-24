#!/usr/bin/env python3
# Checkmk Monitoring Coverage - Agent-based Check-Plugin (Check API v2)
#
# Ausbaustufe 2.0.0 der GUI-Seite "monitoring_coverage_analyzer" (siehe
# PLAN_piggyback_background_job.md): wertet die Piggyback-Section
# "checkmk_monitoring_coverage" aus, die von der GUI-Seite (siehe
# local/share/check_mk/web/plugins/pages/monitoring_coverage_analyzer.py,
# Funktionen _write_piggyback_data()/_run_piggyback_full()/
# _run_piggyback_refresh()) unter der Piggyback-Quelle
# "monitoring_coverage_analyzer" pro Host geschrieben wird.
#
# Bewusst als eigenstaendiges Plugin (eigener Namespace
# "monitoring_coverage_analyzer" unter cmk_addons_plugins/, NICHT unter
# dem Namespace des Referenzprojekts "monitoring_coverage") und mit
# Check-Plugin/Section "checkmk_monitoring_coverage", Service
# "Checkmk Monitoring Coverage".
#
# 0.9.0-b11: "2"-Suffix ueberall entfernt (Datei, Section, Check-Plugin,
# Funktionsnamen; Service-Name bereits in b10). Das Referenzprojekt
# monitoring_coverage wurde verworfen - keine Kompatibilitaet/Koexistenz
# mehr noetig. Bestehende Autochecks "checkmk_monitoring_coverage2" werden
# nach dem Update zu "Unimplemented check" und muessen per Discovery
# ersetzt werden.
#
# ZWEI GETRENNTE ZEITSTEMPEL im Service-Output sind PFLICHT
# (Transparenzprinzip, Plan-Entscheidung 11, kein Ausnahmefall):
#   - "Content last computed: ..." - Zeitpunkt des letzten ECHTEN
#     Full-Runs (Livestatus-Query + Regelauswertung, neuer Inhalt).
#   - "Piggyback transfer last refreshed: ..." - Zeitpunkt des letzten
#     Refresh-Ticks (nur store_piggyback_raw_data erneut mit gleichem
#     Inhalt + neuem message_timestamp aufgerufen).
# Beide Zeilen erscheinen IMMER zusammen.
#
# 0.9.0-b21: Status, Coverage und Texte werden hier aus den ungefilterten
# Items der Section berechnet (lib/evaluate.py, gemeinsam mit der GUI-
# Seite) - dadurch wirken Ignore-Regeln und der Modus fuer generische
# Kandidaten (Setup-Regel "Monitoring coverage analysis") sofort nach
# "Activate changes", ohne neuen Analyse-Lauf.
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
    """Parst die JSON-Zeile der sep(0)-Section. Robust gegenueber
    mehreren Zeilen (nur die erste geparste JSON-Zeile wird verwendet -
    store_piggyback_raw_data() schreibt pro Refresh/Full-Run ohnehin
    genau eine Zeile, siehe _build_piggyback_payload())."""
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
            str(params.get("generic_candidates", _ev.GENERIC_INFO)),
        )
    else:
        # Section eines aelteren Analyse-Laufs (vor b21): fertiges Ergebnis,
        # Regeln wirken erst nach dem naechsten Lauf.
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

    # Plan-Entscheidung 11: ZWEI GETRENNTE Zeitstempel-Zeilen, IMMER
    # zusammen, englischer Wortlaut exakt wie im Plan spezifiziert.
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
