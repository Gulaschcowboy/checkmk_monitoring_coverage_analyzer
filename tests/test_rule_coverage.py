"""Every curated rule of the rules file has a test case.

CASES holds, per detect token, synthetic agent output (and host labels)
that must be detected as an open finding. For each case the test checks:
  - detected as "open" without a matching check plug-in,
  - "monitored" once a check plug-in of the token runs on the host,
  - not detected on Windows for the rules in NOT_ON_WINDOWS.
EXCLUDED holds, per rule with an "unless" condition, output that matches
the rule but must not be detected.

test_every_rule_has_a_case fails as soon as the rules file gets a rule
without a case here - add one when adding a rule.
"""

from __future__ import annotations

import unittest
from typing import Any

from _harness import (
    LINUX,
    WINDOWS_CLIENT,
    WINDOWS_SERVER,
    analyze,
    items_for,
    kinds,
    load_page_module,
    plugin_for,
    processes,
    systemd_units,
    win_services,
)

W = WINDOWS_SERVER

# token -> (agent output, labels)
# Entry: (agent output, host labels[, installed packages])
CASES: dict[str, tuple[Any, ...]] = {
    "zfs": ("<<<zpool>>>\ntank ONLINE\n", LINUX),
    "lvm": ("<<<diskstat>>>\n[dmsetup_info]\nvg0-root 253:0 vg0 root\n", LINUX),
    "md_raid": ("<<<md>>>\nmd0 : active raid1 sda1[0] sdb1[1]\n", LINUX),
    "mysql": (systemd_units("mariadb.service"), LINUX),
    "postgres": (systemd_units("postgresql@16-main.service"), LINUX),
    "apache": (systemd_units("apache2.service"), LINUX),
    "nginx": (systemd_units("nginx.service"), LINUX),
    "sshd": (systemd_units("ssh.service"), LINUX),
    # Tool without a running service: the installed package is the evidence.
    "apt": ("", LINUX, ("apt", "apt-utils")),
    "redis": (systemd_units("redis-server.service"), LINUX),
    "mongodb": (systemd_units("mongod.service"), LINUX),
    "docker": (systemd_units("docker.service"), LINUX),
    "rabbitmq": (systemd_units("rabbitmq-server.service"), LINUX),
    "elasticsearch": (systemd_units("elasticsearch.service"), LINUX),
    "graylog": (systemd_units("graylog-server.service"), LINUX),
    "haproxy": (systemd_units("haproxy.service"), LINUX),
    "oracle": (processes("ora_pmon_ORCL"), LINUX),
    "oracle_crs": (processes("ohasd.bin"), LINUX),
    "powerdns": (systemd_units("pdns-recursor.service"), LINUX),
    "cups": (systemd_units("cups.service"), LINUX),
    "openvpn": (systemd_units("openvpn-server@site1.service"), LINUX),
    "nut": (systemd_units("nut-server.service"), LINUX),
    "unifi": (systemd_units("unifi.service"), LINUX),
    "mssql": (win_services("MSSQLSERVER"), W),
    "iis": (win_services("W3SVC"), W),
    "msexch": (win_services("MSExchangeTransport"), W),
    "veeam": (win_services("VeeamBackupSvc"), W),
    "citrix": (win_services("CitrixBrokerService"), W),
    "ibm_mq": (processes("amqzxma0"), LINUX),
    "domino": (systemd_units("domino.service"), LINUX),
    "dell_om": (systemd_units("dsm_sa_datamgrd.service"), LINUX),
    "smart": (systemd_units("smartd.service"), LINUX),
    "sap": (processes("disp+work"), LINUX),
    "sap_hana": (processes("hdbnameserver"), LINUX),
    "saprouter": (processes("saprouter"), LINUX),
    "entra_connect": (win_services("ADSync"), W),
    "citrix_vda": (win_services("BrokerAgent"), W),
    "esx": (win_services("VMTools"), W),
    "hyperv_host": (win_services("vmms"), W),
    "hyperv_vm": (win_services("vmicheartbeat"), W),
}

