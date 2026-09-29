#!/usr/bin/env python3
"""Global Setup options for the 'monitoring_coverage_analyzer' piggyback job.

Extension stage 2.0.0 (piggyback extension, see PLAN_piggyback_background_job.md):

Registers two new entries under Setup > Global settings, in a
dedicated ConfigVariableGroup "Monitoring Coverage Analyzer":

  - generate_piggyback_data (checkbox, default True): controls whether the
    full run, in addition to the GUI result, also produces piggyback raw
    data per host (via cmk.piggyback.backend.store_piggyback_raw_data()).
  - piggyback_interval_hours (Age/Integer in hours, default 24): after
    how many hours a new REAL full run (Livestatus query +
    rule evaluation) is due. The 5-minute refresh tick is deliberately
    NOT configurable (see plan decision 7) and is therefore not
    represented here as a separate variable.

IMPORTANT FINDING (verified live on the test site / Checkmk 2.5.0p12 Ultimate
via grep in cmk.gui.utils.plugins.register()/cmk.gui.utils.load_web_plugins()):
this Checkmk version has NO legacy plugin namespace
"globals" (only "config", "dashboard", "icons", "metrics", "pages",
"perfometer", "sidebar", "views", "visuals", "wato" - see
cmk.gui.utils.plugins.register()). The location
share/check_mk/web/plugins/globals/ assumed in the original plan simply
does not exist in this Checkmk version (load_web_plugins() is never called
for "globals", so the file would NEVER be loaded).

Pragmatic solution closest to the plan: the
ConfigVariable/ConfigVariableGroup registration is instead done as a
regular "wato" legacy plugin (cmk.gui.wato.register() calls
utils.load_web_plugins("wato", globals())) - this is exactly the
mechanism also used by built-in Checkmk configuration variables such as
graph_timeranges (see cmk.gui.graphing._settings, registered as a "wato"
or module plugin). The default value of the two variables is additionally
set via plugins/config/monitoring_coverage_analyzer.py
(see there), because ConfigVariable itself carries no default value -
Checkmk's default mechanism expects it as a module attribute in the
"config" legacy plugin namespace (see
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
    title=_("Monitoring Coverage Analyzer (MCA)"),
    sort_index=105,
)
config_variable_group_registry.register(ConfigVariableGroupMonitoringCoverageAnalyzer)


def _valuespec_generate_piggyback_data(_context: GlobalSettingsContext) -> Checkbox:
    return Checkbox(
        title=_("Generate per-host piggyback data (MCA)"),
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
    # Deliberately Integer instead of Age: ident "piggyback_interval_hours"
    # holds the value directly in whole hours (not seconds as Age would
    # return) - avoids a silent seconds/hours mix-up in later consumer
    # code (_query_and_analyze_hosts() callers).
    return Integer(
        title=_("Full analysis run interval in hours (MCA)"),
        help=_(
            "Minimum time (in hours) between two REAL full analysis runs "
            "(Livestatus query + rule evaluation, producing new content). "
            "The full run is checked once a day at 05:00 (cron job "
            "installed by 'mcactl setup'), so the "
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
