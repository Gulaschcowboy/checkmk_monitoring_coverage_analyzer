# Copyright (C) 2026 Alexander Wilms, Christian Wirtz
# SPDX-License-Identifier: GPL-2.0-only
"""Anonymization of the support data (lib/support_data.py)."""

from __future__ import annotations

import json
import unittest

from _harness import load_lib_module

sd = load_lib_module("support_data")

KEY = b"0" * 64


def raw_fixture() -> dict:
    hosts = {
        "db01.corp.example": {
            "site": "central", "address": "10.1.2.3",
            "labels": {"cmk/os_family": "windows", "cmk/site": "central", "owner": "team-db01"},
            "tags": {"agent": "cmk-agent", "location": "berlin"},
            "num_services": 42,
            "check_commands": ["check_mk-mssql_instance!x", "check_mk_active-cmk_inv", "check-mk-ping"],
            "services": [["MSSQL db01 Instance", "check_mk-mssql_instance!x", 0]],
            "mca": {
                "status": "WARN", "coverage_pct": 50, "monitored_count": 1, "total_count": 2,
                "items": [
                    {"kind": "open", "token": "iis", "title": "Microsoft IIS", "plugins": [],
                     "evidence": ["Windows service 'W3SVC' running"], "state": "running, not monitored",
                     "hint": "Deploy the plug-in on db01.corp.example"},
                    {"kind": "open", "token": "hyperv_host", "title": "Hyper-V", "plugins": [],
                     "evidence": ["piggyback data for: vm-secret-1"], "state": "x",
                     "hint": "Create the piggybacked host(s) in Checkmk: vm-secret-1"},
                    {"kind": "monitored", "token": "zfs", "title": "ZFS", "plugins": [],
                     "evidence": ["section 'zpool': 'tank-acme ONLINE'"], "state": "monitored"},
                ],
                "source_lines": ["Agent sections: 3 via remote get-agent-output @cached (site remote1)"],
            },
            "effective": {"status": "WARN", "coverage_pct": 50, "findings": "IIS on db01"},
            "agent": {
                "source": "remote get-agent-output @cached", "error": None,
                "sections": {"services": 120, "mssql_instance": 3},
                "piggyback": {"hyperv_vm_general": ["vm-secret-1"]},
                "windows_services": ["acme-billing running/auto ACME Billing", "W3SVC running/auto IIS"],
                "systemd_units": ["[all]", "acme-sync.service loaded active running Sync for db01",
                                  "[status]", "x"],
                "processes": ["acme-billing.exe"],
            },
        },
    }
    return {
        "meta": {
            "site": "central", "root": "/omd/sites/central", "server": "monitor-host",
            "server_fqdn": "monitor-host.corp.example", "generated_at": 1.0,
            "checkmk_version": "2.5.0p15", "mkp_version": "0.9.0-b74",
            "errors": [{"where": "agent", "error": "File /omd/sites/central/x: db01 unreachable via 10.1.2.3"}],
        },
        "site_ids": ["central", "remote1"],
        "all_host_names": ["db01.corp.example", "web02"],
        "all_addresses": ["10.1.2.3", "web02.corp.example"],
        "piggyback_targets": ["vm-secret-1"],
        "folders": ["customer-berlin"],
        "rules_file": {"sha256": "abc", "modified": False, "content": {"titles": {}}},
        "setup_rules": [{
            "folder": "customer-berlin", "disabled": False,
            "value": {"ignore": [{"subsystem": "^iis$", "comment": "ticket for db01"}]},
            "conditions": {"host_name": ["db01.corp.example", {"$regex": "^web"}],
                           "host_tags": {"location": "berlin", "agent": "cmk-agent"}},
        }],
        "hosts": hosts,
    }


REAL_NAMES = [
    "db01", "corp.example", "central", "remote1", "monitor-host", "vm-secret-1",
    "customer-berlin", "10.1.2.3", "web02", "berlin", "/omd/sites/central",
]


