# Analyze monitoring coverage

Monitoring Coverage Analyzer (MCA): Checkmk extension (MKP) that finds applications and subsystems on
agent-monitored hosts that are running but not yet monitored, and tells you
which plug-in or special agent would cover them.

Requires Checkmk 2.5.0p15 or later (uses `get-agent-output ... @cached`,
so no additional agent queries are made).

## Components

"MCA" is the short name used throughout: command line tool `mcactl`,
Setup rule and global settings. Searching for "MCA" in the Setup search
finds the GUI page, the Setup rule and the global settings.

- **GUI page** "Setup > Maintenance > Analyze monitoring coverage":
  overall coverage across all hosts, duration of the last analysis run,
  and a table with coverage per host, findings, already monitored
  subsystems and evidence sources. "Re-run analysis" starts the analysis
  in the background (outside the web server, so large sites do not hit the
  Apache timeout); the page reloads until it has finished. The run's log
  is written to `var/log/monitoring_coverage_analyzer.log`.
  Cluster hosts are not analyzed (they have no agent output of their
  own); their clustered services count as monitored on the nodes.
- **Piggyback service** "Checkmk Monitoring Coverage" per host
  (check plug-in `checkmk_monitoring_coverage`), fed from the cached
  analysis result.
- **Command line tool** `mcactl` (run as the site user):
  - `setup`: installs or updates the cron jobs. MKPs cannot register cron
    jobs themselves, so run it once after installing or updating.
  - `status`: shows the cron jobs and the time of the last runs.
  - `uninstall`: removes the cron jobs.
  - `refresh` (cron, every 5 minutes): re-sends the last result as
    piggyback data.
  - `fullrun` (cron, daily at 05:00): re-runs the analysis if the
    configured interval (Global setting, default 24 h) has elapsed.
  - `rerun`: runs the analysis right away (also used by "Re-run
    analysis").
  - `refresh`, `status` and the `fullrun` due check do not load the
    Checkmk GUI and take well under a second; only an actual analysis run
    does.
- **Rules file** `monitoring_coverage_analyzer_rules.json`: aliases,
  titles, hints and detection rules. Can be adjusted without code changes.
- **Setup rule** "Monitoring coverage analysis (MCA)" (Setup > Services >
  Service monitoring rules): ignore findings (false positives or accepted
  gaps) by regular expressions on subsystem, check plug-in and evidence,
  and disable the fuzzy search for potential check candidates (or show its
  results as info only) for single or all hosts. By default, fuzzy
  candidates are treated like other findings (WARN); the choice 'Enable'
  re-enables them where a more general rule disables them. Applies to
  the service and the GUI page alike, right after activating changes.

## Evidence sources

1. Services already monitored (Livestatus check commands).
2. Host labels (except the operating system labels `cmk/os_*` and
   `cmk/site`; for built-in labels only the part after `cmk/` counts).
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

For plug-ins listed in `empty_ok` in the rules file (default:
`windows_tasks`), a deployed plug-in whose section arrives empty also
counts as covered: there is nothing to monitor on that host. A deployed
plug-in that only delivers piggyback data for other hosts counts as
covered if its services are monitored on all of those hosts. For all
other plug-ins, "deployed but no data" stays a finding with the hint to
check the plug-in.

A subsystem is reported only if the site has a matching check plug-in
(`cmk -L`). Rules may exclude operating systems where a plug-in cannot
run (`not_on_os`), or void a running service as evidence on certain hosts
(`unless`, e.g. the Hyper-V management service on a Windows client).

Virtualization guests are detected by their guest services (VMware Tools,
Hyper-V integration services) and are covered once the piggyback data of
the virtualization host arrives. A Hyper-V host counts as covered if its
agent plug-in delivers piggyback data and the VMs are monitored.

## Installation

```
mkp add monitoring_coverage_analyzer-<version>.mkp
mkp enable monitoring_coverage_analyzer <version>
mcactl setup
omd restart apache
```

## Distributed monitoring

Install and run the setup on the central site only. The central site
analyzes the hosts of all sites; the agent output of hosts on a remote
site is fetched from that site via remote automation
(`get-agent-output ... @cached`, remote site must run 2.5.0p15 or later).
The service data reaches the remote sites through the piggyback hub,
which must be enabled on the central and the remote sites. The remote
sites only need the package itself (e.g. via "Replicate extensions").
If a remote site cannot be reached, its hosts show an "analysis
incomplete" finding instead of a result.

## Repository layout

```
local/bin/                                   mcactl (setup + cron tool)
local/lib/python3/cmk_addons/plugins/...     check plug-in, Setup rule,
                                             shared evaluation (lib/)
local/share/check_mk/web/plugins/pages/      GUI page + rules file
local/share/check_mk/web/plugins/wato/       menu entry, global settings
local/share/check_mk/web/plugins/config/     config defaults
local/share/doc/monitoring_coverage_analyzer cron template
```

## Status

Beta (0.9.0-bN). Not yet released.
