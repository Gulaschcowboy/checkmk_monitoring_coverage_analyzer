"""Detection of curated subsystems on synthetic hosts.

Each case states which finding is expected for a given set of running
services/processes, labels and already monitored check plug-ins:
  open       - detected, not monitored (WARN)
  monitored  - detected and monitored
  []         - not detected at all (no evidence)
"""

from __future__ import annotations

import unittest

from _harness import (
    LINUX,
    WINDOWS_CLIENT,
    WINDOWS_SERVER,
    analyze,
    items_for,
    kinds,
    load_page_module,
    processes,
    systemd_units,
    win_services,
)

SMARTD = systemd_units("smartd.service")


class SmartTest(unittest.TestCase):
    def test_bare_metal_is_open(self) -> None:
        self.assertEqual(kinds(analyze(SMARTD), "smart"), ["open"])

    def test_kvm_guest_is_ignored(self) -> None:
        output = SMARTD + systemd_units("qemu-guest-agent.service")
        self.assertEqual(kinds(analyze(output), "smart"), [])

    def test_vmware_guest_via_vsphere_piggyback_is_ignored(self) -> None:
        output = SMARTD + "<<<esx_vsphere_vm>>>\nconfig.hardware.numCPU 2\n"
        self.assertEqual(kinds(analyze(output), "smart"), [])

    def test_vm_with_smart_monitored_stays_monitored(self) -> None:
        output = SMARTD + systemd_units("open-vm-tools.service")
        self.assertEqual(kinds(analyze(output, checks=["smart_posix_temp"]), "smart"), ["monitored"])

    def test_not_on_windows(self) -> None:
        self.assertEqual(kinds(analyze(processes("smartd"), labels=WINDOWS_SERVER), "smart"), [])


class HyperVTest(unittest.TestCase):
    PLUGIN = (
        "<<<checkmk_agent_plugins_win:sep(0)>>>\n"
        "pluginsdir C:\\ProgramData\\checkmk\\agent\\plugins\n"
        'C:\\ProgramData\\checkmk\\agent\\plugins\\hyperv_host.ps1:CMK_VERSION="2.5.0"\n'
    )
    PIGGYBACK = (
        "<<<hyperv_node>>>\nvms.defined 2\n"
        "<<<<vm-1>>>>\n<<<hyperv_vm_general>>>\nname vm-1\n<<<<>>>>\n"
        "<<<<vm-2>>>>\n<<<hyperv_vm_general>>>\nname vm-2\n<<<<>>>>\n"
    )

    def test_host_without_plugin_is_open(self) -> None:
        items = analyze(win_services("vmms"), labels=WINDOWS_SERVER)
        self.assertEqual(kinds(items, "hyperv_host"), ["open"])

    def test_windows_client_is_ignored(self) -> None:
        items = analyze(win_services("vmms"), labels=WINDOWS_CLIENT)
        self.assertEqual(kinds(items, "hyperv_host"), [])

    def test_all_vms_monitored(self) -> None:
        items = analyze(
            win_services("vmms") + self.PLUGIN + self.PIGGYBACK,
            labels=WINDOWS_SERVER,
            other_hosts={"vm-1": ["hyperv_vm_general"], "vm-2": ["hyperv_vm_ram"]},
        )
        self.assertEqual(kinds(items, "hyperv_host"), ["monitored"])

    def test_missing_vm_host_is_named(self) -> None:
        items = analyze(
            win_services("vmms") + self.PLUGIN + self.PIGGYBACK,
            labels=WINDOWS_SERVER,
            other_hosts={"vm-1": ["hyperv_vm_general"]},
        )
        [item] = items_for(items, "hyperv_host")
        self.assertEqual(item["kind"], "open")
        self.assertIn("vm-2", item["hint"])
        self.assertNotIn("vm-1", item["hint"])

    def test_legacy_check_counts_as_monitored(self) -> None:
        items = analyze(win_services("vmms"), labels=WINDOWS_SERVER, checks=["hyperv_vms"])
        self.assertEqual(kinds(items, "hyperv_host"), ["monitored"])

    def test_windows_guest(self) -> None:
        self.assertEqual(
            kinds(analyze(win_services("vmicheartbeat"), labels=WINDOWS_SERVER), "hyperv_vm"), ["open"]
        )
        items = analyze(win_services("vmicheartbeat"), labels=WINDOWS_SERVER, checks=["hyperv_vm_general"])
        self.assertEqual(kinds(items, "hyperv_vm"), ["monitored"])

    def test_linux_guest(self) -> None:
        self.assertEqual(kinds(analyze(systemd_units("hv-kvp-daemon.service")), "hyperv_vm"), ["open"])

    def test_azure_vm_is_ignored(self) -> None:
        items = analyze(win_services("vmicheartbeat", "WindowsAzureGuestAgent"), labels=WINDOWS_SERVER)
        self.assertEqual(kinds(items, "hyperv_vm"), [])


class SapTest(unittest.TestCase):
    def test_abap_dispatcher(self) -> None:
        self.assertEqual(kinds(analyze(processes("disp+work.exe"), labels=WINDOWS_SERVER), "sap"), ["open"])
        self.assertEqual(kinds(analyze(processes("disp+work"), checks=["sap_state"]), "sap"), ["monitored"])

    def test_side_services_are_no_evidence(self) -> None:
        output = win_services(
            "SAPHostControl", "SAPHostExec", "SAPSprint", "SAPB1Server", "B1LicenseService",
            "SAPCloudConnector",
        )
        items = analyze(output, labels=WINDOWS_SERVER)
        self.assertEqual([i["token"] for i in items if "sap" in i["token"].lower()], [])

    def test_hana_linux_only(self) -> None:
        self.assertEqual(kinds(analyze(processes("hdbnameserver")), "sap_hana"), ["open"])
        self.assertEqual(kinds(analyze(processes("hdbnameserver"), labels=WINDOWS_SERVER), "sap_hana"), [])

    def test_saprouter(self) -> None:
        self.assertEqual(kinds(analyze(processes("saprouter")), "saprouter"), ["open"])