class AnonymizeTest(unittest.TestCase):
    def doc(self, with_runtime: bool = False) -> tuple[dict, dict, str]:
        doc, mapping = sd.anonymize(raw_fixture(), KEY, with_runtime)
        return doc, mapping, json.dumps(doc)

    def test_no_real_name_survives(self) -> None:
        for with_runtime in (False, True):
            _doc, _mapping, blob = self.doc(with_runtime)
            for name in REAL_NAMES:
                with self.subTest(with_runtime=with_runtime, name=name):
                    self.assertNotIn(name, blob)

    def test_mapping_resolves_pseudonyms(self) -> None:
        doc, mapping, blob = self.doc()
        [host] = doc["hosts"]
        self.assertEqual(mapping[host], "db01.corp.example")
        self.assertIn("vm-secret-1", mapping.values())
        self.assertEqual(mapping[doc["meta"]["site"]], "central")
        # short name and FQDN of a host get the same pseudonym, IPs their own
        self.assertIn(f"IIS on {host}", blob)
        self.assertIn("ip-", doc["meta"]["errors"][0]["error"])

    def test_pseudonyms_are_stable(self) -> None:
        first, _m, _b = self.doc()
        second, _m, _b = self.doc()
        self.assertEqual(list(first["hosts"]), list(second["hosts"]))
        other, _m = sd.anonymize(raw_fixture(), b"1" * 64, False)
        self.assertNotEqual(list(first["hosts"]), list(other["hosts"]))

    def test_kept_for_analysis(self) -> None:
        doc, _mapping, _blob = self.doc()
        [host] = doc["hosts"].values()
        self.assertEqual(host["check_plugins"], ["active:cmk_inv", "mssql_instance", "other"])
        self.assertEqual(host["labels"]["cmk/os_family"], "windows")
        self.assertEqual(host["agent"]["sections"], {"services": 120, "mssql_instance": 3})
        self.assertEqual(host["mca"]["items"][0]["token"], "iis")
        self.assertEqual(doc["meta"]["checkmk_version"], "2.5.0p15")

    def test_dropped(self) -> None:
        doc, _mapping, blob = self.doc()
        [host] = doc["hosts"].values()
        self.assertNotIn("owner", host["labels"])
        self.assertEqual(host["custom_labels"], 1)
        self.assertEqual(host["tags"], {"agent": "cmk-agent"})
        self.assertEqual(host["custom_tag_groups"], 1)
        self.assertNotIn("services", host)  # no service descriptions
        self.assertNotIn("MSSQL db01 Instance", blob)
        self.assertNotIn("ticket", blob)  # rule comment
        self.assertNotIn("content", doc["rules_file"])  # unmodified rules file

    def test_runtime_names_only_with_consent(self) -> None:
        _doc, _mapping, blob = self.doc(with_runtime=False)
        self.assertNotIn("acme-billing", blob)
        self.assertNotIn("W3SVC", blob)  # evidence quotes are masked, too
        self.assertNotIn("tank-acme", blob)
        doc, _mapping, blob = self.doc(with_runtime=True)
        self.assertIn("acme-billing.exe", blob)
        self.assertIn("Windows service 'W3SVC' running", blob)
        [host] = doc["hosts"].values()
        # systemd: unit name and state only, no description
        self.assertEqual(host["agent"]["systemd_units"], ["acme-sync.service active running"])

    def test_rule_conditions_reduced_to_shape(self) -> None:
        doc, _mapping, _blob = self.doc()
        [rule] = doc["setup_rules"]
        self.assertEqual(rule["conditions"]["host_name"], {"count": 2, "regex": 1})
        self.assertEqual(rule["value"]["ignore"][0]["subsystem"], "^iis$")
        self.assertTrue(rule["folder"].startswith("folder-"))

    def test_host_named_like_a_plugin(self) -> None:
        raw = raw_fixture()
        raw["all_host_names"].append("mssql_instance")
        raw["all_host_names"].append("iis")
        doc, _mapping = sd.anonymize(raw, KEY, False)
        [host] = doc["hosts"].values()
        self.assertEqual(host["mca"]["items"][0]["token"], "iis")
        self.assertIn("mssql_instance", host["check_plugins"])
        self.assertIn("mssql_instance", host["agent"]["sections"])

    def test_names_in_label_keys_and_unknown_fqdns(self) -> None:
        raw = raw_fixture()
        host = raw["hosts"]["db01.corp.example"]
        host["labels"]["cmk/piggyback_source_hv7.lan.other-corp.de"] = "yes"
        host["mca"]["source_lines"].append("see backup.other-corp.de and Microsoft.Azure.Monitor")
        doc, _mapping = sd.anonymize(raw, KEY, False)
        blob = json.dumps(doc)
        self.assertNotIn("other-corp", blob)
        self.assertNotIn("hv7", blob)
        self.assertIn("Microsoft.Azure.Monitor", blob)  # not a host name

    def test_residual_check(self) -> None:
        self.assertEqual(sd.residual_findings({"a": "host-1a2b3c4d", "b": "mk_inventory.ps1"}), [])
        self.assertEqual(sd.residual_findings({"a": "dns"}, ["dns"], ignore=["dns"]), [])
        found = sd.residual_findings({"x": ["see srv9.other-corp.example and 192.168.7.7"]}, ["leaked-host"])
        self.assertEqual(len(found), 2)
        self.assertEqual(sd.residual_findings({"x": "on leaked-host"}, ["leaked-host"]), ["known name: leaked-host"])


