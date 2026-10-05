# Analyze monitoring coverage

Monitoring Coverage Analyzer (MCA): Checkmk extension (MKP) that finds
applications and subsystems on agent-monitored hosts that are running but
not yet monitored, and tells you which plug-in or special agent would
cover them.

MCA works entirely with data Checkmk already has. Nothing needs to be
deployed to your hosts.

Requires Checkmk 2.5.0p15 or later.

## Components

- **GUI page** "Setup > Maintenance > Analyze monitoring coverage":
  Shows overall coverage across all hosts and a table with coverage, open
  findings and already monitored subsystems per host. "Re-run analysis"
  starts a fresh analysis run in the background. The run's log is written
  to `var/log/monitoring_coverage_analyzer.log`.
  Cluster hosts are not analyzed (they have no agent output of their
  own); their clustered services count as monitored on the nodes.
- **Piggyback service** "Checkmk Monitoring Coverage" per host
  (check plug-in `checkmk_monitoring_coverage`), fed from the cached
  analysis result. Can be disabled in the global settings.
- **Command line tool** `mcactl` (run as the site user):
  - `setup`: installs or updates the cron jobs (MKPs cannot register cron
    jobs themselves). Run it once after installing; updates only need it
    again if the changelog says so.
  - `status`: shows the cron jobs and the time of the last runs.
  - `uninstall`: removes the cron jobs.
  - `refresh` (only for cron): re-sends the last result as
    piggyback data to avoid piggyback staleness.
  - `fullrun` (only for cron, daily at 05:00): re-runs the analysis if the
    configured interval (Global setting, default 24 h) has elapsed.
  - `rerun`: runs the analysis right away (also used by the "Re-run
    analysis" button in the GUI).
  - `support-data`: collects anonymized data for a bug report (see
    "Support data").
- **Setup rule** "Monitoring coverage analysis (MCA)" (Setup > Services >
  Service monitoring rules): ignore findings (false positives or accepted
  gaps) by regular expressions on subsystem, check plug-in and evidence.
  You can also disable the fuzzy search for potential check candidates
  (or show its results as info only) for single or all hosts. By default,
  fuzzy candidates are treated like other findings (WARN); the choice
  'Enable' re-enables them where a more general rule disables them.
  Applies to the piggyback service and the GUI page alike, right after
  activating changes.
- **Rules file** `monitoring_coverage_analyzer_rules.json`: internal
  detection logic (aliases, titles, hints and detection rules).

## Evidence sources

1. Services already monitored (Livestatus check commands). Running checks
   without a curated rule (e.g. from MKPs) are listed and counted as
   monitored too, except base OS checks, stop tokens, `generic_ignore_families`
   and `agent_builtin_families`.
2. Host labels (except the operating system labels `cmk/os_*` and
   `cmk/site`; for built-in labels only the part after `cmk/` counts).
3. Agent sections with real data.
4. Deployed agent plug-ins (`checkmk_agent_plugins_*` sections).
5. Runtime evidence via `detect` rules: running systemd units, processes
   and Windows services.
6. Installed inventory packages: informational only, no effect on status,
   except for `detect` rules with a `package:` condition (tools without a
   running service, e.g. apt).
7. Generic match/fuzzy search: the leading name part of running systemd units,
   processes and Windows services is matched against the families of all
   agent-based check plug-ins of the site, including installed MKPs. This
   finds subsystems without a curated rule. Built-in filters: SNMP-only
   plug-ins, families covered by curated rules, stop tokens,
   `generic_ignore_families`, `agent_builtin_families`, families already
   monitored on the host, and
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

## Permissions

The page "Analyze monitoring coverage" (Setup > Maintenance) needs the
Setup permission "Monitoring coverage analysis (MCA)" (default: admin
role only) in addition to "Use Setup". Users with "Read access to all
modules" can view the page, but cannot start a new analysis run. Grant
the permission to other roles under Setup > Users > Roles & permissions.

## Distributed monitoring

Install and run the setup on the central site only. The central site
analyzes the hosts of all sites; the agent output of hosts on a remote
site is fetched from that site via remote automation; remote sites must
run 2.5.0p15 or later.
The service data reaches the remote sites through the piggyback hub,
which must be enabled on the central and the remote sites. The remote
sites only need the package itself (e.g. via "Replicate extensions").
If a remote site cannot be reached, its hosts show an "analysis
incomplete" finding instead of a result.

With the global setting "Generate per-host piggyback data (MCA)"
disabled, no piggyback data is written, and data written earlier by this
package is removed on the central site by the next refresh tick or
analysis run. Copies already distributed to remote sites expire there
with the maximum piggyback age and are then removed by Checkmk.

## Support data

```
mcactl support-data
```

collects the MCA result and the facts it is based on into a JSON file
that you can optionally attach to a bug report. Nothing is sent
automatically. The data is used to analyze the report and, in aggregated
form, to improve the detection rules.

Before collecting, the tool lists what is included: the MCA result per
host, your MCA Setup rules, Checkmk and package version, MCA settings, the
rules file (its content only if modified locally), built-in host labels
and tags, the names of the monitored check plug-ins (no service names)
and the names of the agent sections. Custom labels and tag groups are
only counted, rule comments are removed.

Host, site, folder and server names, IP addresses and domains are
replaced by pseudonyms such as `host-1a2b3c4d`. They are stable per site
(the key stays on the site), so later reports use the same pseudonyms. A
second file with the mapping pseudonym -> real name is written next to
it: keep it, do not send it. At the end, strings that still look like a
name or address are listed for you to check.

The tool then asks whether to include the names of all Windows services,
systemd units and processes per host. They are very helpful for finding
undetected software, but they are not anonymized and may reveal
applications or your organization. `--with-runtime` includes them without
asking; without a terminal they are left out. `--output DIR` sets the
directory (default: current directory).

## Repository layout

```
local/bin/                                   mcactl (setup + cron tool)
local/lib/python3/cmk_addons/plugins/...     check plug-in, Setup rule,
                                             shared evaluation (lib/)
local/share/check_mk/web/plugins/pages/      GUI page + rules file
local/share/check_mk/web/plugins/wato/       menu entry, global settings
local/share/check_mk/web/plugins/config/     config defaults
local/share/doc/monitoring_coverage_analyzer cron template
tests/                                       unit tests (not packaged)
```
