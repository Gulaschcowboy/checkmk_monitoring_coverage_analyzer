#!/usr/bin/env python3
"""Global Setup options for the 'monitoring_coverage_analyzer' piggyback job.

Extension stage 2.0.0 (piggyback extension):

Registers two new entries under Setup > Global settings, in a
dedicated ConfigVariableGroup "Monitoring Coverage Analyzer":

  - generate_piggyback_data (checkbox, default True): controls whether the
    full run, in addition to the GUI result, also produces piggyback raw
    data per host (via cmk.piggyback.backend.store_piggyback_raw_data()).
  - piggyback_interval_hours (Age/Integer in hours, default 24): after
    how many hours a new REAL full run (Livestatus query +
    rule evaluation) is due. The 5-minute refresh tick is deliberately
    NOT configurable and is therefore not represented here as a
    separate variable.

IMPORTANT FINDING (Checkmk 2.5, see cmk.gui.utils.plugins.register()/
cmk.gui.utils.load_web_plugins()): this Checkmk version has NO legacy
plugin namespace
"globals" (only "config", "dashboard", "icons", "metrics", "pages",
"perfometer", "sidebar", "views", "visuals", "wato" - see
cmk.gui.utils.plugins.register()). A location
share/check_mk/web/plugins/globals/ does not exist in this Checkmk
version (load_web_plugins() is never called
for "globals", so the file would NEVER be loaded).

Therefore the ConfigVariable/ConfigVariableGroup registration is instead done as a
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

import inspect
from typing import Any

from cmk.gui.i18n import _
from cmk.gui.watolib.config_domain_name import (
    ConfigVariable,
    ConfigVariableGroup,
    GlobalSettingsContext,
    config_variable_group_registry,
    config_variable_registry,
)
from cmk.gui.watolib.config_domains import ConfigDomainGUI

# Checkmk 3.0 defines global settings with Form Specs (form_spec=),
# 2.5 with valuespecs (valuespec=).
_USE_FORM_SPEC = "form_spec" in inspect.signature(ConfigVariable.__init__).parameters

ConfigVariableGroupMonitoringCoverageAnalyzer = ConfigVariableGroup(
    title=_("Monitoring Coverage Analyzer (MCA)"),
    sort_index=105,
)
config_variable_group_registry.register(ConfigVariableGroupMonitoringCoverageAnalyzer)

_GENERATE_TITLE = "Generate per-host piggyback data (MCA)"
_GENERATE_LABEL = "Generate piggyback data for the 'Checkmk Monitoring Coverage' service"
_GENERATE_HELP = (
    "If enabled, every full analysis run additionally writes a "
    "piggyback JSON payload per host (source host name "
    "'monitoring_coverage_analyzer'), which the agent-based check "
    "plug-in turns into a 'Checkmk Monitoring Coverage' service "
    "for that host after the next service discovery. "
    "If disabled, nothing is written, and the piggyback data of "
    "this source on this site is removed by the next refresh tick "
    "(every 5 minutes) or analysis run. Copies already distributed "
    "to remote sites by the piggyback hub are not removed directly; "
    "they expire there with the maximum piggyback age and are then "
    "removed by Checkmk."
)

# Deliberately an integer in whole hours (not an Age in seconds), so
# consumers cannot mix up seconds and hours.
_INTERVAL_TITLE = "Full analysis run interval in hours (MCA)"
_INTERVAL_HELP = (
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
)


def _valuespec_generate_piggyback_data(_context: GlobalSettingsContext) -> Any:
    from cmk.gui.valuespec import Checkbox

    return Checkbox(
        title=_(_GENERATE_TITLE), label=_(_GENERATE_LABEL), help=_(_GENERATE_HELP),
        default_value=True,
    )


def _form_spec_generate_piggyback_data(_context: GlobalSettingsContext) -> Any:
    from cmk.rulesets.v1 import Help, Label, Title
    from cmk.rulesets.v1.form_specs import BooleanChoice, DefaultValue

    return BooleanChoice(
        title=Title(_GENERATE_TITLE),
        label=Label(_GENERATE_LABEL),
        help_text=Help(_GENERATE_HELP),
        prefill=DefaultValue(True),
    )


def _valuespec_piggyback_interval_hours(_context: GlobalSettingsContext) -> Any:
    from cmk.gui.valuespec import Integer

    return Integer(
        title=_(_INTERVAL_TITLE), help=_(_INTERVAL_HELP),
        default_value=24, minvalue=1, unit=_("hours"),
    )


def _form_spec_piggyback_interval_hours(_context: GlobalSettingsContext) -> Any:
    from cmk.rulesets.v1 import Help, Title
    from cmk.rulesets.v1.form_specs import DefaultValue, Integer, validators

    return Integer(
        title=Title(_INTERVAL_TITLE),
        help_text=Help(_INTERVAL_HELP),
        prefill=DefaultValue(24),
        unit_symbol="hours",
        custom_validate=(validators.NumberInRange(min_value=1),),
    )


def _config_variable(ident: str, valuespec: Any, form_spec: Any) -> ConfigVariable:
    spec = {"form_spec": form_spec} if _USE_FORM_SPEC else {"valuespec": valuespec}
    return ConfigVariable(
        group=ConfigVariableGroupMonitoringCoverageAnalyzer,
        primary_domain=ConfigDomainGUI,
        ident=ident,
        **spec,
    )


ConfigVariableGeneratePiggybackData = _config_variable(
    "generate_piggyback_data",
    _valuespec_generate_piggyback_data,
    _form_spec_generate_piggyback_data,
)
config_variable_registry.register(ConfigVariableGeneratePiggybackData)

ConfigVariablePiggybackIntervalHours = _config_variable(
    "piggyback_interval_hours",
    _valuespec_piggyback_interval_hours,
    _form_spec_piggyback_interval_hours,
)
config_variable_registry.register(ConfigVariablePiggybackIntervalHours)
