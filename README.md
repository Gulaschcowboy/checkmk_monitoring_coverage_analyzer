# Analyze monitoring coverage

Checkmk extension (MKP) that finds applications and subsystems on
agent-monitored hosts that are running but not yet monitored, and tells you
which plug-in or special agent would cover them.

Requires Checkmk 2.5.0p15 or later (uses `get-agent-output ... @cached`,
so no additional agent queries are made).

## Components

- **GUI page** "Setup > Maintenance > Analyze monitoring coverage":
  overall coverage across all hosts, duration of the last analysis run,
  and a table with coverage per host, findings, already monitored
  subsystems and evidence sources. "Re-run analysis" recomputes on demand.
- **Piggyback service** "Checkmk Monitoring Coverage" per host
  (check plug-in `checkmk_monitoring_coverage`), fed from the cached
  analysis result.
- **Cron script** `monitoring_coverage_analyzer_cron`:
  - `refresh` every 5 minutes: re-sends the last result as piggyback data.
  - `fullrun` daily at 05:00: re-runs the analysis if the configured
    interval (Global setting, default 24 h) has elapsed.
- **Setup script** `monitoring_coverage_analyzer-setup`
  (`status`, `uninstall`): installs the cron jobs. MKPs cannot register
  cron jobs themselves, so run it once after installing or updating.
- **Rules file** `monitoring_coverage_analyzer_rules.json`: aliases,
  titles, hints and detection rules. Can be adjusted without code changes.
- **Setup rule** "Monitoring coverage analysis" (Setup > Services >
  Service monitoring rules): ignore findings (false positives or accepted
  gaps) by regular expressions on subsystem, check plug-in and evidence,
  and choose whether generic candidates are info only or WARN. Applies to
  the service and the GUI page alike, right after activating changes.

## Evidence sources

1. Services already monitored (Livestatus check commands).
2. Host labels.
3. Agent sections with real data (placeholder content does not count).
4. Deployed agent plug-ins (`checkmk_agent_plugins_*` sections).
5. Runtime evidence via `detect` rules: running systemd units, processes
   and Windows services.
6. Installed inventory packages: informational only, no effect on status.
7. Generic match: the leading name part of running systemd units,
   processes and Windows services is matched against the families of all
   agent-based check plug-ins of the site, including installed MKPs. This
   finds subsystems without a curated rule. Built-in filters: SNMP-only
   plug-ins, families covered by curated rules, stop tokens,
   `generic_ignore_families`, families already monitored on the host, and
   families running on nearly all hosts of the same OS. Shown as info by
   default (see Setup rule).

Hosts are analyzed if they are monitored via the Checkmk agent (TCP) and
report a supported operating system (`cmk/os_type`, or `cmk/os_family` for
older agents): linux, windows, freebsd, solaris, aix.

If an agent plug-in delivers data but none of its services is monitored,
and the "Check_MK Discovery" service lists all of them as disabled by rule
("Service ignored") with none left undecided, the subsystem counts as
covered: the plug-in is deployed and every service was decided on
deliberately.

A subsystem is reported only if the site has a matching check plug-in
(`cmk -L`). Rules may exclude operating systems where a plug-in cannot
run (`not_on_os`).

## Installation

```
mkp add monitoring_coverage_analyzer-<version>.mkp
mkp enable monitoring_coverage_analyzer <version>
monitoring_coverage_analyzer-setup
omd restart apache
```

## Repository layout

```
local/bin/                                   cron + setup scripts
local/lib/python3/cmk_addons/plugins/...     check plug-in, Setup rule,
                                             shared evaluation (lib/)
local/share/check_mk/web/plugins/pages/      GUI page + rules file
local/share/check_mk/web/plugins/wato/       menu entry, global settings
local/share/check_mk/web/plugins/config/     config defaults
local/share/doc/monitoring_coverage_analyzer cron template
```

## Status

Beta (0.9.0-bN). Not yet released.
