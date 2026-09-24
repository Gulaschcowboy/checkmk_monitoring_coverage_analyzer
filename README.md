# Analyze monitoring coverage

Checkmk extension (MKP) that finds applications and subsystems on
agent-monitored hosts that are running but not yet monitored, and tells you
which plug-in or special agent would cover them.

Requires Checkmk 2.5.0p14 or later (uses `get-agent-output ... @cached`,
so no additional agent queries are made).

## Components

- **GUI page** "Setup > Maintenance > Analyze monitoring coverage":
  site-wide table with coverage per host, findings, already monitored
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

## Evidence sources

1. Services already monitored (Livestatus check commands).
2. Host labels.
3. Agent sections with real data (placeholder content does not count).
4. Deployed agent plug-ins (`checkmk_agent_plugins_*` sections).
5. Runtime evidence via `detect` rules: running systemd units, processes
   and Windows services.
6. Installed inventory packages: informational only, no effect on status.

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

When updating from 0.9.0-b10 or older, run a service discovery for the
"Checkmk Monitoring Coverage" service (check plug-in was renamed).

## Repository layout

```
local/bin/                                   cron + setup scripts
local/lib/python3/cmk_addons/plugins/...     piggyback check plug-in
local/share/check_mk/web/plugins/pages/      GUI page + rules file
local/share/check_mk/web/plugins/wato/       menu entry, global settings
local/share/check_mk/web/plugins/config/     config defaults
local/share/doc/monitoring_coverage_analyzer cron template
```

## Status

Beta (0.9.0-bN). Not yet released.
