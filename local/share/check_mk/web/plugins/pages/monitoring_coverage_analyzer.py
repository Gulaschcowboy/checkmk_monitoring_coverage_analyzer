#!/usr/bin/env python3
"""GUI page "Analyze monitoring coverage".

Loaded as a Checkmk "legacy" GUI plugin (share/check_mk/web/plugins/pages/),
the same mechanism used by cmk.gui.utils.plugins.register() ->
utils.load_web_plugins("pages", globals()). Registration happens as an
import-time side effect (page_registry.register(...) at module level).

Expansion stage 1.3.0 (vs. 1.2.0 PoC):
  UI polish (step 1):
  - The status cell now uses exactly the same CSS class as the regular
    Checkmk service table: "state svcstate state0/1/2" (see
    lib/python3/cmk/gui/bi/view.py: 'classes = "state svcstate state%s" %
    state["state"]' - looked up live on the system via grep, not
    guessed). OK=state0 (green), WARN=state1 (yellow), CRIT=state2 (red) -
    the color scheme comes automatically from the active Checkmk theme
    (facelift/modern-dark), no custom color definition needed.
  - The hostname is now a link to the built-in "host" view
    (view.py?view_name=host&host=<hostname>), generated via
    cmk.gui.utils.urls.makeuri_contextless(request, [("view_name",
    "host"), ("host", host_name)], filename="view.py") - the same
    convention that Checkmk's own painters use for host links.
  - The coverage cell gets a Perf-o-Meter-like bar via inline
    CSS linear-gradient() in the style attribute of the <td>: the filled share
    corresponds to coverage_pct, color green (>=80%), yellow (>=50%), red
    (<50%) - simplified traffic-light scheme, not pixel-exact like the
    original Perf-o-Meter, but visually clearly recognizable as a bar.

  Result caching (step 2):
  - After each run the analysis result is stored as a simple JSON file
    under var/check_mk/web/monitoring_coverage_analyzer_cache.json
    (relative to OMD_ROOT, see _cache_path()) (atomic rename via
    os.replace(), robust against concurrent worker processes) and read
    back on the next page load - so it persists across
    multiple Apache/CMK GUI worker processes (not a pure
    in-memory cache). Deliberately re-implemented with json.dump()/json.load()
    instead of cmk.ccc.store.save_object_to_file()/load_object_from_file()
    (whose existence was verified live on the test site,
    see research note below), to avoid a hard dependency on
    internal cmk.* module paths that may move between
    Checkmk versions - functionally equivalent
    (atomic write + simple read of a JSON file below
    var/check_mk/web/, the GUI's own data storage).
  - If no cache exists yet when the page is opened, the analysis is
    run automatically once (no more empty state).
  - The button is now called "Re-run analysis" (instead of "Start analysis
    now"): more fitting, because from now on the page ALWAYS shows a (possibly
    cached) result and the button is only needed for
    explicit recomputation.

  Evidence (since 0.9.0-b14, see _analyze_host()):
  - Directly from the agent output (get-agent-output @cached): sections
    whose name maps to a token and which deliver real data
    (T_SECTION), the plug-in list checkmk_agent_plugins_lnx/_win
    (T_PLUGIN) and running services/processes from systemd_units, ps_lnx/
    ps and Windows services via detect rules of the JSON (T_RUNTIME).
  - The HW/SW inventory (installed packages) is only info now, no longer
    evidence - the earlier package matches had to be filtered out again
    afterwards via requires_evidence.

Scope (still deliberately reduced):
  - Only a manual "Start analysis now" button, no scheduler, no
    notifications.
  - The analysis still runs SYNCHRONOUSLY (no cmk.gui.background_job):
    "cmk -L" is executed via subprocess EXACTLY ONCE per analysis run
    and reused for all hosts (see _available_plugin_map()
    with a simple time-based cache, analogous to available_plugins() in the
    reference special agent script). For production use with very many hosts /
    more complex logic, it should nevertheless be switched to
    cmk.gui.background_job.

Expansion stage 2.0.0 (piggyback extension, see
PLAN_piggyback_background_job.md - all decisions there are final):
  - New functions _build_piggyback_payload()/_write_piggyback_data()/
    _run_piggyback_full()/_run_piggyback_refresh(): create a JSON payload
    per host (findings/coverage/both timestamps) and write it
    via cmk.piggyback.backend.store_piggyback_raw_data() (official API,
    NO raw file system writes) under the piggyback source
    "monitoring_coverage_analyzer" for each host.
  - Extended cache file (_CACHE_FILE_REL): now additionally carries
    "last_full_run_timestamp" (real full run: new Livestatus query +
    rule evaluation) and "last_piggyback_refresh_timestamp" (last
    refresh tick: same content, only a new message_timestamp on the
    repeated store_piggyback_raw_data() call). Both timestamps are
    shown by the check plugin (see cmk_addons_plugins/monitoring_coverage_
    analyzer/agent_based/monitoring_coverage.py) as TWO SEPARATE
    lines in the service output (transparency principle, plan
    decision 11) - "Content last computed" vs. "Piggyback transfer
    last refreshed".
  - Two new GET parameters for the cron trigger:
      ?_cron_fullrun=1  -> full run (only if piggyback_interval_hours
                           has expired, unless _force=1 is set)
                           + full piggyback update (new content,
                           both timestamps renewed).
      ?_cron_refresh=1  -> ONLY refresh tick: write the last cached content
                           again with a new message_timestamp,
                           NO Livestatus query. Only the second
                           timestamp changes.
    Invocation path: both endpoints are triggered PRAGMATICALLY via a direct
    Python call in the site context (cmk.gui.utils.
    script_helpers.application_and_request_context(), see
    local/bin/mcactl - analogous to
    Checkmk's own CLI scripts such as cmk-update-config), NOT via an
    authenticated HTTP request: for a pure cron trigger this is
    simpler/more robust than an automation-user-secret solution
    (no network round trip, no secret handling needed) and was explicitly
    allowed by the plan as the preferred alternative ("if a
    simpler, cleaner way exists ... prefer that"). The GET
    parameter mechanism in this file remains nevertheless (also
    usable via a real HTTP request, e.g. for manual tests via curl
    with a GUI session cookie), only the bundled mcactl uses the
    direct Python path.
  - The "Re-run analysis" button still immediately triggers a real full run
    AND afterwards (if generate_piggyback_data is enabled) immediately
    also kicks off the piggyback full write (new content for both
    timestamps, see PageMonitoringCoverageAnalyzer._show_results()).
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any, NamedTuple, override

import cmk.gui.sites as sites
from cmk.gui.breadcrumb import (
    Breadcrumb,
    make_current_page_breadcrumb_item,
    make_topic_breadcrumb,
)
from cmk.gui.htmllib.header import make_header
from cmk.gui.htmllib.html import html
from cmk.gui.http import request
from cmk.gui.i18n import _
from cmk.gui.main_menu import main_menu_registry
from cmk.gui.pages import Page, PageContext, PageEndpoint, PageResult, page_registry
from cmk.gui.utils.html import HTML
from cmk.gui.utils.urls import makeuri_contextless

# 0.9.0-b21: shared evaluation (ignore rules, status, coverage, texts)
# with the check plugin - both apply the setup rule the same way.
from cmk_addons.plugins.monitoring_coverage_analyzer.lib import evaluate as _ev
from cmk_addons.plugins.monitoring_coverage_analyzer.lib import runstate as _rs

# Piggyback write API (cmk.piggyback.backend): see lib/runstate.py

PAGE_TITLE = _("Analyze monitoring coverage")

# Expansion stage 2.0.0: constants for the piggyback extension (see
# PLAN_piggyback_background_job.md, decisions 4-7).
PIGGYBACK_SOURCE_HOSTNAME = "monitoring_coverage_analyzer"
PIGGYBACK_SECTION_NAME = "checkmk_monitoring_coverage"
PIGGYBACK_SERVICE_TITLE = "Checkmk Monitoring Coverage"
# Hard-wired refresh tick interval (plan decision 7, NOT configurable) -
# not enforced by _run_piggyback_refresh() itself (the cron schedule does
# that every 5 minutes), but documented here as a constant so it can be
# looked up in one place.
PIGGYBACK_REFRESH_INTERVAL_SECONDS = 5 * 60

# 0.9.0-b15: spinner for the "Re-run analysis" button (pure CSS, no
# image file - color follows the theme via currentColor).
_RERUN_SPINNER_CSS = """<style>
#mca_running { margin-left: 10px; vertical-align: middle; }
.mca_spinner {
  display: inline-block; width: 14px; height: 14px; margin-right: 6px;
  vertical-align: middle; border: 2px solid currentColor;
  border-right-color: transparent; border-radius: 50%;
  animation: mca_spin 0.8s linear infinite;
}
@keyframes mca_spin { to { transform: rotate(360deg); } }
</style>"""

_RERUN_ONSUBMIT_JS = (
    "var b=this.querySelector('input[name=_analyze]');"
    "if(b){if(b.disabled){return false;}"
    "var h=document.createElement('input');h.type='hidden';h.name='_analyze';"
    "h.value=b.value;this.appendChild(h);b.disabled=true;}"
    "var r=document.getElementById('mca_running');if(r){r.style.display='inline';}"
    "return true;"
)


# ---------------------------------------------------------------------------
# Coverage knowledge base (step 1.4.0: purely data-driven): the rules
# (alias/title/hint tables, stop tokens) live EXCLUSIVELY in the file
# monitoring_coverage_analyzer_rules.json in the same directory -
# there is deliberately NO hard-coded copy/fallback in the code anymore. This
# makes it unambiguous for every analysis result: it ALWAYS comes from this one
# JSON file, never from a silent code alternative. Extensions/
# corrections are thus possible WITHOUT code change/redeploy (edit the file on
# the site, the next analysis run re-reads it automatically,
# see _load_rules()/_reload_rules()).
#
# Consequence: if the file is missing, is broken JSON, or its content is
# structurally unusable (empty aliases/titles), the analysis run fails
# with a clear error message (see _query_and_analyze_hosts()) -
# it does NOT silently continue with an incomplete/wrong
# knowledge base. Otherwise it would later be unclear whether a
# result came from the real rules file or from a code fallback.
# ---------------------------------------------------------------------------

_RULES_FILE_NAME = "monitoring_coverage_analyzer_rules.json"


class _Rules(NamedTuple):
    aliases: dict[str, str]
    titles: dict[str, str]
    hints: dict[str, str]
    stop_tokens: frozenset[str]
    # 0.9.0-b14: positive detection rules instead of a retroactive
    # requires_evidence filter, see _match_condition().
    detect: dict[str, dict[str, list[str]]]
    section_data: dict[str, str]
    section_ignore: frozenset[str]
    no_data_lines: list[str]
    # 0.9.0-b21: plug-in families that the generic matching never
    # suggests (too generic names like "local", "win", "job").
    generic_ignore_families: frozenset[str] = frozenset()
    # 0.9.0-b26: sections for which "header present, no data" is a valid
    # state (e.g. windows_tasks without matching tasks) - see
    # _analyze_host().
    empty_ok: frozenset[str] = frozenset()


class RulesLoadError(RuntimeError):
    """The coverage rules file is missing or unusable.

    Deliberately NOT caught and silently replaced by a
    code-fallback knowledge base: rule detection is purely
    data-driven (see module docstring above), an analysis result must
    never be able to come from an unclear source. Callers (see
    _query_and_analyze_hosts()) catch this error and show it clearly to the
    user, instead of continuing the run with wrong/missing rules.
    """


# Capability types (modeled on T_* in the reference check).
T_LABEL = "host_label"
# 0.9.0-b14: installed packages (HW/SW inventory) are NO longer evidence -
# on their own they do not produce a finding, but only appear
# as an info line under "Sources" (see _analyze_host()).
T_INV_PACKAGE = "inv_package"
# 0.9.0-b14: direct evidence - a section of the agent output (get-agent-
# output @cached) whose name maps to the token delivers real data
# (or a "direct" rule from detect matches). Previously T_SECTION came from
# the plug-in list in the HW/SW inventory.
T_SECTION = "agent_section"
# 0.9.0-b14: deployed agent plug-in / local check according to section
# checkmk_agent_plugins_lnx/_win of the agent output.
T_PLUGIN = "agent_plugin"
# 0.9.0-b14: indirect evidence - service/process is running (systemd_units,
# ps_lnx/ps, Windows services), rules under detect.<token>.runtime.
T_RUNTIME = "runtime"
# T_CHECK (0.9.0-b6): already monitored check_command as its own
# capability evidence - see docstring in _analyze_host().
T_CHECK = "check_command"


def _rules_path() -> str:
    """Path to monitoring_coverage_analyzer_rules.json.

    IMPORTANT (since 0.9.0-b3): on some sites/Checkmk versions the
    legacy plugin loader (load_web_plugins) merges all page plugins into a
    synthetic file under lib/python3/cmk/gui/utils/ before it is
    executed. In that case __file__ NO longer points to the
    actual installation directory share/check_mk/web/plugins/pages/,
    but to this synthetic intermediate path - the JSON file never lives
    there, see the user's bug report ("...lib/python3/cmk/gui/utils/
    monitoring_coverage_analyzer_rules.json ... could not be
    read").

    Therefore: first look next to __file__ (works on most
    sites), and if the file does not exist there, fall back to the
    OMD_ROOT-relative installation path where the MKP
    actually places the file (share/check_mk/web/plugins/pages/).
    """
    candidate = os.path.join(os.path.dirname(os.path.abspath(__file__)), _RULES_FILE_NAME)
    if os.path.isfile(candidate):
        return candidate
    omd_root = os.environ.get("OMD_ROOT", "")
    if omd_root:
        # IMPORTANT: MKP contents end up under local/share/... (not directly
        # under share/...) - share/check_mk/... is the path for
        # Checkmk built-ins, locally installed extensions always live
        # under local/.
        fallback = os.path.join(
            omd_root,
            "local",
            "share",
            "check_mk",
            "web",
            "plugins",
            "pages",
            _RULES_FILE_NAME,
        )
        if os.path.isfile(fallback):
            return fallback
    return candidate


def _load_rules() -> _Rules:
    """Loads the coverage rules EXCLUSIVELY from
    monitoring_coverage_analyzer_rules.json - purely data-driven, without
    code fallback (see RulesLoadError docstring). Every error case
    (file missing, invalid JSON, empty/broken structure) leads to a
    RulesLoadError with a message understandable to the user, instead of
    silently falling back to an alternative stored in the code. No
    time-based cache needed: the file is read only once per analysis run
    (see caller in _query_and_analyze_hosts()).
    """
    path = _rules_path()
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise RulesLoadError(
            f"Rules file {path!r} could not be read ({exc}). "
            "The file must be located next to this Python module."
        ) from exc
    except ValueError as exc:
        raise RulesLoadError(
            f"Rules file {path!r} does not contain valid JSON ({exc})."
        ) from exc

    if not isinstance(data, dict):
        raise RulesLoadError(
            f"Rules file {path!r} must be a JSON object (aliases/titles/"
            "hints/stop_tokens), found: {type(data).__name__}."
        )

    aliases = dict(data.get("aliases") or {})
    titles = dict(data.get("titles") or {})
    hints = dict(data.get("hints") or {})
    stop_tokens = frozenset(data.get("stop_tokens") or [])
    detect: dict[str, dict[str, list[str]]] = {}
    raw_detect = data.get("detect") or {}
    if isinstance(raw_detect, dict):
        for token, spec in raw_detect.items():
            if not isinstance(spec, dict):
                continue
            detect[token] = {
                kind: [str(item) for item in spec.get(kind) or [] if isinstance(item, str)]
                for kind in ("direct", "runtime", "not_on_os", "monitored_elsewhere")
            }
    section_data = {
        str(k): str(v) for k, v in (data.get("section_data") or {}).items()
    } if isinstance(data.get("section_data"), dict) else {}
    section_ignore = frozenset(str(x) for x in data.get("section_ignore") or [])
    no_data_lines = [str(x) for x in data.get("no_data_lines") or []]
    generic_ignore_families = frozenset(
        str(x).lower() for x in data.get("generic_ignore_families") or []
    )
    empty_ok = frozenset(str(x) for x in data.get("empty_ok") or [])
    for pattern in [*section_data.values(), *no_data_lines]:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise RulesLoadError(
                f"Rules file {path!r}: invalid regular expression {pattern!r} ({exc})."
            ) from exc
    if not aliases or not titles:
        raise RulesLoadError(
            f"Rules file {path!r} is structurally unusable: 'aliases' "
            "and/or 'titles' are empty or missing."
        )
    return _Rules(
        aliases, titles, hints, stop_tokens, detect, section_data, section_ignore, no_data_lines,
        generic_ignore_families, empty_ok,
    )


# Module-global knowledge base, filled via _reload_rules() - see
# module docstring above: purely data-driven, no code fallback. Stays empty
# until the first successful _reload_rules() call; _query_and_analyze_
# hosts() calls _reload_rules() at the start of each run and aborts on
# RulesLoadError with a clear error message, instead of continuing with
# empty/old rules.
ALIASES: dict[str, str] = {}
TITLES: dict[str, str] = {}
HINTS: dict[str, str] = {}
STOP_TOKENS: frozenset[str] = frozenset()
DETECT: dict[str, dict[str, list[str]]] = {}
SECTION_DATA: dict[str, str] = {}
SECTION_IGNORE: frozenset[str] = frozenset()
NO_DATA_LINES: list[str] = []
GENERIC_IGNORE_FAMILIES: frozenset[str] = frozenset()
EMPTY_OK: frozenset[str] = frozenset()


def _reload_rules() -> None:
    """Re-reads monitoring_coverage_analyzer_rules.json and updates
    the module-global rule tables (ALIASES/TITLES/HINTS/STOP_TOKENS/
    DETECT/SECTION_DATA/SECTION_IGNORE/NO_DATA_LINES)
    in place (dict.clear() + update(), so that already bound references -
    e.g. closures - keep pointing to the same objects)."""
    rules = _load_rules()
    ALIASES.clear()
    ALIASES.update(rules.aliases)
    TITLES.clear()
    TITLES.update(rules.titles)
    HINTS.clear()
    HINTS.update(rules.hints)
    global STOP_TOKENS, SECTION_IGNORE, GENERIC_IGNORE_FAMILIES, EMPTY_OK
    STOP_TOKENS = rules.stop_tokens
    EMPTY_OK = rules.empty_ok
    GENERIC_IGNORE_FAMILIES = rules.generic_ignore_families
    DETECT.clear()
    DETECT.update(rules.detect)
    SECTION_DATA.clear()
    SECTION_DATA.update(rules.section_data)
    SECTION_IGNORE = rules.section_ignore
    NO_DATA_LINES[:] = rules.no_data_lines


def _rules_source_status() -> str:
    """Human-readable provenance info about the currently loaded
    findings/hint rules - directly answers the question "where does
    this result come from": modification time of the JSON file, and the
    number of loaded entries per table. There is no code fallback
    source anymore (see module docstring), hence always the same file.
    """
    path = _rules_path()
    try:
        mtime = os.path.getmtime(path)
        mtime_txt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))
    except OSError:
        mtime_txt = "?"
    return (
        f"{mtime_txt}; "
        f"{len(ALIASES)} aliases, {len(TITLES)} titles, "
        f"{len(HINTS)} hints, {len(STOP_TOKENS)} stop-tokens, "
        f"{len(DETECT)} detect rules loaded"
    )


try:
    _reload_rules()
except RulesLoadError:
    # On module import (e.g. on the first WATO page build, before any analysis
    # run has even been started) a problem with the rules file must NOT crash
    # the whole page build/menu registration. ALIASES/TITLES/HINTS/STOP_TOKENS
    # then stay empty; the actual analysis run calls _reload_rules() again
    # (see _query_and_analyze_hosts()) and shows the error clearly to the user
    # there, instead of breaking a page with a stack trace here.
    pass

# Cache for "cmk -L" (available_plugins) - ONCE per analysis run, not per
# host: analogous to available_plugins() in the reference special agent script
# libexec/agent_monitoring_coverage.
_AVAILABLE_PLUGINS_CACHE_TTL = 60.0
_available_plugins_cache: dict[str, tuple[float, Any]] = {}

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_SAFE_HOST_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def _canonical_token(raw: str) -> str:
    """Normalizes a raw plug-in/process/package name to a token

    Step 3 fix: agent plug-in file names such as "mk_inventory" or
    "mk_apt.py" contain underscores, which the original
    _TOKEN_RE ([a-z0-9]+) treated as separators - as a result
    "mk_inventory" was wrongly shortened to "mk" (no ALIASES hit,
    T_SECTION for "inventory" was lost). Fix: first strip a known
    file extension (.py/.sh/.exe) and check the COMPLETE (dot/
    whitespace-cleaned) name against ALIASES, before falling back to
    the "first alphanumeric word" fallback (which still yields the
    desired behavior for process names such as "mariadb 10.6.1" or
    package names such as "cups-browsed").
    """
    raw = raw.strip().lower()
    for suffix in (".py", ".sh", ".exe", ".pl", ".ps1", ".vbs", ".bat", ".cmd"):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)]
            break
    if raw in ALIASES:
        return ALIASES[raw]
    match = _TOKEN_RE.match(raw)
    token = match.group(0) if match else raw
    return ALIASES.get(token, token)


def _cmk_list_plugins() -> list[tuple[str, str, str]]:
    """'cmk -L' -> [(plugin, type, title)], type e.g. "agent", "snmp",
    "active". Same TTL cache as _available_plugin_map()."""
    now = time.time()
    cached = _available_plugins_cache.get("rows")
    if cached is not None and (now - cached[0]) < _AVAILABLE_PLUGINS_CACHE_TTL:
        return cached[1]
    rows: list[tuple[str, str, str]] = []
    try:
        proc = subprocess.run(
            ["cmk", "-L"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        for line in proc.stdout.splitlines():
            parts = line.strip().split(None, 2)
            if not parts:
                continue
            rows.append((parts[0], parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else ""))
    except Exception:  # pragma: no cover - defensive, GUI context
        rows = []
    _available_plugins_cache["rows"] = (now, rows)
    return rows


def _available_plugin_map() -> dict[str, list[str]]:
    """Returns token -> sorted list of the check plugin names available on
    the site via 'cmk -L' (canonicalized via ALIASES), with a simple
    time-based cache across the process lifecycle.

    'cmk -L' is called EXACTLY ONCE per analysis run (or until the TTL
    expires), regardless of the number of hosts to analyze -
    performance requirement from the task specification.
    """
    now = time.time()
    cached = _available_plugins_cache.get("map")
    if cached is not None and (now - cached[0]) < _AVAILABLE_PLUGINS_CACHE_TTL:
        return cached[1]

    plugin_map: dict[str, set[str]] = {}
    for first_word, _ptype, _title in _cmk_list_plugins():
        token = _canonical_token(first_word)
        if token and token not in STOP_TOKENS:
            plugin_map.setdefault(token, set()).add(first_word)

    result = {token: sorted(names) for token, names in plugin_map.items()}
    # Step 3: the active check "cmk_inv" (Checkmk HW/SW Inventory) is
    # available on EVERY Checkmk site, but does not show up in "cmk -L"
    # (which only lists agent_based check plugins, no active
    # checks) - so it must be seeded as available here, otherwise
    # "inventory" could never be detected as a monitorable subsystem.
    result.setdefault("inventory", ["cmk_inv"])
    _available_plugins_cache["map"] = (now, result)
    return result


def _inventory_package_names(host_name: str) -> list[str]:
    """Reads the installed packages of this host stored in the site's
    HW/SW inventory. Since 0.9.0-b14 informational only (see
    T_INV_PACKAGE), not evidence for a finding.

    Source: var/check_mk/inventory/<host>.json (Checkmk HW/SW inventory
    tree), path Nodes.software.Nodes.packages.Table.Rows[].name.
    Deliberately defensive: if the file / the inventory for this host is
    missing, an empty list is simply returned (no error in the GUI).
    """
    if not _SAFE_HOST_RE.match(host_name):
        return []
    omd_root = os.environ.get("OMD_ROOT", "")
    if not omd_root:
        return []
    path = os.path.join(omd_root, "var", "check_mk", "inventory", f"{host_name}.json")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []

    try:
        packages_node = data["Nodes"]["software"]["Nodes"]["packages"]
        rows = packages_node.get("Table", {}).get("Rows", [])
    except (KeyError, TypeError, AttributeError):
        return []

    names: list[str] = []
    for row in rows:
        name = row.get("name") if isinstance(row, dict) else None
        if isinstance(name, str) and name:
            names.append(name)
    return names


_RAW_CACHE_SECTION_RE = re.compile(r"^<<<([A-Za-z0-9_.-]+)(?::[^>]*)?>>>\s*$")
_PIGGYBACK_MARKER_RE = re.compile(r"^<<<<(.*)>>>>\s*$")

# 0.9.0-b7: The data source for the agent sections is the (site-patched)
# automation "get-agent-output <HOST> agent @cached" via the running
# automation-helper (cmk-automation-client). "@cached" sets
# FileCacheOptions(use_outdated=True) -> the agent sources read the cache
# files the core writes anyway, without an age limit, instead of querying
# the agent again (verified live on the test site: mtime of the cache file
# stays unchanged, ~0.2-0.5 s per host). Only if NO cache file at all exists
# yet for a source does the core fetch live (Checkmk's own behavior of
# use_outdated, not controllable by us).
# Compared to reading tmp/check_mk/cache/<host> directly, the automation
# additionally returns special agent and piggyback data of the host.
# 0.9.0-b8: "@cached" is upstream as of Checkmk 2.5.0p15 (minimum version in
# the MKP). No more fallback to the cache file - older sites without the
# directive are excluded via version.min_required.
_AGENT_OUTPUT_WORKERS = 4
_AGENT_OUTPUT_TIMEOUT = 60
SRC_AUTOMATION = "get-agent-output @cached"
SRC_REMOTE_AUTOMATION = "remote get-agent-output @cached"


class _AgentSections(NamedTuple):
    sections: dict[str, list[str]]
    source: str
    error: str | None
    # 0.9.0-b26: Sections in piggyback blocks for OTHER hosts:
    # section_name -> target hosts. Not evidence for this host, but proof
    # that a plug-in deployed here delivers data (e.g.
    # oxidized: only piggyback data for the backed-up devices).
    piggyback: Mapping[str, frozenset[str]] = {}
    # 0.9.0-b27: Remote site unreachable/not logged in - the analysis
    # of the host is incomplete (not just "no agent data").
    site_error: bool = False


def _parse_piggyback_sections(raw_text: str) -> dict[str, frozenset[str]]:
    """Sections in piggyback blocks for OTHER hosts, with the target hosts
    per section (only sections with at least one data line)."""
    found: dict[str, set[str]] = {}
    target: str | None = None
    current: str | None = None
    for line in raw_text.splitlines():
        pb_match = _PIGGYBACK_MARKER_RE.match(line)
        if pb_match:
            target = pb_match.group(1).strip() or None
            current = None
            continue
        if target is None:
            continue
        match = _RAW_CACHE_SECTION_RE.match(line)
        if match:
            current = match.group(1)
            continue
        if current is not None and line.strip():
            found.setdefault(current, set()).add(target)
    return {name: frozenset(hosts) for name, hosts in found.items()}


def _parse_agent_sections(raw_text: str) -> dict[str, list[str]]:
    """Splits raw agent output into section_name -> list of lines (WITHOUT
    the <<<...>>> marker lines). Piggyback blocks for OTHER hosts
    (<<<<foreign_host>>>> ... <<<<>>>>, e.g. from Proxmox/HA special
    agents) are skipped - their sections do not belong to this
    host and must not provide evidence for it."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    in_foreign_piggyback = False
    for line in raw_text.splitlines():
        pb_match = _PIGGYBACK_MARKER_RE.match(line)
        if pb_match:
            in_foreign_piggyback = bool(pb_match.group(1).strip())
            current = None
            continue
        if in_foreign_piggyback:
            continue
        match = _RAW_CACHE_SECTION_RE.match(line)
        if match:
            current = match.group(1)
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)
    return sections