class GraylogTest(unittest.TestCase):
    def test_server(self) -> None:
        self.assertEqual(kinds(analyze(systemd_units("graylog-server.service")), "graylog"), ["open"])

    def test_sidecar_is_no_evidence(self) -> None:
        self.assertEqual(kinds(analyze(systemd_units("graylog-sidecar.service")), "graylog"), [])


class MssqlTest(unittest.TestCase):
    def test_named_instance(self) -> None:
        items = analyze(win_services("MSSQL$INSTANCE1"), labels=WINDOWS_SERVER)
        self.assertEqual(kinds(items, "mssql"), ["open"])

    def test_internal_database_is_ignored(self) -> None:
        items = analyze(win_services("MSSQL$MICROSOFT##WID"), labels=WINDOWS_SERVER)
        self.assertEqual(kinds(items, "mssql"), [])

    def test_monitored(self) -> None:
        items = analyze(win_services("MSSQLSERVER"), labels=WINDOWS_SERVER, checks=["mssql_counters_locks"])
        self.assertEqual(kinds(items, "mssql"), ["monitored"])


class EntraConnectTest(unittest.TestCase):
    def test_open_without_tenant_monitoring(self) -> None:
        items = analyze(win_services("ADSync"), labels=WINDOWS_SERVER)
        self.assertEqual(kinds(items, "entra_connect"), ["open"])

    def test_monitored_on_tenant_host(self) -> None:
        items = analyze(
            win_services("ADSync"), labels=WINDOWS_SERVER, other_hosts={"tenant-1": ["azure_ad_sync"]}
        )
        [item] = items_for(items, "entra_connect")
        self.assertEqual(item["kind"], "monitored")
        self.assertIn("tenant-1", item["state"])


class MysqlTest(unittest.TestCase):
    HEADER = "<<<ps_lnx>>>\n[processes]\n[header] CGROUP USER VSZ RSS TIME ELAPSED PID COMMAND\n"

    def test_host_process(self) -> None:
        output = self.HEADER + "0::/system.slice/mysql.service mysql 1 1 00:00:01 00:00:01 1 /usr/sbin/mysqld\n"
        self.assertEqual(kinds(analyze(output), "mysql"), ["open"])

    def test_container_process_is_ignored(self) -> None:
        output = self.HEADER + "0::/system.slice/docker-1a2b.scope mysql 1 1 00:00:01 00:00:01 1 /usr/sbin/mysqld\n"
        self.assertEqual(kinds(analyze(output), "mysql"), [])


class SectionDataTest(unittest.TestCase):
    def test_empty_zpool_is_no_evidence(self) -> None:
        self.assertEqual(kinds(analyze("<<<zpool>>>\nno pools available\n"), "zfs"), [])


class NoAgentDataTest(unittest.TestCase):
    def test_host_without_agent_output_has_no_open_findings(self) -> None:
        self.assertEqual([i for i in analyze("", labels=LINUX) if i["kind"] == "open"], [])


class UnknownMonitoredCheckTest(unittest.TestCase):
    """Running checks of a family the rules file does not know (e.g. from
    an MKP) are listed as monitored, but not counted."""

    def test_listed_as_monitored_generic(self) -> None:
        items = analyze("", checks=["acmecloud_info", "acmecloud_users", "df", "cpu_loads"])
        [item] = items_for(items, "generic:acmecloud")
        self.assertEqual(item["kind"], "monitored_generic")
        self.assertEqual(item["title"], "ACME Cloud")
        self.assertEqual(item["plugins"], ["acmecloud_info", "acmecloud_users"])
        # base checks (stop tokens) are not listed
        self.assertEqual(items_for(items, "generic:df") + items_for(items, "generic:cpu"), [])

    def test_agent_builtin_checks_are_not_listed(self) -> None:
        items = analyze("", checks=["timesyncd", "postfix_mailq", "mknotifyd", "mkbackup"])
        self.assertEqual([i for i in items if i["kind"] == "monitored_generic"], [])
        items = analyze("", labels=WINDOWS_SERVER,
                        checks=["winperf_processor_util", "systemtime", "windows_updates"])
        self.assertEqual([i for i in items if i["kind"] == "monitored_generic"], [])

    def test_known_family_is_not_listed_twice(self) -> None:
        items = analyze(systemd_units("mariadb.service"), checks=["mysql_capacity"])
        self.assertEqual(kinds(items, "mysql"), ["monitored"])
        self.assertEqual([i for i in items if i["kind"] == "monitored_generic"], [])

    def test_common_family_on_all_hosts_of_an_os_is_dropped(self) -> None:
        m = load_page_module()
        rows = [(f"h{n}", LINUX) for n in range(5)]
        commands = {h: ["check_mk-acmecloud_info"] for h, _l in rows}
        self.assertEqual(m._generic_monitored_by_host(rows, commands)["h0"], {})
        commands["h0"].append("check_mk-nfsmounts")
        self.assertEqual(m._generic_monitored_by_host(rows, commands)["h0"], {"nfsmounts": ["nfsmounts"]})


if __name__ == "__main__":
    unittest.main()
