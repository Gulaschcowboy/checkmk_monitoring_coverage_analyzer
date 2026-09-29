#!/usr/bin/env python3
"""WATO menu integration: Setup > Maintenance > "Analyze monitoring coverage".

Legacy WATO plugin (share/check_mk/web/plugins/wato/), imported by
cmk.gui.utils.plugins.load_web_plugins("wato", globals()) at site start;
it registers via main_module_registry.register(...) an additional menu
entry in the Setup main menu under the existing topic
"Maintenance" (main_module_topic_registry["maintenance"], see the model
"Analyze configuration" in cmk/gui/wato/_main_modules.py).

The menu entry points via direct URL (no WATO "mode") to the page
"monitoring_coverage_analyzer" registered in
local/lib/python3/cmk/gui/plugins/pages/monitoring_coverage_analyzer.py - see
ABCMainModule.get_url(): if mode_or_url contains a "/" or ends with ".py",
the value is interpreted as a direct URL instead of wato.py?mode=....
"""
from __future__ import annotations

from typing import override

from cmk.gui.i18n import _
from cmk.gui.type_defs import DynamicIcon, IconNames, StaticIcon
from cmk.gui.wato import MainModuleTopicMaintenance
from cmk.gui.watolib.main_menu import ABCMainModule, MainModuleTopic, main_module_registry


class MainModuleMonitoringCoverageAnalyzer(ABCMainModule):
    @property
    @override
    def mode_or_url(self) -> str:
        return "monitoring_coverage_analyzer.py"

    @property
    @override
    def topic(self) -> MainModuleTopic:
        return MainModuleTopicMaintenance

    @property
    @override
    def title(self) -> str:
        return _("Analyze monitoring coverage")

    @property
    @override
    def icon(self) -> StaticIcon | DynamicIcon:
        return StaticIcon(IconNames.analyze_config)

    @property
    @override
    def permission(self) -> None | str:
        # No dedicated permission concept for this PoC/extension -
        # visible to all Setup users, like "Analyze configuration".
        return None

    @property
    @override
    def description(self) -> str:
        return _(
            "Run an on-demand analysis of monitoring coverage: correlates "
            "monitored Checkmk check plug-ins against plug-ins available on "
            "this site and host labels to find installed but unmonitored "
            "applications."
        )

    @property
    @override
    def sort_index(self) -> int:
        return 85

    @property
    @override
    def is_show_more(self) -> bool:
        return False


main_module_registry.register(MainModuleMonitoringCoverageAnalyzer)