def _automation_agent_sections(host_name: str) -> _AgentSections:
    """Fetches the agent output via
    'cmk-automation-client get-agent-output <HOST> agent @cached'.

    Response format (verified live): JSON with
    "serialized_result_or_error_code" = repr() of the tuple
    (success: bool, details: str, raw_agent_data: bytes) - hence
    ast.literal_eval() (literals only, no code execution)."""
    omd_root = os.environ.get("OMD_ROOT", "")
    cli = os.path.join(omd_root, "bin", "cmk-automation-client")
    python = os.path.join(omd_root, "bin", "python3")
    try:
        proc = subprocess.run(
            [python, cli, "get-agent-output", host_name, "agent", "@cached"],
            capture_output=True,
            text=True,
            timeout=_AGENT_OUTPUT_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _AgentSections({}, SRC_AUTOMATION, f"timeout after {_AGENT_OUTPUT_TIMEOUT}s")
    except OSError as exc:
        return _AgentSections({}, SRC_AUTOMATION, f"cannot run cmk-automation-client: {exc}")
    try:
        payload = json.loads(proc.stdout)
        serialized = payload["serialized_result_or_error_code"]
        if not isinstance(serialized, str):
            return _AgentSections({}, SRC_AUTOMATION, f"automation error code {serialized!r}")
        success, details, raw = ast.literal_eval(serialized)
    except Exception as exc:  # pragma: no cover - defensive, GUI context
        detail = (proc.stderr or proc.stdout or "").strip()[:300]
        return _AgentSections({}, SRC_AUTOMATION, f"unparsable response ({exc!r}): {detail}")

    return _sections_from_result((success, details, raw), SRC_AUTOMATION)


def _sections_from_result(result: object, source: str) -> _AgentSections:
    """(success, details, raw_agent_data) -> _AgentSections."""
    try:
        success, details, raw = result  # type: ignore[misc]
    except (TypeError, ValueError):
        return _AgentSections({}, source, f"unexpected response: {str(result)[:200]}")
    raw_text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    error = None if success else (str(details).strip() or "fetch failed")
    return _AgentSections(
        _parse_agent_sections(raw_text), source, error,
        _parse_piggyback_sections(raw_text),
    )


# 0.9.0-b27: Distributed monitoring. Hosts of a remote site have no agent
# cache on the central site - the central site therefore fetches the agent
# output via remote automation from the responsible site (like Checkmk's own
# "Download agent output"), also with "@cached" (the remote site needs
# 2.5.0p15+, otherwise it queries the agent live). do_remote_automation is
# an internal GUI function: signature changes lead to an
# error message per host, not to an abort.
def _remote_automation_config(site_id: str) -> tuple[object | None, str | None]:
    """(RemoteAutomationConfig, None) or (None, error text)."""
    try:
        from cmk.gui.config import active_config
        from cmk.gui.watolib.automations import remote_automation_config_from_site_config

        site_config = active_config.sites.get(site_id)
        if site_config is None:
            return None, f"site {site_id!r} is not configured"
        return remote_automation_config_from_site_config(site_config), None
    except Exception as exc:  # pragma: no cover - GUI internal
        return None, f"site {site_id!r}: {exc}"


def _remote_agent_sections(host_name: str, automation_config: object) -> _AgentSections:
    from cmk.gui.watolib.automations import do_remote_automation
    from cmk.gui.watolib.utils import mk_repr

    result = do_remote_automation(
        automation_config,  # type: ignore[arg-type]
        "checkmk-automation",
        [
            ("automation", "get-agent-output"),
            ("arguments", mk_repr([host_name, "agent", "@cached"]).decode("ascii")),
            ("indata", mk_repr("").decode("ascii")),
            ("stdin_data", mk_repr("").decode("ascii")),
            ("timeout", mk_repr(_AGENT_OUTPUT_TIMEOUT).decode("ascii")),
        ],
        debug=False,
        timeout=_AGENT_OUTPUT_TIMEOUT,
    )
    return _sections_from_result(result, SRC_REMOTE_AUTOMATION)


def _is_local_site(site_id: str | None) -> bool:
    if not site_id:
        return True
    try:
        from cmk.gui.config import active_config
        from cmk.gui.site_config import site_is_local

        site_config = active_config.sites.get(site_id)
        return site_config is None or site_is_local(site_config)
    except Exception:  # pragma: no cover - GUI internal
        return True


def _agent_sections(host_name: str) -> _AgentSections:
    if not _SAFE_HOST_RE.match(host_name):
        return _AgentSections({}, "", "unsafe host name")
    return _automation_agent_sections(host_name)


def _collect_agent_sections(
    host_names: Sequence[str], host_sites: Mapping[str, str] | None = None
) -> dict[str, _AgentSections]:
    """Agent sections for all hosts of an analysis run, in parallel
    (measured live on the test site, 46 hosts: serial 23 s, 4 workers 8 s,
    8 workers no further gain). Hosts on remote sites via
    remote automation; if a site is unreachable, the remaining hosts of
    that site get the same error without another attempt."""
    host_sites = host_sites or {}
    remote_configs: dict[str, tuple[object | None, str | None]] = {}
    site_errors: dict[str, str] = {}

    for site_id in {host_sites.get(h) for h in host_names}:
        if site_id and not _is_local_site(site_id):
            remote_configs[site_id] = _remote_automation_config(site_id)

    def _fetch(host_name: str) -> _AgentSections:
        site_id = host_sites.get(host_name)
        if site_id not in remote_configs:
            return _agent_sections(host_name)
        if not _SAFE_HOST_RE.match(host_name):
            return _AgentSections({}, "", "unsafe host name")
        automation_config, config_error = remote_configs[site_id]
        if automation_config is None:
            return _AgentSections({}, SRC_REMOTE_AUTOMATION, config_error, site_error=True)
        if site_id in site_errors:
            return _AgentSections({}, SRC_REMOTE_AUTOMATION, site_errors[site_id], site_error=True)
        try:
            return _remote_agent_sections(host_name, automation_config)
        except Exception as exc:
            # Connection/auth/HTTP errors affect the whole site
            # (host errors come back as success=False).
            message = f"site {site_id!r}: {exc}"
            site_errors.setdefault(site_id, message)
            return _AgentSections({}, SRC_REMOTE_AUTOMATION, message, site_error=True)

    with ThreadPoolExecutor(max_workers=_AGENT_OUTPUT_WORKERS) as executor:
        return dict(zip(host_names, executor.map(_fetch, host_names)))


# ---------------------------------------------------------------------------
# 0.9.0-b14: Evidence directly from the agent output (get-agent-output @cached).
# Replaces the plug-in list from the HW/SW inventory (old T_SECTION) and the
# subsequent requires_evidence filter for inventory packages.
# ---------------------------------------------------------------------------

# Subsection header within a section, e.g. "[df]", "[all]",
# "[processes]" - never counts as a data line.
_SUBSECTION_RE = re.compile(r"^\[[^\]]*\]$")

# Processes in containers/LXC do not belong to this host - an agent
# plug-in on the host could not monitor them either (seen on the test site:
# MariaDB in an LXC container, nginx/redis in Docker).
_CONTAINER_CGROUP_RE = re.compile(r"/lxc/|/docker[-/]|/libpod-|/machine\.slice/|/kubepods")

_PLUGIN_SECTIONS = ("checkmk_agent_plugins_lnx", "checkmk_agent_plugins_win")
# '<path>:CMK_VERSION="..."' or '<path>:__version__ = "..."' (Python
# plug-ins); Windows paths themselves contain a ':' ("C:\\...").
_PLUGIN_LINE_RE = re.compile(r"^(.*?):\s*(?:CMK_VERSION|__version__)\b")


def _subsection_lines(lines: Sequence[str], name: str) -> list[str]:
    """Lines of a subsection ("[name]") of a section."""
    out: list[str] = []
    current: str | None = None
    for line in lines:
        stripped = line.strip()
        if _SUBSECTION_RE.match(stripped):
            current = stripped[1:-1]
            continue
        if current == name:
            out.append(line)
    return out


def _section_lines(sections: Mapping[str, list[str]], ref: str) -> list[str] | None:
    """'<section>' or '<section>/<subsection>' -> lines, None if
    the section is missing."""
    name, _sep, sub = ref.partition("/")
    lines = sections.get(name)
    if lines is None:
        return None
    return _subsection_lines(lines, sub) if sub else lines


def _data_lines(lines: Sequence[str]) -> list[str]:
    """Real data lines: not empty, no subsection header, no
    no_data_lines match (e.g. "no pools available")."""
    patterns = [re.compile(p) for p in NO_DATA_LINES]
    return [
        line for line in lines
        if line.strip()
        and not _SUBSECTION_RE.match(line.strip())
        and not any(p.search(line) for p in patterns)
    ]


def _section_has_data(name: str, lines: Sequence[str]) -> bool:
    """Does the section deliver real data? Default: at least one data line.
    section_data.<name> (rules file) tightens this to "at least one
    data line matches <regex>" - e.g. FreeBSD zfs_arc_cache, which only
    delivers zero values when ZFS is not used at all."""
    data = _data_lines(lines)
    pattern = SECTION_DATA.get(name)
    if pattern is None:
        return bool(data)
    regex = re.compile(pattern)
    return any(regex.search(line) for line in data)


def _plugin_token(file_name: str) -> str:
    """Plug-in file name -> token; 'mk_mysql' -> 'mysql' etc."""
    token = _canonical_token(file_name)
    if token in TITLES:
        return token
    lowered = file_name.strip().lower()
    for prefix in ("mk_", "mk-"):
        if lowered.startswith(prefix):
            return _canonical_token(lowered[len(prefix):])
    return token


def _agent_plugin_names(sections: Mapping[str, list[str]]) -> list[str]:
    """File names of the deployed agent plug-ins and local checks from
    checkmk_agent_plugins_lnx/_win (lines '<path>:CMK_VERSION=...')."""
    names: list[str] = []
    for section in _PLUGIN_SECTIONS:
        for line in sections.get(section, []):
            if line.startswith(("pluginsdir ", "localdir ")):
                continue
            match = _PLUGIN_LINE_RE.match(line)
            if not match:
                continue
            base = re.split(r"[\\/]", match.group(1))[-1].strip()
            if base:
                names.append(base)
    return names


class _Runtime(NamedTuple):
    systemd_running: list[str]
    processes: list[str]
    win_services_running: list[str]
    # 0.9.0-b21: service name -> display name (for the generic matching)
    win_service_titles: dict[str, str] = {}


def _runtime_facts(sections: Mapping[str, list[str]]) -> _Runtime:
    """Running services/processes of a host from the agent output.

    - systemd_units, subsection [all]: '<unit> loaded active running ...'
    - ps_lnx [processes]: '<cgroup> <user> <vsz> <rss> <time> <elapsed>
      <pid> <command>'; container/LXC processes are discarded.
    - ps (Windows/BSD/older agents): '(<user>,...)\t<command>'
    - services (Windows): '<name> <state>/<start_type> <display_name>'
    """
    units: list[str] = []
    for line in _subsection_lines(sections.get("systemd_units", []), "all"):
        parts = line.split()
        if len(parts) >= 4 and parts[2] == "active" and parts[3] == "running":
            units.append(parts[0])
    processes: list[str] = []
    for line in _subsection_lines(sections.get("ps_lnx", []), "processes"):
        parts = line.split(None, 7)
        if len(parts) < 8 or parts[0] == "[header]":
            continue
        if _CONTAINER_CGROUP_RE.search(parts[0]):
            continue
        command = parts[7].split()[0] if parts[7].split() else ""
        if command.startswith("["):  # kernel threads
            continue
        processes.append(re.split(r"[\\/]", command)[-1])
    for line in sections.get("ps", []):
        _head, _sep, command = line.partition("\t")
        if command:
            first = command.split()[0] if command.split() else ""
            processes.append(re.split(r"[\\/]", first)[-1])
    services: list[str] = []
    service_titles: dict[str, str] = {}
    for line in sections.get("services", []):
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[1].startswith("running"):
            services.append(parts[0])
            if len(parts) > 2:
                service_titles[parts[0]] = parts[2].strip()
    return _Runtime(units, processes, services, service_titles)


def _match_condition(
    condition: str, sections: Mapping[str, list[str]], runtime: _Runtime
) -> str | None:
    """Evaluates a detect condition; on a match returns a short evidence
    text, otherwise None. Formats (monitoring_coverage_analyzer_rules.json):

      section:<sec>[/<sub>]:data              - section delivers data
      section:<sec>[/<sub>]:contains:<regex>  - a data line matches
      systemd:<regex>     - running systemd unit ([all], active running)
      process:<regex>     - process name (without path), containers excluded
      winservice:<regex>  - running Windows service (service name)

    Regex always via re.search - anchors (^...$) belong in the rule.
    """
    kind, _sep, rest = condition.partition(":")
    try:
        if kind == "section":
            ref, _sep, rest2 = rest.partition(":")
            mode, _sep, pattern = rest2.partition(":")
            lines = _section_lines(sections, ref)
            if lines is None:
                return None
            if mode == "data":
                name = ref.partition("/")[0]
                return f"section '{ref}' has data" if _section_has_data(name, lines) else None
            if mode == "contains" and pattern:
                regex = re.compile(pattern)
                for line in _data_lines(lines):
                    if regex.search(line):
                        return f"section '{ref}': '{line.strip()[:60]}'"
            return None
        candidates = {
            "systemd": (runtime.systemd_running, "systemd unit '{}' running"),
            "process": (runtime.processes, "process '{}'"),
            "winservice": (runtime.win_services_running, "Windows service '{}' running"),
        }.get(kind)
        if candidates is None:
            return None
        regex = re.compile(rest)
        for item in candidates[0]:
            if regex.search(item):
                return candidates[1].format(item)
    except re.error:
        return None
    return None


# ---------------------------------------------------------------------------
# 0.9.0-b21: generic (fuzzy) matching - finds subsystems WITHOUT an
# entry in the rules file: leading name part of running systemd units,
# processes and Windows services against the families of the agent-based
# check plug-ins of this site ('cmk -L', type "agent"; SNMP plug-ins can
# never indicate a missing agent plug-in for an agent host).
# Filters against nonsense:
#   - families known to the rules file (TITLES/ALIASES) are decided
#     exclusively by the curated logic (e.g. ZFS needs real data).
#   - stop_tokens and generic_ignore_families of the rules file.
#   - family already monitored on the host (a check plug-in of the
#     family is running).
#   - family runs on almost all hosts of the same OS (base OS service),
#     see _GENERIC_COMMON_* in _query_and_analyze_hosts().
# Custom exceptions: Setup rule "Monitoring coverage analysis".
# ---------------------------------------------------------------------------
_GENERIC_SUFFIX_RE = re.compile(r"\.(service|socket|timer|scope|exe)$", re.IGNORECASE)
_GENERIC_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+")
_GENERIC_MIN_LEN = 3
# A family is suppressed if it occurs on >= 80 % of the hosts of the same OS
# - only if there are at least 5 hosts with this OS.
_GENERIC_COMMON_MIN_HOSTS = 5
_GENERIC_COMMON_RATIO = 0.8
_GENERIC_MAX_EVIDENCE = 3


def _plugin_family(plugin: str) -> str:
    """'mssql_counters.locks' -> 'mssql', 'zpool_status' -> 'zpool'."""
    head = re.split(r"[_.\-]", plugin.strip().lower())[0]
    return re.sub(r"\d+$", "", head)


def _generic_name_tokens(*names: str) -> list[str]:
    """Leading name part (plus first CamelCase word) per name:
    'postgresql@15-main.service' -> ['postgresql'],
    'MSSQL$SQLEXPRESS' -> ['mssql'], 'MSExchangeIS' -> ['msexchangeis', 'ms']."""
    out: list[str] = []
    for name in names:
        if not name:
            continue
        cleaned = _GENERIC_SUFFIX_RE.sub("", name.strip()).split("@")[0]
        parts = [part for part in re.split(r"[^A-Za-z0-9]+", cleaned) if part]
        if not parts:
            continue
        candidates = [parts[0]]
        camel = _GENERIC_CAMEL_RE.findall(parts[0])
        if camel:
            candidates.append(camel[0])
        for candidate in candidates:
            token = re.sub(r"\d+$", "", candidate.lower())
            if len(token) >= _GENERIC_MIN_LEN and token not in out:
                out.append(token)
    return out


def _generic_token_matches(token: str, family: str) -> bool:
    """Equal, or the family is a prefix with a short remainder ('postgresql' ->
    'postgres', 'mssqlserver' -> 'mssql'). Short families (3 characters) only
    exact."""
    if token == family:
        return True
    return len(family) >= 4 and token.startswith(family) and len(token) - len(family) <= 6


class _GenericFamily(NamedTuple):
    title: str
    plugins: list[str]
    # 0.9.0-b37: matching data sources for the hint (agent plug-in files,
    # special agents as (name, title)); empty = nothing matching found.
    agent_plugins: tuple[str, ...] = ()
    special_agents: tuple[tuple[str, str], ...] = ()


# Directories of the deployable agent plug-ins (Linux/Unix and Windows),
# each for the site version and the local hierarchy (MKPs).
_AGENT_PLUGIN_DIRS = (
    "share/check_mk/agents/plugins",
    "share/check_mk/agents/windows/plugins",
    "local/share/check_mk/agents/plugins",
    "local/share/check_mk/agents/windows/plugins",
)
# Special agents: executable file libexec/agent_<name> per plug-in package.
# As of 2.5, some of the plug-in packages (e.g. vsphere, proxmox_ve,
# pure_storage_fa) are located under lib/python3.<x>/site-packages/cmk/plugins.
_SPECIAL_AGENT_GLOBS = (
    "lib/python3/cmk/plugins/*/libexec/agent_*",
    "lib/python3.*/site-packages/cmk/plugins/*/libexec/agent_*",
    "local/lib/python3/cmk_addons/plugins/*/libexec/agent_*",
)


def _plugin_base(file_name: str) -> str:
    """'mk_mysql.py' -> 'mysql', 'smart_posix' -> 'smart_posix'."""
    base = file_name.strip().lower()
    base = re.sub(r"\.(py|sh|ps1|vbs|bat|cmd|exe|pl)$", "", base)
    for prefix in ("mk_", "mk-"):
        if base.startswith(prefix):
            base = base[len(prefix):]
    return base


def _family_matches(name: str, family: str) -> bool:
    return name == family or name.startswith((family + "_", family + "-"))


def _data_sources() -> tuple[list[str], list[tuple[str, str]]]:
    """(Agent plug-in files, [(special agent name, title)]) of the site,
    with the same TTL cache as 'cmk -L'. Title from the Setup rule
    (special_agents:<name>), otherwise the name."""
    now = time.time()
    cached = _available_plugins_cache.get("sources")
    if cached is not None and (now - cached[0]) < _AVAILABLE_PLUGINS_CACHE_TTL:
        return cached[1]
    omd_root = os.environ.get("OMD_ROOT", "")
    files: set[str] = set()
    specials: dict[str, str] = {}
    if omd_root:
        for rel in _AGENT_PLUGIN_DIRS:
            try:
                files.update(
                    e.name for e in os.scandir(os.path.join(omd_root, rel)) if e.is_file()
                )
            except OSError:
                continue
        import glob

        for pattern in _SPECIAL_AGENT_GLOBS:
            for path in glob.glob(os.path.join(omd_root, pattern)):
                specials.setdefault(os.path.basename(path)[len("agent_"):], "")
    try:
        from cmk.gui.watolib.rulespecs import rulespec_registry

        for name in specials:
            try:
                specials[name] = str(rulespec_registry["special_agents:" + name].title or "")
            except Exception:  # noqa: BLE001 - title is display only
                pass
    except Exception:  # noqa: BLE001
        pass
    result = (sorted(files), sorted((n, t or n) for n, t in specials.items()))
    _available_plugins_cache["sources"] = (now, result)
    return result


def _sources_for_family(
    family: str, plugins: Sequence[str], sources: tuple[list[str], list[tuple[str, str]]]
) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    """Agent plug-ins and special agents whose name matches the family
    (equal or '<family>_...'). Deprecated special agents only if there
    are no others."""
    files, specials = sources
    names = {family} | {p.lower() for p in plugins}
    agent = tuple(f for f in files if _family_matches(_plugin_base(f), family))
    special = [(n, t) for n, t in specials if _family_matches(n, family) or n in names]
    current = [(n, t) for n, t in special if "deprecated" not in t.lower()]
    return agent, tuple(current or special)


def _generic_catalog() -> dict[str, _GenericFamily]:
    """Family -> (title, plug-ins) of all agent-based check plug-ins
    NOT covered by the rules file. Title derived from the catalog
    title ('ACME SBC: Health' -> 'ACME SBC')."""
    grouped: dict[str, list[tuple[str, str]]] = {}
    for name, ptype, title in _cmk_list_plugins():
        if ptype != "agent":
            continue
        family = _plugin_family(name)
        if (
            len(family) < _GENERIC_MIN_LEN
            or family in STOP_TOKENS
            or family in GENERIC_IGNORE_FAMILIES
            or _canonical_token(family) in TITLES
            or _canonical_token(name) in TITLES
        ):
            continue
        grouped.setdefault(family, []).append((name, title))
    catalog: dict[str, _GenericFamily] = {}
    sources = _data_sources()
    for family, entries in grouped.items():
        plugins = sorted(n for n, _t in entries)
        agent, special = _sources_for_family(family, plugins, sources)
        catalog[family] = _GenericFamily(
            _generic_title(family, [t for _n, t in entries]), plugins, agent, special
        )
    return catalog


def _generic_sources(entry: _GenericFamily) -> str:
    """0.9.0-b39: matching data sources of a generic match for the
    detail line (agent plug-ins/special agents); the findings text itself
    stays short and only refers to the details."""
    parts = []
    if entry.agent_plugins:
        parts.append(_("agent plug-in(s) %s") % ", ".join(entry.agent_plugins[:4]))
    if entry.special_agents:
        parts.append(
            _("special agent(s) %s") % ", ".join(f"'{t}'" for _n, t in entry.special_agents[:3])
        )
    if parts:
        return "; ".join(parts)
    return _("no matching agent plug-in or special agent (data may come from the agent itself)")


def _generic_title(family: str, titles: Sequence[str]) -> str:
    """Title from the catalog titles: common leading words of the parts
    before ':' ('Couchbase Nodes: ...', 'Couchbase Buckets: ...' ->
    'Couchbase'); without ':' in the catalog title, the family name."""
    prefixes = [t.split(":")[0].split() for t in titles if ":" in t]
    if not prefixes:
        return family.title()
    common = prefixes[0]
    for words in prefixes[1:]:
        n = 0
        while n < min(len(common), len(words)) and common[n].lower() == words[n].lower():
            n += 1
        common = common[:n]
    if common:
        return " ".join(common)
    counts: dict[str, int] = {}
    for words in prefixes:
        counts[" ".join(words)] = counts.get(" ".join(words), 0) + 1
    return max(counts.items(), key=lambda kv: (kv[1], -len(kv[0])))[0]


# 0.9.0-b28: name parts that only name the host's operating system
# ("Windows Update", "Windows Event Log") are no evidence of an
# application - otherwise e.g. the plug-in family "windows" would match on
# every Windows host. Applies per host to its OS (labels cmk/os_type and
# cmk/os_family); vendor families remain unaffected.
_OS_NAME_TOKENS: Mapping[str, frozenset[str]] = {
    "windows": frozenset({"windows", "win", "microsoft"}),
    "linux": frozenset({"linux"}),
    "freebsd": frozenset({"freebsd", "bsd"}),
    "solaris": frozenset({"solaris", "sunos"}),
    "aix": frozenset({"aix"}),
}


# Host labels that describe the operating system; never evidence for an
# application (see _analyze_host).
_OS_LABELS = frozenset({
    "cmk/os_name", "cmk/os_platform", "cmk/os_type", "cmk/os_family", "cmk/os_version",
})


def _os_name_tokens(labels: Mapping[str, str]) -> frozenset[str]:
    """Name parts that denote the host's OS (from os_type/os_family)."""
    tokens: set[str] = set()
    for key in ("cmk/os_type", "cmk/os_family"):
        value = str(labels.get(key, "")).strip().lower()
        if value:
            tokens.add(value)
            tokens |= _OS_NAME_TOKENS.get(value, frozenset())
    return frozenset(tokens)


def _generic_candidates(
    runtime: _Runtime,
    check_commands: Sequence[str],
    catalog: Mapping[str, _GenericFamily],
    os_tokens: frozenset[str] = frozenset(),
) -> dict[str, list[str]]:
    """Family -> evidence for one host (without OS frequency filter).
    os_tokens: name parts of the host OS, do not count as evidence."""
    monitored_families = {
        _plugin_family(cmd[len("check_mk-"):].split("!")[0])
        for cmd in check_commands
        if cmd.startswith("check_mk-")
    }
    evidence_sources: list[tuple[str, list[str]]] = []
    for unit in runtime.systemd_running:
        evidence_sources.append((f"systemd unit '{unit}' running", _generic_name_tokens(unit)))
    for proc in dict.fromkeys(runtime.processes):
        evidence_sources.append((f"process '{proc}'", _generic_name_tokens(proc)))
    for service in runtime.win_services_running:
        display = runtime.win_service_titles.get(service, "")
        text = f"Windows service '{service}'" + (f" ({display})" if display else "") + " running"
        evidence_sources.append((text, _generic_name_tokens(service, display)))

    found: dict[str, list[str]] = {}
    for text, tokens in evidence_sources:
        for token in tokens:
            if token in STOP_TOKENS or token in os_tokens:
                continue
            for family in catalog:
                if family in monitored_families or not _generic_token_matches(token, family):
                    continue
                items = found.setdefault(family, [])
                if text not in items:
                    items.append(text)
    return found


def _generic_by_host(
    agent_rows: Sequence[tuple[str, Mapping[str, str]]],
    sections_by_host: Mapping[str, _AgentSections],
    check_commands_by_host: Mapping[str, list[str]],
    catalog: Mapping[str, _GenericFamily],
) -> dict[str, dict[str, list[str]]]:
    """Generic candidates of all hosts, excluding families that run on almost
    all hosts of the same OS (label cmk/os_family)."""
    found_by_host: dict[str, dict[str, list[str]]] = {}
    os_by_host: dict[str, str] = {}
    hosts_per_os: dict[str, int] = {}
    for host_name, labels in agent_rows:
        sec = sections_by_host.get(host_name)
        if sec is None or not sec.sections:
            continue
        os_family = str(labels.get("cmk/os_family", "")).strip().lower() or "?"
        os_by_host[host_name] = os_family
        hosts_per_os[os_family] = hosts_per_os.get(os_family, 0) + 1
        found_by_host[host_name] = _generic_candidates(
            _runtime_facts(sec.sections), check_commands_by_host.get(host_name, []), catalog,
            _os_name_tokens(labels),
        )
    per_os_family: dict[tuple[str, str], int] = {}
    for host_name, found in found_by_host.items():
        for family in found:
            key = (os_by_host[host_name], family)
            per_os_family[key] = per_os_family.get(key, 0) + 1
    common = {
        key
        for key, count in per_os_family.items()
        if hosts_per_os.get(key[0], 0) >= _GENERIC_COMMON_MIN_HOSTS
        and count / hosts_per_os[key[0]] >= _GENERIC_COMMON_RATIO
    }
    for host_name, found in found_by_host.items():
        for family in list(found):
            if (os_by_host[host_name], family) in common:
                del found[family]
    return found_by_host


class _Subsystem(NamedTuple):
    token: str
    title: str
    monitored: bool
    via: list[str]
    evidence: list[str]
    severity: str  # "" (monitored) | "warn" | "crit"
    # 0.9.0-b14: "delivered" (section delivers data, discovery missing) |
    # "deployed" (plug-in deployed, but no own section - e.g. output goes
    # as piggyback to other hosts) | "running" (service/process running,
    # plug-in missing) | "label" (host label only)
    kind: str = ""


class _HostResult(NamedTuple):
    host_name: str
    status: str
    coverage_pct: int
    fraction_text: str
    findings: str
    # Flat representation incl. subheadings ("Unmonitored:",
    # "Already monitored:", "Sources:") - output 1:1 as the long output of
    # the piggyback service.
    detail_lines: list[str]
    capability_summary: str
    # 0.9.0-b13: structured detail sections for the GUI page. Defaults so that
    # older cache files (without these fields) can still be loaded - the
    # page then falls back to the flat detail_lines.
    unmonitored_lines: list[str] = []
    monitored_lines: list[str] = []
    source_lines: list[str] = []
    # 0.9.0-b21: unfiltered items (see lib/evaluate.py). Status and texts
    # above are the evaluation WITHOUT the Setup rule; page and check
    # re-evaluate the items with the rule effective for the host.
    items: list[dict[str, Any]] = []
    candidate_lines: list[str] = []
    ignored_lines: list[str] = []
    # Counters for the overall coverage (after applying the Setup rule)
    monitored_count: int = 0
    total_count: int = 0


def _monitored_map_for_host(check_commands: Sequence[str]) -> dict[str, list[str]]:
    """Determines, from a host's Livestatus check_command values, the set
    of already monitored (canonicalized) plugin tokens, per token with the
    concrete triggering plugin names (for the "via ..." display).

    Checkmk-generated check commands have the prefix "check_mk-",
    followed by the plugin name, e.g. "check_mk-mysql_capacity" or
    "check_mk-apache_status". The active check for the HW/SW inventory
    ("Checkmk HW/SW Inventory" service) instead has the prefix
    "check_mk_active-cmk_inv" (see step 3 / T_SECTION "inventory"
    above) - it is explicitly mapped to the token "inventory" here,
    so that "mk_inventory is present, but the HW/SW inventory service
    is not running" is correctly detected as an open finding.
    """
    result: dict[str, set[str]] = {}
    for cmd in check_commands:
        if cmd.startswith("check_mk_active-cmk_inv"):
            result.setdefault("inventory", set()).add("check_mk_active-cmk_inv")
            continue
        if not cmd.startswith("check_mk-"):
            continue
        plugin_name = cmd[len("check_mk-"):].split("!")[0]
        token = _canonical_token(plugin_name)
        if token and token not in STOP_TOKENS:
            result.setdefault(token, set()).add(plugin_name)
    return {token: sorted(names) for token, names in result.items()}


def _detail_sections_for(result: _HostResult, lookup: Any) -> list[tuple[str, list[str]]]:
    """Detail sections of the page (same order as in the service's long
    output); empty sections are omitted."""
    params = lookup.params_for(result.host_name) if result.items else {}
    mode = _ev.generic_mode(params)
    candidate_heading = (
        _("Candidates (generic match):")
        if mode == _ev.GENERIC_WARN
        else _("Candidates (generic match, info only):")
    )
    return [
        (heading, list(lines))
        for heading, lines in (
            (_("Unmonitored:"), result.unmonitored_lines),
            (candidate_heading, result.candidate_lines),
            (_("Ignored:"), result.ignored_lines),
            (_("Already monitored:"), result.monitored_lines),
            (_("Sources:"), result.source_lines),
        )
        if lines
    ]


def _monitored_elsewhere(
    token: str, host_name: str, check_commands_by_host: Mapping[str, Sequence[str]]
) -> list[tuple[str, str]]:
    """[(host, check plug-in)] of other hosts whose check plug-in matches a
    regex from detect.<token>.monitored_elsewhere (re.fullmatch)."""
    patterns = DETECT.get(token, {}).get("monitored_elsewhere", [])
    if not patterns:
        return []
    try:
        regexes = [re.compile(p) for p in patterns]
    except re.error:
        return []
    found: list[tuple[str, str]] = []
    for other, commands in sorted(check_commands_by_host.items()):
        if other == host_name:
            continue
        for cmd in commands:
            if not cmd.startswith("check_mk-"):
                continue
            plugin = cmd[len("check_mk-"):].split("!")[0]
            if any(r.fullmatch(plugin) for r in regexes):
                found.append((other, plugin))
                break
    return found


def _analyze_host(
    host_name: str,
    labels: Mapping[str, str],
    check_commands: Sequence[str],
    available_map: dict[str, list[str]],
    agent_sections: _AgentSections | None = None,
    generic_found: Mapping[str, list[str]] | None = None,
    generic_catalog: Mapping[str, _GenericFamily] | None = None,
    discovery: Mapping[str, _DiscoveryCounts] | None = None,
    check_commands_by_host: Mapping[str, Sequence[str]] | None = None,
) -> _HostResult:
    """Coverage correlation for a single host (as of 0.9.0-b14).

    Evidence per token (type -> list), only tokens from TITLES count:
      - T_CHECK:   already active check_command (Livestatus) - also
                   "monitored".
      - T_LABEL:   host label (name or value) maps to the token.
      - T_SECTION: section of the agent output (get-agent-output @cached)
                   whose name maps to the token and which delivers real
                   data (see _section_has_data()), or a
                   detect.<token>.direct rule matches.
      - T_PLUGIN:  deployed agent plug-in / local check according to
                   checkmk_agent_plugins_lnx/_win.
      - T_RUNTIME: detect.<token>.runtime rule matches (running
                   systemd unit, process outside of containers,
                   running Windows service).

    A token with evidence for which a check plugin exists on the site
    (available_map) is a "monitorable subsystem". Without a matching
    check_command it is an open finding - since b14 always WARN; the
    text distinguishes "data arrives / plug-in deployed" (discovery
    missing) from "running" (agent plug-in missing).

    0.9.0-b21: additionally generic candidates (generic_found, see
    _generic_candidates()) and return of the unfiltered items - the
    evaluation (status, coverage, texts) is done by lib/evaluate.py.

    Installed packages from the HW/SW inventory (T_INV_PACKAGE) are NO
    longer evidence since b14 (false positives, e.g. lvm2 without a single
    LV), but only an info line under "Sources". Hosts without agent output
    (no cache file) thus only have T_CHECK/T_LABEL.
    """
    monitored_map = _monitored_map_for_host(check_commands)
    inv_packages = _inventory_package_names(host_name)
    if agent_sections is None:
        agent_sections = _agent_sections(host_name)
    raw_sections = agent_sections.sections
    agent_plugins = _agent_plugin_names(raw_sections)
    runtime = _runtime_facts(raw_sections)

    # Collect capabilities per token (type -> evidence).
    capability_evidence: dict[str, dict[str, list[str]]] = {}

    def _add_capability(token: str, cap_type: str, evidence: str) -> None:
        by_type = capability_evidence.setdefault(token, {})
        items = by_type.setdefault(cap_type, [])
        if evidence not in items:
            items.append(evidence)

    # T_CHECK (0.9.0-b6): an already active, monitored check_command is
    # itself capability evidence (see docstring above) - purely generic
    # via TITLES (JSON-fed), no subsystem special cases.
    for token, plugin_names in monitored_map.items():
        if token in TITLES:
            _add_capability(token, T_CHECK, f"check_command(s)='{', '.join(plugin_names)}'")

    for name, value in labels.items():
        # OS labels describe the operating system, not an application
        # running on it ("Oracle Linux Server" is not an Oracle database,
        # "Citrix Hypervisor" is not a Citrix Delivery Controller).
        if name in _OS_LABELS:
            continue
        for raw in (name, value):
            token = _canonical_token(str(raw))
            if token in TITLES:
                _add_capability(token, T_LABEL, f"host_label='{name}:{value}'")

    # T_SECTION (0.9.0-b14): section name -> token, only if the section
    # delivers real data (empty section / only "[df]" header does not count).
    for section_name, lines in raw_sections.items():
        if section_name in SECTION_IGNORE:
            continue
        token = _canonical_token(section_name)
        if token not in TITLES or token in STOP_TOKENS:
            continue
        if _section_has_data(section_name, lines):
            _add_capability(token, T_SECTION, f"section '{section_name}' has data")

    for plugin in agent_plugins:
        token = _plugin_token(plugin)
        if token in TITLES and token not in STOP_TOKENS:
            _add_capability(token, T_PLUGIN, f"agent plug-in '{plugin}' deployed")

    # detect rules: "direct" acts like T_SECTION (data arrives),
    # "runtime" is the indirect evidence (service/process running).
    for token, spec in DETECT.items():
        if token not in TITLES:
            continue
        for kind, cap_type in (("direct", T_SECTION), ("runtime", T_RUNTIME)):
            for condition in spec.get(kind, []):
                hit = _match_condition(condition, raw_sections, runtime)
                if hit:
                    _add_capability(token, cap_type, hit)

    # 0.9.0-b17: detect.<token>.not_on_os - plug-in does not run on this
    # operating system (e.g. openvpn_clients is a Bash script, not a
    # Windows plug-in). Compared against the host label cmk/os_family;
    # no label means no restriction.
    os_family = str(labels.get("cmk/os_family", "")).strip().lower()

    def _os_excluded(token: str) -> bool:
        excluded = {o.lower() for o in DETECT.get(token, {}).get("not_on_os", [])}
        return bool(os_family) and os_family in excluded

    subsystems: list[_Subsystem] = []
    for token in sorted(capability_evidence):
        if token not in monitored_map and _os_excluded(token):
            continue
        available_names = available_map.get(token)
        if not available_names:
            # Capability detected, but no matching check plugin available
            # on this site at all - not a "monitorable subsystem" (only
            # available plugins count in the fraction).
            continue
        title = TITLES.get(token, token)
        by_type = capability_evidence[token]
        evidence = [item for items in by_type.values() for item in items]

        if token in monitored_map:
            subsystems.append(
                _Subsystem(
                    token=token, title=title, monitored=True,
                    via=monitored_map[token], evidence=evidence, severity="",
                )
            )
            continue
        # 0.9.0-b14: all open findings WARN for now (user requirement, to be
        # assessed in production). The type of evidence only determines the
        # text: data already arrives -> discovery missing; service running ->
        # agent plug-in missing.
        if T_SECTION in by_type:
            kind = "delivered"
        elif T_PLUGIN in by_type:
            kind = "deployed"
        elif T_RUNTIME in by_type:
            kind = "running"
        else:
            kind = "label"
        subsystems.append(
            _Subsystem(
                token=token, title=title, monitored=False,
                via=available_names, evidence=evidence, severity="warn", kind=kind,
            )
        )

    # 0.9.0-b21: unfiltered items; status/coverage/texts are produced in
    # lib/evaluate.py (shared with the check plugin).
    items: list[dict[str, Any]] = []
    for s in subsystems:
        if s.monitored:
            items.append({
                "kind": "monitored", "token": s.token, "title": s.title,
                "plugins": list(s.via), "evidence": list(s.evidence), "state": "monitored",
            })
            continue
        elsewhere = _monitored_elsewhere(s.token, host_name, check_commands_by_host or {})
        if elsewhere:
            # 0.9.0-b40: detect.<token>.monitored_elsewhere - monitored
            # centrally on another host (e.g. Entra Connect Sync via the Azure
            # special agent on the tenant host), not on this server.
            shown = ", ".join(f"{h} ({p})" for h, p in elsewhere[:3])
            if len(elsewhere) > 3:
                shown += f", ... (+{len(elsewhere) - 3})"
            items.append({
                "kind": "monitored", "token": s.token, "title": s.title,
                "plugins": sorted({p for _h, p in elsewhere}),
                "evidence": [*s.evidence, "monitored on: " + shown],
                "state": _("monitored on another host: %s") % shown,
            })
            continue
        if s.kind == "deployed" and agent_sections is not None and agent_sections.piggyback:
            # 0.9.0-b26: plug-in deployed, but only delivers piggyback data
            # for other hosts (e.g. oxidized). Covered if the plugin is
            # monitored on all target hosts.
            targets = sorted({
                t for name, hosts in agent_sections.piggyback.items()
                if _canonical_token(name) == s.token for t in hosts
            })
            if targets:
                commands = check_commands_by_host or {}
                by_lower = {h.lower(): h for h in commands}
                monitored_on = [
                    t for t in targets
                    if s.token in _monitored_map_for_host(commands.get(by_lower.get(t.lower(), t), []))
                ]
                missing = [t for t in targets if t not in monitored_on]
                pb_evidence = [*s.evidence, "piggyback data for: " + ", ".join(targets)]
                if not missing:
                    items.append({
                        "kind": "monitored", "token": s.token, "title": s.title,
                        "plugins": list(s.via), "evidence": pb_evidence,
                        "state": _("plug-in delivers piggyback data for %d host(s), monitored there (%d/%d)")
                        % (len(targets), len(monitored_on), len(targets)),
                    })
                    continue
                # Target host without any service = not created in Checkmk
                unknown = [t for t in missing if t.lower() not in by_lower]
                undiscovered = [t for t in missing if t.lower() in by_lower]
                hints = []
                if undiscovered:
                    hints.append(_("Run service discovery on the piggybacked host(s): %s") % ", ".join(undiscovered))
                if unknown:
                    hints.append(_("Create the piggybacked host(s) in Checkmk: %s") % ", ".join(unknown))
                items.append({
                    "kind": "open", "token": s.token, "title": s.title,
                    "plugins": list(s.via), "evidence": pb_evidence,
                    "state": _("plug-in delivers piggyback data for %d host(s), monitored on %d")
                    % (len(targets), len(monitored_on)),
                    "hint": " ".join(hints),
                })
                continue
        if s.kind == "deployed":
            # 0.9.0-b26: plug-in deployed, section header arrives, but without
            # data - a valid state for sections from empty_ok (e.g.
            # windows_tasks: no tasks reported by the agent). The
            # plug-in is deployed, there is nothing to monitor.
            empty = sorted(
                name for name in EMPTY_OK
                if name in raw_sections
                and _canonical_token(name) == s.token
                and not _section_has_data(name, raw_sections[name])
            )
            if empty:
                items.append({
                    "kind": "monitored", "token": s.token, "title": s.title,
                    "plugins": list(s.via),
                    "evidence": [*s.evidence, *(f"section '{n}' is empty" for n in empty)],
                    "state": _("plug-in deployed, nothing to monitor (section '%s' is empty)")
                    % "', '".join(empty),
                })
                continue
        if s.kind in ("delivered", "deployed") and discovery:
            # 0.9.0-b23: plug-in delivers data, and ALL services of this
            # subsystem's plugins have been decided (at least one disabled
            # by rule, none open) -> intentional, counts as covered.
            ignored_n = sum(discovery[p].ignored for p in s.via if p in discovery)
            undecided_n = sum(discovery[p].unmonitored for p in s.via if p in discovery)
            if ignored_n and not undecided_n:
                items.append({
                    "kind": "monitored", "token": s.token, "title": s.title,
                    "plugins": list(s.via), "evidence": list(s.evidence),
                    "state": _("plug-in deployed, all services disabled by rule (%d)") % ignored_n,
                })
                continue
        state_txt = {
            "delivered": _("agent delivers data, not monitored"),
            "deployed": _("agent plug-in deployed, delivers no data"),
            "running": _("running, not monitored"),
        }.get(s.kind, _("detected, not monitored"))
        if s.kind == "delivered":
            # Data is already there - a plug-in deployment hint would be
            # wrong here, only the service discovery is missing.
            hint = _("Run service discovery for this host.")
        elif s.kind == "deployed":
            # 0.9.0-b26: plug-in deployed, but delivers no data - a discovery
            # would find nothing, check the plug-in itself.
            hint = _(
                "The agent plug-in is deployed but delivers no data - check "
                "the plug-in (configuration, permissions, timeouts), then run "
                "service discovery."
            )
        else:
            hint = HINTS.get(s.token, "")
        items.append({
            "kind": "open", "token": s.token, "title": s.title,
            "plugins": list(s.via), "evidence": list(s.evidence),
            "state": state_txt, "hint": hint,
        })
    # 0.9.0-b27: remote site unreachable -> analysis incomplete, visible
    # as a finding instead of silently OK (based on check commands only).
    if agent_sections.site_error:
        items.append({
            "kind": "open", "token": "remote_site_unreachable",
            "title": _("Agent output from remote site"),
            "plugins": [], "evidence": [str(agent_sections.error)],
            "state": _("unavailable, analysis incomplete"),
            "hint": _(
                "Check that the central site is logged in to the remote site "
                "(Setup > Distributed monitoring) and that the remote site is reachable."
            ),
        })
    for family, evidence in sorted((generic_found or {}).items()):
        entry = (generic_catalog or {}).get(family)
        if entry is None:
            continue
        shown = evidence[:_GENERIC_MAX_EVIDENCE]
        if len(evidence) > len(shown):
            shown.append(f"... (+{len(evidence) - len(shown)})")
        items.append({
            "kind": "candidate", "token": f"generic:{family}", "title": entry.title,
            "plugins": list(entry.plugins), "evidence": shown,
            # 0.9.0-b39: short findings text; check plug-ins and data sources
            # are in the detail line (see lib/evaluate.py _item_line).
            "state": _("possible match (fuzzy search)"),
            "hint": _("See details for potential checks."),
            "sources": _generic_sources(entry),
        })

    source_lines: list[str] = []
    type_counts: dict[str, int] = {
        t: sum(1 for by_type in capability_evidence.values() if t in by_type)
        for t in (T_CHECK, T_LABEL, T_SECTION, T_PLUGIN, T_RUNTIME)
    }
    capability_summary = _("Evidence per subsystem: %s") % (
        ", ".join(f"{t}:{c}" for t, c in type_counts.items() if c) or "-"
    )
    source_lines.append(capability_summary)
    # T_INV_PACKAGE (0.9.0-b14): info only - packages that indicate an
    # available, unmonitored subsystem for which the agent output provides
    # no evidence. Affects neither status nor coverage.
    subsystem_tokens = {s.token for s in subsystems}
    pkg_only: dict[str, list[str]] = {}
    for pkg in inv_packages:
        token = _canonical_token(pkg)
        if (
            token in TITLES
            and token not in STOP_TOKENS
            and token not in subsystem_tokens
            and token not in monitored_map
            and available_map.get(token)
            and not _os_excluded(token)
        ):
            pkg_only.setdefault(token, []).append(pkg)
    if pkg_only:
        source_lines.append(
            _("Installed packages without runtime evidence (info only): %s")
            % "; ".join(
                f"{TITLES[t]} ({', '.join(sorted(pkgs)[:3])}{', ...' if len(pkgs) > 3 else ''})"
                for t, pkgs in sorted(pkg_only.items(), key=lambda kv: TITLES[kv[0]])
            )
        )
    source_lines.append(
        _("Inventory packages: %d (HW/SW inventory, info only)") % len(inv_packages)
    )
    # Transparency: without agent output there is no section/runtime
    # evidence - this should be visible.
    if agent_sections.error:
        source_lines.append(
            _("Agent sections: unavailable via %s (%s)")
            % (agent_sections.source, agent_sections.error)
        )
    else:
        source_lines.append(
            _("Agent sections: %d via %s") % (len(raw_sections), agent_sections.source)
        )

    return _build_result(host_name, items, source_lines, capability_summary, None)


def _build_result(
    host_name: str,
    items: list[dict[str, Any]],
    source_lines: list[str],
    capability_summary: str,
    params: Mapping[str, Any] | None,
) -> _HostResult:
    """Evaluate the items with the parameters of the setup rule (None = no
    rule, default behavior) -> _HostResult for display/cache."""
    evaluation = _ev.evaluate(items, params)
    mode = _ev.generic_mode(params)
    return _HostResult(
        host_name=host_name,
        status=evaluation.status,
        coverage_pct=evaluation.coverage_pct,
        fraction_text=evaluation.fraction_text,
        findings=evaluation.findings,
        detail_lines=_ev.detail_lines(evaluation, source_lines, mode),
        capability_summary=capability_summary,
        unmonitored_lines=evaluation.unmonitored_lines,
        monitored_lines=evaluation.monitored_lines,
        source_lines=list(source_lines),
        items=items,
        candidate_lines=evaluation.candidate_lines,
        ignored_lines=evaluation.ignored_lines,
        monitored_count=evaluation.monitored_count,
        total_count=evaluation.total_count,
    )


# 0.9.0-b23: User decisions from the "Check_MK Discovery" service (long
# output): "Service ignored: <plugin>: <item>" = disabled via rule,
# "Service unmonitored: <plugin>: <item>" = not yet decided. NOT used to
# detect subsystems, only to recognize an "agent delivers data" finding as
# deliberately decided.
_DISCOVERY_LINE_RE = re.compile(r"^Service (ignored|unmonitored): ([^:\s]+): ")


class _DiscoveryCounts(NamedTuple):
    ignored: int
    unmonitored: int


def _parse_discovery_output(long_output: str) -> dict[str, _DiscoveryCounts]:
    """Long output of the discovery service -> plugin -> number of ignored /
    undecided services. Livestatus returns line breaks as '\\n'."""
    ignored: dict[str, int] = {}
    unmonitored: dict[str, int] = {}
    for line in long_output.replace("\\n", "\n").splitlines():
        match = _DISCOVERY_LINE_RE.match(line.strip())
        if not match:
            continue
        target = ignored if match.group(1) == "ignored" else unmonitored
        target[match.group(2)] = target.get(match.group(2), 0) + 1
    return {
        plugin: _DiscoveryCounts(ignored.get(plugin, 0), unmonitored.get(plugin, 0))
        for plugin in set(ignored) | set(unmonitored)
    }


def _discovery_states(connection: Any) -> dict[str, dict[str, _DiscoveryCounts]]:
    """Host -> plugin -> _DiscoveryCounts, one Livestatus query per run."""
    try:
        rows = connection.query(
            "GET services\n"
            "Columns: host_name long_plugin_output\n"
            "Filter: check_command = check-mk-inventory\n"
        )
    except Exception:  # pragma: no cover - defensive, GUI context
        return {}
    return {
        host_name: _parse_discovery_output(long_output or "")
        for host_name, long_output in rows
        if isinstance(long_output, str)
    }


# 0.9.0-b22: Operating systems whose hosts are analyzed.
_AGENT_OS_TYPES = frozenset({"linux", "windows", "freebsd", "solaris", "aix"})


def _host_os(labels: Mapping[str, str]) -> str:
    """OS of the host from the agent labels: cmk/os_type (e.g. "linux" also
    for UniFi OS / OpenWrt, whose cmk/os_family differs); for older agents
    without cmk/os_type, cmk/os_family as a fallback."""
    value = labels.get("cmk/os_type") or labels.get("cmk/os_family") or ""
    return str(value).strip().lower()


# Duration of the last analysis run in this process (seconds); copied into
# the cache file by _save_cached_results().
_last_run_duration: list[float] = []


def _query_and_analyze_hosts() -> Sequence[_HostResult]:
    """Analysis run with duration measurement, see _query_and_analyze_hosts_impl()."""
    started = time.monotonic()
    try:
        return _query_and_analyze_hosts_impl()
    finally:
        _last_run_duration[:] = [time.monotonic() - started]


def _query_and_analyze_hosts_impl() -> Sequence[_HostResult]:
    """Determines via Livestatus all hosts monitored by the Checkmk agent
    (TCP) and applies the coverage correlation from _analyze_host() to each
    host.

    Filtering by "agent = cmk-agent" is done on the Python side using the
    tags column returned by Livestatus (instead of a Livestatus filter line),
    because Checkmk 2.5 expects the format
    "Filter: tags = <tag_group_id> <tag_id>" for host tag filters and no
    longer supports the "tags_<tag_group_id> = <tag_id>" shorthand.
    """
    connection = sites.live()
    try:
        _reload_rules()
    except RulesLoadError as exc:
        # Purely data-based rule detection (see module docstring): no silent
        # code fallback. A problem with the rule file leads to a clear error
        # visible to the user instead of an analysis result of unclear
        # origin.
        html.show_error(
            "Coverage rules could not be loaded - analysis "
            f"aborted: {exc}"
        )
        return []
    try:
        # 0.9.0-b27: Site per host (distributed monitoring), so that the
        # agent output is fetched from the responsible site.
        with sites.prepend_site():
            site_rows = connection.query(
                "GET hosts\n"
                "Columns: name tags labels\n"
            )
        rows = [row[1:] for row in site_rows]
        host_sites = {row[1]: row[0] for row in site_rows}
        # services_with_info/-fullstate do NOT return check_command; so query
        # all service check commands per host separately (a single
        # Livestatus query for the entire analysis run, not per host).
        service_rows = connection.query(
            "GET services\n"
            "Columns: host_name check_command\n"
        )
    except Exception as exc:  # pragma: no cover - debug aid
        html.show_error(f"Livestatus error: {exc!r}")
        return []

    check_commands_by_host: dict[str, list[str]] = {}
    for host_name, check_command in service_rows:
        if isinstance(check_command, str) and check_command:
            check_commands_by_host.setdefault(host_name, []).append(check_command)

    # "cmk -L" EXACTLY ONCE for the entire analysis run, not per host.
    available_map = _available_plugin_map()
    discovery_by_host = _discovery_states(connection)

    agent_rows: list[tuple[str, dict[str, str]]] = []
    for host_name, tags, labels in rows:
        # Note: Checkmk allows custom IDs for the "agent" tag group (e.g.
        # "all-agents" instead of the default value "cmk-agent") - a check
        # for tags["agent"] == "cmk-agent" is therefore NOT site-independent.
        # More robust is the built-in "tcp" auxiliary tag (aux_tag "tcp"),
        # which is set as soon as a host is contacted via the Checkmk agent
        # (TCP) - independent of the site's specific tag group/ID
        # configuration.
        tag_map = tags if isinstance(tags, dict) else {}
        is_cmk_agent_host = tag_map.get("tcp") == "tcp" or tag_map.get(
            "checkmk-agent"
        ) == "checkmk-agent"
        if not is_cmk_agent_host:
            continue
        label_map = labels if isinstance(labels, dict) else {}
        # 0.9.0-b22: the tcp tag alone is too imprecise (also special agent
        # and piggyback hosts, routers with their own agent). Additionally,
        # the agent must report an operating system for which agent plug-ins
        # exist - see _host_os().
        if _host_os(label_map) not in _AGENT_OS_TYPES:
            continue
        agent_rows.append((host_name, label_map))

    # 0.9.0-b7: Agent outputs of all hosts ONCE per run, in parallel, from
    # the core cache (get-agent-output @cached) - no agent query.
    sections_by_host = _collect_agent_sections([h for h, _labels in agent_rows], host_sites)

    # 0.9.0-b21: generic matching - first collect for all hosts, then remove
    # families that run on almost all hosts of the same OS (base OS
    # components, without a maintained list).
    catalog = _generic_catalog()
    generic_by_host = _generic_by_host(agent_rows, sections_by_host, check_commands_by_host, catalog)

    results: list[_HostResult] = []
    for host_name, labels in agent_rows:
        result = _analyze_host(
            host_name=host_name,
            labels=labels,
            check_commands=check_commands_by_host.get(host_name, []),
            available_map=available_map,
            agent_sections=sections_by_host.get(host_name),
            generic_found=generic_by_host.get(host_name),
            generic_catalog=catalog,
            discovery=discovery_by_host.get(host_name),
            check_commands_by_host=check_commands_by_host,
        )
        results.append(result)

    results.sort(key=lambda r: r.host_name)
    return results


# 0.9.0-b25: Cache file, piggyback writing and run state live in
# lib/runstate.py (without GUI imports) - mcactl uses them for the
# fast paths without starting up the GUI. Only thin adapters to
# _HostResult here.


def _cache_path() -> str | None:
    return _rs.cache_path()


def _save_cached_results(
    results: Sequence[_HostResult],
    *,
    last_full_run_timestamp: float | None = None,
    last_piggyback_refresh_timestamp: float | None = None,
) -> None:
    """Persists the result (JSON file, shared by all Apache workers).
    Duration: from this process's run, otherwise the last stored value is
    kept."""
    _rs.save_results(
        [r._asdict() for r in results],
        last_full_run_timestamp=last_full_run_timestamp,
        last_piggyback_refresh_timestamp=last_piggyback_refresh_timestamp,
        last_run_duration_seconds=_last_run_duration[0] if _last_run_duration else None,
    )


def _load_cache_raw() -> dict[str, Any] | None:
    return _rs.load_cache_raw()


def _cached_run_duration() -> float | None:
    """Duration of the last successful run: preferably the total duration of
    the background run (incl. starting up the GUI), otherwise the pure
    analysis time stored in the cache file."""
    job = _rs.load_job_state()
    started, finished = job.get("started"), job.get("finished")
    if job.get("state") == "done" and isinstance(started, (int, float)) and isinstance(finished, (int, float)):
        return max(0.0, float(finished) - float(started))
    payload = _load_cache_raw() or {}
    value = payload.get("last_run_duration_seconds")
    return float(value) if isinstance(value, (int, float)) else None


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return _("unknown (run the analysis again)")
    if seconds < 60:
        return f"{seconds:.1f} s"
    return f"{int(seconds // 60)} min {int(seconds % 60)} s"


def _load_cached_results() -> tuple[float, Sequence[_HostResult]] | None:
    payload = _load_cache_raw()
    if payload is None:
        return None
    fields = set(_HostResult._fields)
    try:
        # Ignore unknown fields (e.g. from a newer version)
        results = [
            _HostResult(**{k: v for k, v in row.items() if k in fields})
            for row in payload["results"]
        ]
        return float(payload["generated_at"]), results
    except (KeyError, TypeError):
        return None


def _run_piggyback_full(results: Sequence[_HostResult]) -> tuple[int, str | None, float]:
    """Piggyback data with NEW content directly after an analysis run:
    both timestamps set to 'now', result written to the cache file."""
    now = time.time()
    rows = [r._asdict() for r in results]
    written, error = _rs.write_piggyback(
        rows,
        last_full_run_timestamp=now,
        last_piggyback_refresh_timestamp=now,
    )
    _rs.remove_stale_piggyback(rows)
    _save_cached_results(
        results,
        last_full_run_timestamp=now,
        last_piggyback_refresh_timestamp=now,
    )
    return written, error, now


def _run_piggyback_refresh() -> tuple[int, str | None]:
    return _rs.run_piggyback_refresh()


def _run_analysis_and_store() -> tuple[int, int, str | None]:
    """Complete analysis run incl. storing (and piggyback, if enabled).
    Called by mcactl (fullrun/rerun), no longer from the Apache
    request. Returns: (hosts, piggyback written, error)."""
    results = _query_and_analyze_hosts()
    if _generate_piggyback_data_enabled():
        written, error, _now = _run_piggyback_full(results)
        return len(results), written, error
    _save_cached_results(results, last_full_run_timestamp=time.time())
    return len(results), 0, None


def _perfometer_style(coverage_pct: int, status: str | None = None) -> str:
    """Step 1c: inline CSS background analogous to the Perf-o-Meter style in
    Checkmk views - a horizontal linear-gradient bar whose filled portion
    corresponds to coverage_pct. Colors taken 1:1 from the actual traffic
    light colors of the running system (see
    themes/facelift/theme.css: .state0{background-color:#13d389},
    .state1{background-color:#ffd703}, .state2{background-color:#c83232}),
    so that perfometer and status column are color-consistent.
    Rounded corners analogous to span.state_rounded_fill (border-radius:2px)
    in the normal service table. Font deliberately black, as Checkmk also
    does for state0/state1 (light backgrounds).
    """
    pct = max(0, min(100, coverage_pct))
    # 0.9.0-b29: per host the status determines the color (the status column
    # is dropped) - otherwise a WARN host with 92 % would be green. Without
    # status (total bar) still by percentage.
    by_status = {"OK": "#13d389", "WARN": "#ffd703", "CRIT": "#c83232"}
    if status is not None:
        fill = by_status.get(status, "#a0a0a0")  # UNKNOWN gray
    elif pct >= 90:
        fill = "#13d389"  # green, like state0
    elif pct >= 50:
        fill = "#ffd703"  # yellow, like state1
    else:
        fill = "#c83232"  # red, like state2
    return (
        f"background: linear-gradient(to right, {fill} 0%, {fill} {pct}%, "
        f"#e0e0e0 {pct}%, #e0e0e0 100%); text-align:center; font-weight:bold; "
        "color:#000; border-radius:4px;"
    )

def _host_link(host_name: str) -> HTML:
    """Step 1b: clickable link to the "Service of host" page,
    identical to the convention of regular Checkmk views. Looked up live on
    the test site: the built-in view name for the host detail view
    (services of a single host) is "host", called as
    view.py?view_name=host&host=<hostname> - see
    cmk.gui.views.builtin_views (view ID "host") and
    cmk.gui.utils.urls.makeuri_contextless(), which builds exactly these
    context-less URLs for views.
    """
    href = makeuri_contextless(
        request, [("view_name", "host"), ("host", host_name)], filename="view.py"
    )
    return html.render_a(host_name, href=href)


# ---------------------------------------------------------------------------
# Expansion stage 2.0.0: access to the global Setup options (see
# plugins/wato/monitoring_coverage_analyzer_globals.py /
# plugins/config/monitoring_coverage_analyzer.py).
# ---------------------------------------------------------------------------


def _generate_piggyback_data_enabled() -> bool:
    return _rs.generate_piggyback_data_enabled()


def _piggyback_interval_hours() -> int:
    return _rs.piggyback_interval_hours()


def _is_full_run_due(*, force: bool) -> tuple[bool, str]:
    return _rs.is_full_run_due(force=force)


# 0.9.0-b21: Setup rule "Monitoring coverage analysis" (rulesets/
# monitoring_coverage.py) - the same rule the check plug-in receives as
# parameters. The page evaluates it per host via Checkmk's own rule
# evaluation (like "Effective parameters of" in Setup).
_RULESET_NAME = "checkgroup_parameters:checkmk_monitoring_coverage"


class _RuleLookup:
    def __init__(self) -> None:
        self.error: str | None = None
        self._ruleset: Any = None
        self._memo: dict[str, dict[str, Any]] = {}
        try:
            from cmk.gui.watolib.rulesets import SingleRulesetRecursively

            rulesets = SingleRulesetRecursively.load_single_ruleset_recursively(_RULESET_NAME)
            ruleset = rulesets.get(_RULESET_NAME)
            if ruleset is not None and not ruleset.is_empty():
                self._ruleset = ruleset
        except Exception as exc:  # pragma: no cover - defensive, GUI context
            self.error = f"{exc!r}"

    @property
    def rule_count(self) -> int:
        return 0 if self._ruleset is None else self._ruleset.num_rules()

    def params_for(self, host_name: str) -> dict[str, Any]:
        if host_name not in self._memo:
            self._memo[host_name] = self._lookup(host_name)
        return self._memo[host_name]

    def _lookup(self, host_name: str) -> dict[str, Any]:
        if self._ruleset is None:
            return {}
        try:
            value, _rules = self._ruleset.analyse_ruleset(
                host_name, None, PIGGYBACK_SERVICE_TITLE, {}, debug=False
            )
        except Exception as exc:  # pragma: no cover - defensive, GUI context
            self.error = f"{host_name}: {exc!r}"
            return {}
        if isinstance(value, dict) and "tp_default_value" in value:
            # Time-dependent parameters: the page uses the default value.
            value = value.get("tp_default_value")
        return dict(value) if isinstance(value, dict) else {}


def _apply_rules(results: Sequence[_HostResult], lookup: _RuleLookup) -> list[_HostResult]:
    """Re-evaluates the items of each host with its Setup rule."""
    out: list[_HostResult] = []
    for r in results:
        if not r.items:
            out.append(r)  # cache from before b21 without items
            continue
        out.append(
            _build_result(
                r.host_name, list(r.items), list(r.source_lines), r.capability_summary,
                lookup.params_for(r.host_name),
            )
        )
    return out


# Sorting of the host table by clicking the column header, as in the
# Checkmk views (cmk.gui.views.sort_url): URL variable "sort", value
# "<column>" ascending, "-<column>" descending. Click sequence per column:
# ascending -> descending -> off (default order by hostname).
_SORT_COLUMNS = ("host", "coverage")


def _current_sort() -> str | None:
    value = request.get_ascii_input("sort") or ""
    return value if value.lstrip("-") in _SORT_COLUMNS else None


def _sort_vars() -> list[tuple[str, str]]:
    current = _current_sort()
    return [("sort", current)] if current else []


def _next_sort(column: str) -> str | None:
    current = _current_sort()
    if current == column:
        return "-" + column
    if current == "-" + column:
        return None
    return column


def _sorted_results(results: Sequence[_HostResult]) -> list[_HostResult]:
    current = _current_sort()
    ordered = sorted(results, key=lambda r: r.host_name.lower())
    if current is None:
        return ordered
    reverse = current.startswith("-")
    if current.lstrip("-") == "coverage":
        # Equal coverage: hostname ascending (stable sort)
        return sorted(ordered, key=lambda r: r.coverage_pct, reverse=reverse)
    return sorted(ordered, key=lambda r: r.host_name.lower(), reverse=reverse)


def _sortable_th(title: str, column: str, style: str | None = None) -> None:
    nxt = _next_sort(column)
    url = makeuri_contextless(
        request, [("sort", nxt)] if nxt else [], filename="monitoring_coverage_analyzer.py"
    )
    html.open_th(
        class_=["sort"],
        onclick=f"location.href={json.dumps(url)}",
        title=_("Sort by %s") % title,
        style=style,
    )
    html.write_text(title)
    html.close_th()


def _page_breadcrumb() -> Breadcrumb:
    """Setup > Maintenance > Analyze monitoring coverage - as with the
    built-in maintenance pages (e.g. "Analyze configuration", there via
    WatoMode.breadcrumb(): main menu + topic from the MainModule + page).
    Import of MainModuleTopicMaintenance deliberately local: cmk.gui.wato may
    not be fully initialized yet when the page plug-ins are loaded."""
    from cmk.gui.wato import MainModuleTopicMaintenance

    breadcrumb = make_topic_breadcrumb(
        main_menu_registry.menu_setup(),
        MainModuleTopicMaintenance.title,
        MainModuleTopicMaintenance.name,
    )
    breadcrumb.append(make_current_page_breadcrumb_item(PAGE_TITLE))
    return breadcrumb


class PageMonitoringCoverageAnalyzer(Page):
    @override
    def page(self, ctx: PageContext) -> PageResult:
        # Expansion stage 2.0.0: the two cron trigger GET parameters are
        # handled BEFORE the normal page build and return a plain text
        # response instead of HTML (analogous to Checkmk's own
        # automation/Ajax endpoints) - this keeps the endpoint usable both
        # via a direct Python call in the site context (see local/bin/
        # mcactl) and via a real authenticated
        # HTTP GET (e.g. curl with a GUI session cookie, for manual tests).
        if ctx.request.has_var("_cron_refresh"):
            written, error = _run_piggyback_refresh()
            html.write_text(
                f"OK refresh written={written} error={error}\n"
                if error is None
                else f"ERROR refresh written={written} error={error}\n"
            )
            return None
        if ctx.request.has_var("_cron_fullrun"):
            force = ctx.request.has_var("_force")
            due, reason = _is_full_run_due(force=force)
            if not due:
                html.write_text(f"SKIP fullrun not due ({reason})\n")
                return None
            # 0.9.0-b25: here too in the background, not in the Apache request
            started, message = _rs.start_background_rerun()
            html.write_text(
                f"{'OK' if started else 'SKIP'} fullrun {message}\n"
            )
            return None

        make_header(html, PAGE_TITLE, _page_breadcrumb())
        self._show_cron_warning()

        # 0.9.0-b15: visible feedback while the re-run is in progress - the
        # analysis is synchronous (page only responds after the run, approx.
        # 10-15 s on the test site). On submit: disable the button (no
        # double click), show spinner + text. A disabled submit button is
        # NOT sent along, therefore "_analyze" is added as a hidden input.
        html.write_html(HTML.without_escaping(_RERUN_SPINNER_CSS))
        html.begin_form("analyze", method="GET", onsubmit=_RERUN_ONSUBMIT_JS)
        html.help(
            _(
                "This page shows a cached analysis of the hosts monitored "
                "via the Checkmk agent, correlating monitored Checkmk check "
                "plug-ins (via Livestatus) against plug-ins available on "
                "this site ('cmk -L'), host labels, installed packages "
                "from the Checkmk HW/SW inventory, AND agent plug-ins "
                "actually delivered by the agent (agent_section proxy). "
                "The result is cached on disk and refreshed by the daily "
                "full run or when you click 'Re-run analysis' below. The "
                "analysis runs in the background, outside the web server; "
                "the page reloads automatically until it has finished. "
                "Click the arrow before a "
                "hostname to expand the per-subsystem detail, analogous "
                "to the long output of the 'Checkmk Monitoring Coverage' "
                "service."
            )
        )
        # Step 2: button renamed from "Start analysis now" to
        # "Re-run analysis", because the page now ALWAYS shows a (possibly
        # cached) result - so the button no longer triggers an initial
        # start, but explicitly a new run.
        html.button("_analyze", _("Re-run analysis"), cssclass="hot")
        html.open_span(id_="mca_running", style="display:none")
        html.open_span(class_="mca_spinner")
        html.close_span()
        html.write_text(
            _("Starting analysis ...")
        )
        html.close_span()
        html.hidden_fields()
        html.end_form()

        # 0.9.0-b25: "Re-run analysis" starts the analysis as a separate
        # process outside of Apache (mcactl, command "rerun") - with
        # many hosts or slow systems it takes longer than the Apache
        # timeout. The page shows the progress and reloads until the run
        # has finished.
        start_message = None
        # No result yet (first visit) -> like a click on Re-run
        if ctx.request.has_var("_analyze") or _load_cache_raw() is None:
            started, message = _rs.start_background_rerun()
            if not started and message != "already running":
                start_message = _("Could not start the analysis: %s") % message
        self._show_job_status(start_message)
        self._show_results()
        return None

    def _show_cron_warning(self) -> None:
        """Yellow warning box if the cron jobs of "mcactl setup" are missing,
        outdated or not active in the site crontab."""
        problem = _rs.cron_problem()
        if problem is None:
            return
        html.show_warning(
            HTML.with_escaping(
                _(
                    "The cron jobs of the Monitoring Coverage Analyzer (MCA) are not "
                    "set up correctly: %s. Without them, the daily full analysis run and the "
                    "piggyback refresh every 5 minutes do not take place, and the "
                    "'Checkmk Monitoring Coverage' services become stale."
                )
                % problem
            )
            + HTML.without_escaping("<br>")
            + HTML.with_escaping(_("Run once as the site user:"))
            + HTML.without_escaping(" <tt>mcactl setup</tt>")
            + HTML.with_escaping(
                " " + _("(in distributed setups on the central site only).")
            )
        )

    def _show_job_status(self, start_message: str | None) -> None:
        status = _rs.job_status()
        state = status.get("state")
        last = _cached_run_duration()
        if start_message:
            html.show_error(start_message)
        if state == "running":
            started = status.get("started") or status.get("requested")
            since = (
                time.strftime("%H:%M:%S", time.localtime(float(started)))
                if isinstance(started, (int, float)) else "?"
            )
            text = _("Analysis running since %s") % since
            if last is not None:
                text += " - " + _("last run took %s") % _format_duration(last)
            text += " - " + _("this page reloads automatically.")
            html.open_div(class_="info", style="margin:8px 0")
            html.open_span(class_="mca_spinner")
            html.close_span()
            html.write_text(text)
            html.close_div()
            # Reload WITHOUT _analyze (otherwise a new run would be started),
            # sorting is preserved.
            url = makeuri_contextless(request, _sort_vars(), filename="monitoring_coverage_analyzer.py")
            html.javascript(f"setTimeout(function(){{window.location.href={json.dumps(url)};}}, 5000);")
        elif state == "failed":
            # An aborted process has no end time -> start time
            when_ts = status.get("finished") or status.get("started") or status.get("requested")
            when = (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(when_ts)))
                if isinstance(when_ts, (int, float)) else "?"
            )
            html.show_error(
                _("Last analysis run failed (%s): %s - the result below is from an earlier run.")
                % (when, status.get("error") or _("unknown error"))
            )

    def _show_summary(self, results: Sequence[_HostResult]) -> None:
        """Overall coverage across all hosts: sum of monitored divided by
        sum of monitorable subsystems (per Setup rule) - so hosts with
        many subsystems weigh more than in an average of the host
        percentages."""
        if not results:
            return
        monitored = sum(r.monitored_count for r in results)
        total = sum(r.total_count for r in results)
        states: dict[str, int] = {}
        for r in results:
            states[r.status] = states.get(r.status, 0) + 1
        pct = 100 if total == 0 else round(100 * monitored / total)
        html.open_table(class_=("data",), style="margin-bottom:10px")
        html.open_tr()
        html.td(_("Overall coverage"), style="font-weight:bold; padding-right:12px")
        html.open_td(style=_perfometer_style(pct) + " min-width:120px")
        html.write_text(f"{pct}%")
        html.close_td()
        html.td(
            _("%d/%d monitorable subsystems monitored on %d hosts") % (monitored, total, len(results))
            + " (" + ", ".join(
                f"{states[s]} {s}" for s in ("OK", "WARN", "CRIT", "UNKNOWN") if states.get(s)
            ) + ")",
            style="padding-left:12px",
        )
        html.close_tr()
        html.close_table()

    def _show_results(self) -> None:
        cached = _load_cached_results()
        if cached is None:
            # First visit: the run was started in page(), the status
            # above shows the progress and reloads the page.
            html.p(_("No analysis result yet - the first analysis run has been started."))
            return
        generated_at, results = cached

        lookup = _RuleLookup()
        results = _sorted_results(_apply_rules(results, lookup))

        html.h3(_("Analysis result"))
        self._show_summary(results)
        age_txt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(generated_at))
        html.p(
            _("Last run: %s, took %s")
            % (age_txt, _format_duration(_cached_run_duration()))
        )
        html.p(_("Rule file last modified: %s") % _rules_source_status())
        rules_txt = _("MCA setup user rules applied: %d rule(s)") % lookup.rule_count
        if lookup.error:
            rules_txt += " - " + _("error: %s") % lookup.error
        html.p(rules_txt)

        if not results:
            html.show_message(
                _(
                    "No hosts monitored via the Checkmk agent (TCP) with a "
                    "supported operating system (linux, windows, freebsd, "
                    "solaris, aix) were found."
                )
            )
            return

        html.open_table(class_=("data", "table"))
        html.open_tr(class_="header")
        html.th("")  # expand arrow column
        # 0.9.0-b29: status column dropped (color of the coverage column),
        # hostname twice as wide (previously ~215 px, wrapped too early).
        _sortable_th(_("Hostname"), "host", style="min-width:430px")
        _sortable_th(_("Coverage"), "coverage")
        # Spacing to the coverage bar (fills its cell up to the edge).
        html.th(_("Subsystems"), style="padding-left:12px")
        html.th(_("Findings"))
        html.close_tr()
        for idx, result in enumerate(results):
            row_id = f"mca_detail_{idx}"
            html.open_tr(class_="even0" if idx % 2 == 0 else "odd0")
            html.open_td()
            # Plain onclick + style.display toggling, without external
            # JS libraries: the arrow rotates via CSS transform and the
            # detail row is shown/hidden via style.display.
            html.write_html(
                html.render_a(
                    "\u25b6",
                    href="javascript:void(0)",
                    onclick=(
                        f"var d=document.getElementById('{row_id}');"
                        "var t=this;"
                        "if(d.style.display==='none'||d.style.display===''){"
                        "d.style.display='table-row';"
                        "t.innerHTML='\\u25bc';"
                        "}else{"
                        "d.style.display='none';"
                        "t.innerHTML='\\u25b6';"
                        "}"
                    ),
                    title=_("Show / hide subsystem detail"),
                )
            )
            html.close_td()
            # Step 1b: hostname as clickable link to "Service of host".
            html.open_td()
            html.write_html(_host_link(result.host_name))
            html.close_td()
            # Step 1c: Perf-O-Meter-like background bar.
            html.open_td(
                style=_perfometer_style(result.coverage_pct, result.status),
                title=result.status,
            )
            html.write_text(f"{result.coverage_pct}%")
            html.close_td()
            html.td(result.fraction_text, style="padding-left:12px")
            html.td(result.findings)
            html.close_tr()

            html.open_tr(id_=row_id, style="display:none")
            html.open_td()
            html.close_td()
            html.open_td(colspan=4)
            sections = _detail_sections_for(result, lookup)
            if sections:
                for heading, lines in sections:
                    # No html.h4() in this Checkmk version (only h1-h3);
                    # bold div instead of h3, which is already used on the
                    # page as section title ("Analysis result").
                    html.div(heading, style="font-weight:bold; margin-top:6px")
                    html.open_ul()
                    for line in lines:
                        html.li(line)
                    html.close_ul()
            else:
                # Older cache file without structured fields.
                html.open_ul()
                for line in result.detail_lines:
                    html.li(line)
                html.close_ul()
            html.close_td()
            html.close_tr()
        html.close_table()
        html.p(_("Number of hosts checked: %d") % len(results))


page_registry.register(PageEndpoint("monitoring_coverage_analyzer", PageMonitoringCoverageAnalyzer()))
