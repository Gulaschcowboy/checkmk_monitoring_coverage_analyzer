# Copyright (C) 2026 Alexander Wilms, Christian Wirtz
# SPDX-License-Identifier: GPL-2.0-only
"""Test harness: loads the MCA page module and its shared library without a
Checkmk installation.

The page module imports a handful of Checkmk GUI modules at load time. They
are replaced by stand-ins here, so the detection logic (rules file, detect
conditions, host analysis) and the evaluation can be tested with the
standard library alone. Site-dependent lookups ('cmk -L', agent plug-in and
special agent files, HW/SW inventory) are replaced by a synthetic plug-in
catalog: a few fixed entries plus one check plug-in per curated rule of the
rules file, derived from its aliases.

All host names and agent data in the tests are synthetic.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
PAGE_DIR = REPO / "local" / "share" / "check_mk" / "web" / "plugins" / "pages"
PAGE_FILE = PAGE_DIR / "monitoring_coverage_analyzer.py"
RULES_FILE = PAGE_DIR / "monitoring_coverage_analyzer_rules.json"
LIB_ROOT = REPO / "local" / "lib" / "python3"

# Check plug-ins of a fictitious site: (name, type, title) like 'cmk -L'.
PLUGIN_CATALOG: list[tuple[str, str, str]] = [
    ("smart_posix_temp", "agent", "S.M.A.R.T.: Temperature"),
    ("smart_posix_stats", "agent", "S.M.A.R.T.: Statistics"),
    ("hyperv_vms", "agent", "Microsoft Hyper-V Server: VM state"),
    ("hyperv_vm_general", "agent", "Microsoft Hyper-V Server: VM general"),
    ("hyperv_vm_ram", "agent", "Microsoft Hyper-V Server: VM RAM"),
    ("sap_state", "agent", "SAP R/3: State"),
    ("sap_hana_status", "agent", "SAP HANA: Status"),
    ("saprouter_cert", "agent", "SAP router: Certificate"),
    ("graylog_cluster_stats", "agent", "Graylog: Cluster statistics"),
    ("mssql_counters_locks", "agent", "MS SQL: Locks"),
    ("mysql_capacity", "agent", "MySQL: Capacity"),
    ("azure_ad_sync", "agent", "Microsoft Entra ID: Sync"),
    ("esx_vsphere_vm_cpu", "agent", "VMware ESX: VM CPU"),
    ("cisco_temperature", "snmp", "Cisco: Temperature"),
    # Check plug-ins of an MKP the rules file does not know
    ("acmecloud_info", "agent", "ACME Cloud: Info"),
    ("acmecloud_users", "agent", "ACME Cloud: Users"),
]

_CMK_MODULES = (
    "cmk",
    "cmk.gui",
    "cmk.gui.sites",
    "cmk.gui.breadcrumb",
    "cmk.gui.htmllib",
    "cmk.gui.htmllib.header",
    "cmk.gui.htmllib.html",
    "cmk.gui.http",
    "cmk.gui.i18n",
    "cmk.gui.logged_in",
    "cmk.gui.main_menu",
    "cmk.gui.pages",
    "cmk.gui.type_defs",
    "cmk.gui.utils",
    "cmk.gui.utils.html",
    "cmk.gui.utils.urls",
)


class _StubModule(types.ModuleType):
    """Module whose unknown attributes are mocks."""

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        value = mock.MagicMock(name=f"{self.__name__}.{name}")
        setattr(self, name, value)
        return value


def _install_cmk_stubs() -> None:
    for name in _CMK_MODULES:
        if name not in sys.modules:
            module = _StubModule(name)
            module.__path__ = []  # mark as package
            sys.modules[name] = module
    sys.modules["cmk.gui.i18n"]._ = lambda text: text  # type: ignore[attr-defined]

    class Page:  # base class of the page
        pass

    sys.modules["cmk.gui.pages"].Page = Page  # type: ignore[attr-defined]


def load_page_module() -> types.ModuleType:
    """Imports the page module once (cached in sys.modules)."""
    if "mca_page" in sys.modules:
        return sys.modules["mca_page"]
    _install_cmk_stubs()
    if str(LIB_ROOT) not in sys.path:
        sys.path.insert(0, str(LIB_ROOT))
    spec = importlib.util.spec_from_file_location("mca_page", PAGE_FILE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["mca_page"] = module
    spec.loader.exec_module(module)
    module._data_sources = lambda: ([], [])
    module._inventory_package_names = lambda host_name: []
    module._reload_rules()
    # The site offers a check plug-in for every curated rule, so every rule
    # can be tested; plus the fixed entries above.
    derived = [(plugin_for(token, module), "agent", f"{token} (test)") for token in module.DETECT]
    known = {name for name, _t, _x in PLUGIN_CATALOG}
    catalog = list(PLUGIN_CATALOG) + [row for row in derived if row[0] not in known]
    module._cmk_list_plugins = lambda: list(catalog)
    module._available_plugins_cache.clear()
    return module


def plugin_for(token: str, module: types.ModuleType | None = None) -> str:
    """A check plug-in name that the rules file maps to the token: the
    token itself or one of its aliases."""
    m = module or load_page_module()
    candidates = [token] + sorted(k for k, v in m.ALIASES.items() if v == token)
    for name in candidates:
        if m._canonical_token(name) == token:
            return name
    raise AssertionError(f"no plug-in name maps to the rules file token {token!r}")


def load_evaluate_module() -> types.ModuleType:
    if str(LIB_ROOT) not in sys.path:
        sys.path.insert(0, str(LIB_ROOT))
    from cmk_addons.plugins.monitoring_coverage_analyzer.lib import evaluate

    return evaluate


def load_lib_module(name: str) -> types.ModuleType:
    """A module of the package's lib/ (they import nothing from Checkmk at
    module level)."""
    if str(LIB_ROOT) not in sys.path:
        sys.path.insert(0, str(LIB_ROOT))
    return importlib.import_module(f"cmk_addons.plugins.monitoring_coverage_analyzer.lib.{name}")


# --- building synthetic agent output -----------------------------------------


def systemd_units(*units: str) -> str:
    lines = ["<<<systemd_units>>>", "[all]"]
    lines += [f"{unit} loaded active running {unit}" for unit in units]
    return "\n".join(lines) + "\n"


def processes(*names: str) -> str:
    """Windows/BSD style 'ps' section."""
    lines = ["<<<ps>>>"] + [f"(root,1,1,00:00:01,1)\t{name}" for name in names]
    return "\n".join(lines) + "\n"


def win_services(*names: str) -> str:
    lines = ["<<<services>>>"] + [f"{name} running/auto {name}" for name in names]
    return "\n".join(lines) + "\n"


LINUX = {"cmk/os_family": "linux"}
WINDOWS_SERVER = {
    "cmk/os_family": "windows",
    "cmk/os_name": "Microsoft Windows Server 2022 Standard",
}
WINDOWS_CLIENT = {"cmk/os_family": "windows", "cmk/os_name": "Microsoft Windows 11 Pro"}


def analyze(
    agent_output: str,
    *,
    labels: Mapping[str, str] = LINUX,
    checks: Sequence[str] = (),
    other_hosts: Mapping[str, Sequence[str]] | None = None,
    host: str = "testhost",
    packages: Sequence[str] = (),
    agent_error: str | None = None,
) -> list[dict[str, Any]]:
    """Runs the host analysis on synthetic agent output and returns the
    items (open/monitored/candidate findings) of the host.

    checks: check plug-in names already monitored on the host.
    other_hosts: host -> check plug-in names of other hosts of the site.
    packages: installed packages of the host (HW/SW inventory).
    agent_error: no agent data available (fetch error text).
    """
    m = load_page_module()
    m._inventory_package_names = lambda host_name: list(packages)
    sections = m._AgentSections(
        m._parse_agent_sections(agent_output),
        "test",
        agent_error,
        m._parse_piggyback_sections(agent_output),
    )
    catalog = m._generic_catalog()
    commands = [f"check_mk-{c}" for c in checks]
    by_host = {host: commands}
    for other, other_checks in (other_hosts or {}).items():
        by_host[other] = [f"check_mk-{c}" for c in other_checks]
    generic = m._generic_candidates(
        m._runtime_facts(sections.sections), commands, catalog, m._os_name_tokens(labels)
    )
    generic_monitored = m._generic_monitored_by_host([(host, labels)], by_host).get(host)
    result = m._analyze_host(
        host, labels, commands, m._available_plugin_map(), sections, generic, catalog, None, by_host,
        generic_monitored=generic_monitored,
    )
    return list(result.items)


def items_for(items: Sequence[Mapping[str, Any]], token: str) -> list[Mapping[str, Any]]:
    return [i for i in items if i.get("token") == token]


def kinds(items: Sequence[Mapping[str, Any]], token: str) -> list[str]:
    return [str(i.get("kind")) for i in items_for(items, token)]
