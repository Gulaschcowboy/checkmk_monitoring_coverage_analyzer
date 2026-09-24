#!/usr/bin/env python3
"""Globale Setup-Optionen fuer den 'monitoring_coverage_analyzer' Piggyback-Job.

Ausbaustufe 2.0.0 (Piggyback-Erweiterung, siehe PLAN_piggyback_background_job.md):

Registriert zwei neue Eintraege unter Setup > Global settings, in einer
eigenen ConfigVariableGroup "Monitoring Coverage Analyzer":

  - generate_piggyback_data (Checkbox, Default True): steuert, ob der
    Full-Run zusaetzlich zum GUI-Ergebnis auch Piggyback-Rohdaten pro Host
    erzeugt (via cmk.piggyback.backend.store_piggyback_raw_data()).
  - piggyback_interval_hours (Age/Integer in Stunden, Default 24): nach
    wie vielen Stunden ein neuer ECHTER Full-Run (Livestatus-Query +
    Regelauswertung) faellig ist. Der 5-Minuten-Refresh-Tick ist bewusst
    NICHT konfigurierbar (siehe Plan-Entscheidung 7) und daher hier nicht
    als eigene Variable vertreten.

WICHTIGER BEFUND (live auf der Test-Site / Checkmk 2.5.0p12 Ultimate per grep
in cmk.gui.utils.plugins.register()/cmk.gui.utils.load_web_plugins()
verifiziert): diese Checkmk-Version kennt KEINEN Legacy-Plugin-Namespace
"globals" (nur "config", "dashboard", "icons", "metrics", "pages",
"perfometer", "sidebar", "views", "visuals", "wato" - siehe
cmk.gui.utils.plugins.register()). Der im urspruenglichen Plan
angenommene Ablageort share/check_mk/web/plugins/globals/ existiert bei
dieser Checkmk-Version schlicht nicht (load_web_plugins() wird fuer
"globals" nirgends aufgerufen, die Datei wuerde also NIE geladen).

Pragmatische, dem Plan am naechsten kommende Loesung: die
ConfigVariable-/ConfigVariableGroup-Registrierung erfolgt stattdessen als
regulaeres "wato"-Legacy-Plugin (cmk.gui.wato.register() ruft
utils.load_web_plugins("wato", globals()) auf) - das ist exakt der
Mechanismus, den auch eingebaute Checkmk-Konfigurationsvariablen wie
graph_timeranges nutzen (siehe cmk.gui.graphing._settings, als "wato"-
bzw. Modul-Plugin registriert). Der Default-Wert der beiden Variablen
wird zusaetzlich per plugins/config/monitoring_coverage_analyzer.py
(siehe dort) gesetzt, weil ConfigVariable selbst keinen Default-Wert
traegt - der Default-Mechanismus von Checkmk erwartet ihn als
Modul-Attribut im "config"-Legacy-Plugin-Namespace (siehe
cmk.gui.config._get_default_config_from_legacy_plugins()).
"""
from __future__ import annotations

from cmk.gui.i18n import _
from cmk.gui.valuespec import Checkbox, Integer
from cmk.gui.watolib.config_domain_name import (
    ConfigVariable,
    ConfigVariableGroup,
    GlobalSettingsContext,
    config_variable_group_registry,
    config_variable_registry,
)
from cmk.gui.watolib.config_domains import ConfigDomainGUI

ConfigVariableGroupMonitoringCoverageAnalyzer = ConfigVariableGroup(
    title=_("Monitoring Coverage Analyzer"),
    sort_index=105,
)
config_variable_group_registry.register(ConfigVariableGroupMonitoringCoverageAnalyzer)


def _valuespec_generate_piggyback_data(_context: GlobalSettingsContext) -> Checkbox:
    return Checkbox(
        title=_("Generate per-host piggyback data"),
        label=_("Generate piggyback data for the 'Checkmk Monitoring Coverage' service"),
        help=_(
            "If enabled, every full analysis run additionally writes a "
            "piggyback JSON payload per host (source host name "
            "'monitoring_coverage_analyzer'), which the agent-based check "
            "plug-in turns into a 'Checkmk Monitoring Coverage' service "
            "for that host after the next service discovery."
        ),
        default_value=True,
    )


ConfigVariableGeneratePiggybackData = ConfigVariable(
    group=ConfigVariableGroupMonitoringCoverageAnalyzer,
    primary_domain=ConfigDomainGUI,
    ident="generate_piggyback_data",
    valuespec=_valuespec_generate_piggyback_data,
)
config_variable_registry.register(ConfigVariableGeneratePiggybackData)


def _valuespec_piggyback_interval_hours(_context: GlobalSettingsContext) -> Integer:
    # Bewusst Integer statt Age: ident "piggyback_interval_hours" traegt den
    # Wert direkt in ganzen Stunden (nicht Sekunden wie es Age liefern
    # wuerde) - vermeidet eine stille Sekunden/Stunden-Verwechslung im
    # spaeteren Konsumenten-Code (_query_and_analyze_hosts()-Aufrufer).
    return Integer(
        title=_("Full analysis run interval (hours)"),
        help=_(
            "Minimum time (in hours) between two REAL full analysis runs "
            "(Livestatus query + rule evaluation, producing new content). "
            "The full run is checked once a day at 05:00 (cron job "
            "installed by monitoring_coverage_analyzer-setup), so the "
            "effective granularity is whole days: 24 = daily, 48 = every "
            "second day; values below 24 behave like 24. "
            "Between full runs, a lightweight refresh tick (every 5 "
            "minutes, fixed, not configurable) merely re-sends the last "
            "computed content with a fresh piggyback transfer timestamp, "
            "without recomputing anything."
        ),
        default_value=24,
        minvalue=1,
        unit=_("hours"),
    )


ConfigVariablePiggybackIntervalHours = ConfigVariable(
    group=ConfigVariableGroupMonitoringCoverageAnalyzer,
    primary_domain=ConfigDomainGUI,
    ident="piggyback_interval_hours",
    valuespec=_valuespec_piggyback_interval_hours,
)
config_variable_registry.register(ConfigVariablePiggybackIntervalHours)