class SummaryTest(unittest.TestCase):
    def test_resolve_matches_the_document(self) -> None:
        doc, _mapping = sd.anonymize(raw_fixture(), KEY, False)
        pseudonym = next(iter(doc["hosts"]))
        all_hosts = raw_fixture()["all_host_names"]
        self.assertEqual(sd.resolve_hosts(KEY, all_hosts, [pseudonym.upper()]),
                         {pseudonym: ["db01.corp.example"]})
        self.assertEqual(sd.resolve_hosts(KEY, all_hosts, ["host-00000000"]), {"host-00000000": []})
        everything = sd.resolve_hosts(KEY, all_hosts, [])
        self.assertEqual(sorted(n for names in everything.values() for n in names), sorted(all_hosts))
        self.assertTrue(all(p.startswith("host-") for p in everything))
        # The site's own server, monitored as a host too, is a "server-..."
        raw = raw_fixture()
        raw["all_host_names"].append("monitor-host.corp.example")
        raw["hosts"]["monitor-host.corp.example"] = raw["hosts"]["db01.corp.example"]
        server_pseudonym = [h for h in sd.anonymize(raw, KEY, False)[0]["hosts"] if h.startswith("server-")]
        self.assertEqual(len(server_pseudonym), 1)
        self.assertEqual(sd.resolve_hosts(KEY, raw["all_host_names"], server_pseudonym),
                         {server_pseudonym[0]: ["monitor-host.corp.example"]})
        self.assertTrue(sd.is_host_pseudonym("host-1a2b3c4d"))
        self.assertTrue(sd.is_host_pseudonym("server-1a2b3c4d"))
        self.assertFalse(sd.is_host_pseudonym("folder-1a2b3c4d"))

    def test_summary_lists_content_and_residuals(self) -> None:
        doc, _mapping = sd.anonymize(raw_fixture(), KEY, False)
        text = "\n".join(sd.summary(doc, ["known name: db01"]))
        self.assertIn("Hosts:            1 (pseudonymized, e.g. host-", text)
        self.assertIn("of 2 on the site", text)
        self.assertIn("mssql_instance", text)
        self.assertIn("Service/process names: not included", text)
        self.assertIn("checksum only (unmodified)", text)
        self.assertIn("  - known name: db01", text)
        self.assertNotIn("db01.corp.example", text)

    def test_single_host_is_marked(self) -> None:
        raw = raw_fixture()
        raw["meta"]["single_host"] = True
        doc, _mapping = sd.anonymize(raw, KEY, False)
        self.assertTrue(doc["meta"]["single_host"])
        self.assertIn("- single host", "\n".join(sd.summary(doc)))


try:
    import cryptography  # noqa: F401  (shipped with Checkmk, optional here)

    HAVE_CRYPTO = True
except ImportError:
    HAVE_CRYPTO = False


@unittest.skipUnless(HAVE_CRYPTO, "Python package 'cryptography' not available")
class TransportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.t = load_lib_module("transport")
        cls.private, cls.public = cls.t.generate_keypair()

    def test_roundtrip_and_not_readable(self) -> None:
        doc, _mapping = sd.anonymize(raw_fixture(), KEY, False)
        blob = self.t.encrypt(doc, self.public)
        self.assertTrue(blob.startswith(self.t.MAGIC))
        self.assertNotIn(b"mssql_instance", blob)
        self.assertEqual(self.t.decrypt(blob, self.private), json.loads(json.dumps(doc, sort_keys=True)))

    def test_wrong_key_and_tampering_fail(self) -> None:
        blob = self.t.encrypt({"a": 1}, self.public)
        other_private, _other_public = self.t.generate_keypair()
        with self.assertRaises(self.t.TransportError):
            self.t.decrypt(blob, other_private)
        broken = blob[:-1] + bytes([blob[-1] ^ 1])
        with self.assertRaises(self.t.TransportError):
            self.t.decrypt(broken, self.private)
        with self.assertRaises(self.t.TransportError):
            self.t.decrypt(b"something else", self.private)

    def test_key_kinds_are_not_mixed_up(self) -> None:
        with self.assertRaises(self.t.TransportError):
            self.t.encrypt({"a": 1}, self.private)


if __name__ == "__main__":
    unittest.main()
