#!/usr/bin/env python3
"""WATO-Menuintegration: Setup > Maintenance > "Analyze monitoring coverage".

Legacy-WATO-Plugin (share/check_mk/web/plugins/wato/), wird von
cmk.gui.utils.plugins.load_web_plugins("wato", globals()) beim Site-Start
importiert und registriert per main_module_registry.register(...) einen
zusaetzlichen Menuepunkt im Setup-Hauptmenue unter dem bestehenden Topic
"Maintenance" (main_module_topic_registry["maintenance"], siehe Vorbild
"Analyze configuration" in cmk/gui/wato/_main_modules.py).

Der Menuepunkt verweist per Direkt-URL (kein WATO "mode") auf die in
local/lib/python3/cmk/gui/plugins/pages/monitoring_coverage_analyzer.py
registrierte Page "monitoring_coverage_analyzer" - siehe
ABCMainModule.get_url(): enthaelt mode_or_url ein "/" oder endet auf ".py",
wird der Wert als direkte URL statt als wato.py?mode=... interpretiert.
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
        # Kein eigenes Permission-Konzept fuer diesen PoC/Ausbau -
        # sichtbar fuer alle Setup-Nutzer wie "Analyze configuration".
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
