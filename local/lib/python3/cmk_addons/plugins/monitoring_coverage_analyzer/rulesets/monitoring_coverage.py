#!/usr/bin/env python3
"""Setup rule for the "Checkmk Monitoring Coverage" service.

Used by the check plug-in AND by the GUI page "Analyze monitoring coverage"
(the page evaluates the same rule per host), so both show the same result.
"""
from cmk.rulesets.v1 import Help, Label, Title
from cmk.rulesets.v1.form_specs import (
    DefaultValue,
    DictElement,
    Dictionary,
    List,
    MatchingScope,
    RegularExpression,
    SingleChoice,
    SingleChoiceElement,
    String,
)
from cmk.rulesets.v1.rule_specs import CheckParameters, HostCondition, Topic


def _ignore_entry() -> Dictionary:
    return Dictionary(
        elements={
            "subsystem": DictElement(
                required=False,
                parameter_form=RegularExpression(
                    title=Title("Subsystem"),
                    help_text=Help(
                        "Matches the subsystem title or its internal name, "
                        "e.g. 'Microsoft SQL Server' or '^mssql$'."
                    ),
                    predefined_help_text=MatchingScope.INFIX,
                ),
            ),
            "plugin": DictElement(
                required=False,
                parameter_form=RegularExpression(
                    title=Title("Check plug-in"),
                    help_text=Help(
                        "Matches any of the suggested check plug-ins, e.g. '^mssql_'."
                    ),
                    predefined_help_text=MatchingScope.INFIX,
                ),
            ),
            "evidence": DictElement(
                required=False,
                parameter_form=RegularExpression(
                    title=Title("Evidence"),
                    help_text=Help(
                        "Matches any of the evidence texts, e.g. \"Windows "
                        "service 'MSSQLSERVER'\" or 'unit .acme-sh'."
                    ),
                    predefined_help_text=MatchingScope.INFIX,
                ),
            ),
            "comment": DictElement(
                required=False,
                parameter_form=String(
                    title=Title("Reason"),
                    help_text=Help("Shown next to the ignored finding."),
                ),
            ),
        },
    )


def _parameter_form() -> Dictionary:
    return Dictionary(
        help_text=Help(
            "Applies to the 'Checkmk Monitoring Coverage' service and to the "
            "page Setup > Maintenance > Analyze monitoring coverage. Changes "
            "take effect after activating changes, no new analysis run is "
            "needed."
        ),
        elements={
            "ignore": DictElement(
                required=False,
                parameter_form=List(
                    title=Title("Ignore findings"),
                    help_text=Help(
                        "False positives or deliberately accepted monitoring "
                        "gaps. All regular expressions set in one entry must "
                        "match (case-insensitive, search anywhere). Entries "
                        "without any expression are ignored. Ignored findings "
                        "are listed separately and do not count for status "
                        "and coverage."
                    ),
                    element_template=_ignore_entry(),
                    add_element_label=Label("Add ignore entry"),
                ),
            ),
            "fuzzy_search": DictElement(
                required=False,
                parameter_form=SingleChoice(
                    title=Title("Disable fuzzy search for potential check candidates"),
                    help_text=Help(
                        "Use or disable candidates found by fuzzy matching running "
                        "services and processes to the available Checkmk check "
                        "plug-ins. Enabled by default."
                    ),
                    elements=[
                        SingleChoiceElement(
                            name="off",
                            title=Title("Disable (no fuzzy candidates are shown)"),
                        ),
                        SingleChoiceElement(
                            name="info",
                            title=Title("Show as info only (no effect on status and coverage)"),
                        ),
                        SingleChoiceElement(
                            name="warn",
                            title=Title("Enable (treat like other findings, WARN, counts for coverage)"),
                        ),
                    ],
                    prefill=DefaultValue("warn"),
                ),
            ),
        },
    )


rule_spec_monitoring_coverage_analyzer = CheckParameters(
    name="checkmk_monitoring_coverage",
    title=Title("Monitoring coverage analysis"),
    topic=Topic.GENERAL,
    parameter_form=_parameter_form,
    condition=HostCondition(),
)