# token -> (agent output, labels) that matches the rule but hits "unless"
EXCLUDED: dict[str, tuple[str, dict[str, str]]] = {
    "smart": (systemd_units("smartd.service", "qemu-guest-agent.service"), LINUX),
    "hyperv_host": (win_services("vmms"), WINDOWS_CLIENT),
    "hyperv_vm": (win_services("vmicheartbeat", "WindowsAzureGuestAgent"), W),
}

# Rules whose detection is not "running service -> monitored by own check":
# the monitored state is covered in test_detection.py instead.
MONITORED_ELSEWHERE = {"entra_connect"}

# Rules that must not fire on Windows (rules file: "not_on_os": ["windows"]).
# Fixed here on purpose, so that a dropped "not_on_os" is noticed.
NOT_ON_WINDOWS = {"smart", "sap_hana", "saprouter", "oracle_crs", "openvpn", "ibm_mq", "sshd", "apt"}


def _case(token: str) -> tuple[str, dict[str, str], tuple[str, ...]]:
    """CASES entry as (agent output, labels, installed packages)."""
    output, labels, *packages = CASES[token]
    return output, labels, tuple(packages[0]) if packages else ()


def _cases() -> list[tuple[str, str, dict[str, str], tuple[str, ...]]]:
    return [(token, *_case(token)) for token in CASES]


class RuleCoverageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.detect = load_page_module().DETECT

    def test_every_rule_has_a_case(self) -> None:
        self.assertEqual(sorted(set(self.detect) - set(CASES)), [], "rules without a test case")
        self.assertEqual(sorted(set(CASES) - set(self.detect)), [], "test cases without a rule")

    def test_every_unless_rule_has_an_excluded_case(self) -> None:
        with_unless = {t for t, spec in self.detect.items() if spec.get("unless")}
        self.assertEqual(sorted(with_unless - set(EXCLUDED)), [])

    def test_detected_as_open(self) -> None:
        for token, output, labels, packages in _cases():
            with self.subTest(token=token):
                self.assertEqual(kinds(analyze(output, labels=labels, packages=packages), token), ["open"])

    def test_monitored_with_own_check(self) -> None:
        for token, output, labels, packages in _cases():
            if token in MONITORED_ELSEWHERE:
                continue
            with self.subTest(token=token):
                items = analyze(output, labels=labels, packages=packages, checks=[plugin_for(token)])
                self.assertEqual(kinds(items, token), ["monitored"])

    def test_not_on_windows(self) -> None:
        declared = {t for t, spec in self.detect.items() if "windows" in spec.get("not_on_os", [])}
        self.assertEqual(sorted(declared ^ NOT_ON_WINDOWS), [], "not_on_os differs from NOT_ON_WINDOWS")
        for token in sorted(NOT_ON_WINDOWS & set(CASES)):
            output, _labels, packages = _case(token)
            with self.subTest(token=token):
                self.assertEqual(kinds(analyze(output, labels=W, packages=packages), token), [])

    def test_excluded(self) -> None:
        for token, (output, labels) in EXCLUDED.items():
            with self.subTest(token=token):
                self.assertEqual(kinds(analyze(output, labels=labels), token), [])

    def test_sshd_config_check_counts_as_monitored(self) -> None:
        # The real check plug-in name differs from the token.
        items = analyze(systemd_units("ssh.service"), labels=LINUX, checks=["sshd_config"])
        self.assertEqual(kinds(items, "sshd"), ["monitored"])

    def test_package_rule(self) -> None:
        # Only the exact package counts, not e.g. "apt-utils" alone.
        self.assertEqual(kinds(analyze("", packages=["apt-utils"]), "apt"), [])
        item = items_for(analyze("", packages=["apt"]), "apt")[0]
        self.assertEqual(item["state"], "installed, not monitored")
        self.assertIn("mk_apt", item["hint"])
        # Package evidence also counts without current agent data.
        self.assertEqual(kinds(analyze("", packages=["apt"], agent_error="no route to host"), "apt"), ["open"])

    def test_unrelated_host_has_no_curated_finding(self) -> None:
        output = systemd_units("cron.service", "dbus.service") + processes("bash")
        items = analyze(output, labels=LINUX)
        self.assertEqual([i["token"] for i in items if i["token"] in self.detect], [])


if __name__ == "__main__":
    unittest.main()
