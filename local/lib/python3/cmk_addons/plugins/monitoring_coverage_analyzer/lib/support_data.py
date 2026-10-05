#!/usr/bin/env python3
# Copyright (C) 2026 Alexander Wilms, Christian Wirtz
# SPDX-License-Identifier: GPL-2.0-only
"""Support data for bug reports ("mcactl support-data").

collect_raw() gathers the data in the GUI context (real names), anonymize()
turns it into the document that may be sent: host, site, folder and server
names, IP addresses and domains become stable pseudonyms (HMAC with a key
that stays on the site), free texts are scrubbed, and everything not needed
for the analysis is dropped. The mapping pseudonym -> real name is returned
separately and is meant to stay with the user.

anonymize(), Pseudonymizer and residual_findings() do not need Checkmk and
are covered by the unit tests.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import re
import secrets
import tarfile
import time
import traceback
from collections.abc import Iterable, Mapping
from typing import Any

PKG = "monitoring_coverage_analyzer"
FORMAT = 1

# Built-in host tag groups of Checkmk (fallback if the GUI does not provide
# them). Custom tag groups often carry location or customer names.
BUILTIN_TAG_GROUPS = frozenset({
    "address_family", "agent", "piggyback", "snmp_ds", "ip-v4", "ip-v6",
    "checkmk-agent", "tcp", "snmp", "ping", "site",
})

# Built-in labels kept in the anonymized document (values are scrubbed).
_KEEP_LABEL_PREFIX = "cmk/"

_IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_IPV6_RE = re.compile(r"(?<![\w:])(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}(?![\w:])")
# Something that looks like a host or domain name: at least three labels,
# last label alphabetic (excludes file names like "mk_inventory.ps1").
_FQDN_RE = re.compile(r"(?<![A-Za-z0-9.-])(?:[A-Za-z0-9-]+\.){2,}[A-Za-z]{2,24}(?![A-Za-z0-9-])")
# Last labels that make a dotted name a host/domain name (and not e.g. a
# .NET or Java style service name like "Microsoft.Azure.Monitor").
_DOMAIN_SUFFIXES = frozenset({
    "com", "org", "net", "edu", "gov", "int", "info", "biz", "io", "eu", "de", "at", "ch",
    "fr", "it", "nl", "be", "lu", "uk", "us", "es", "pl", "cz", "se", "no", "dk", "fi",
    "local", "lan", "intern", "internal", "intra", "corp", "home", "localdomain", "domain",
    "ad", "priv", "private", "test", "example", "invalid", "arpa", "cloud",
})
# Candidate tokens for known names (host names may contain dots,
# underscores and dashes); looked up in a dict instead of one huge regex.
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*[A-Za-z0-9]|[A-Za-z0-9]")
_SAFE_FQDN_RE = re.compile(r"\.(py|ps1|sh|exe|vbs|bat|cmd|pl|json|mk|cfg|conf|service|socket|timer|log|dll)$", re.I)


# ---------------------------------------------------------------------------
# Pseudonyms
# ---------------------------------------------------------------------------


def load_or_create_key(path: str) -> bytes:
    """Site-local secret for stable pseudonyms; never part of the output."""
    try:
        with open(path, encoding="utf-8") as handle:
            key = handle.read().strip()
        if len(key) >= 32:
            return key.encode()
    except OSError:
        pass
    key = secrets.token_hex(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(key + "\n")
    return key.encode()


class Pseudonymizer:
    """Replaces known names in values and free texts by stable pseudonyms."""

    def __init__(self, key: bytes) -> None:
        self._key = key
        self._known: dict[str, tuple[str, str]] = {}  # lower(name) -> (kind, real)
        self.mapping: dict[str, str] = {}  # pseudonym -> real name (used ones)
        # Technical identifiers (section, plug-in, token names) that are
        # never replaced, even if a host has the same name ("dns").
        self.protected: set[str] = set()

    def pseudonym(self, kind: str, value: str) -> str:
        digest = hmac.new(self._key, f"{kind}:{value.lower()}".encode(), hashlib.sha256).hexdigest()
        name = f"{kind}-{digest[:8]}"
        self.mapping.setdefault(name, value)
        return name

    def add(self, kind: str, names: Iterable[str]) -> None:
        """Registers real names. Addresses: IPs become "ip-...", DNS names
        are treated as host names. An FQDN also registers its short name
        (same pseudonym as the FQDN) and its domain."""
        for name in names:
            name = str(name or "").strip()
            if len(name) < 2:
                continue
            k = kind
            if kind == "address":
                k = "ip" if _IPV4_RE.fullmatch(name) or _IPV6_RE.fullmatch(name) else "host"
            self._register(name, k, name)
            if k in ("host", "server") and "." in name:
                short, _sep, domain = name.partition(".")
                self._register(short, k, name)
                if "." in domain:
                    self._register(domain, "domain", domain)

    def _register(self, name: str, kind: str, real: str) -> None:
        self._known.setdefault(name.lower(), (kind, real))

    def known_names(self) -> list[str]:
        return [real for _kind, real in self._known.values()]

    def name(self, kind: str, value: str) -> str:
        """Pseudonym for a value that is itself a name (e.g. a host name)."""
        known = self._known.get(str(value).lower())
        return self.pseudonym(known[0] if known else kind, str(value))

    def text(self, value: object) -> str:
        """Free text: host/domain names, known names and IP addresses replaced."""
        text = str(value)
        text = _FQDN_RE.sub(self._sub_fqdn, text)
        if self._known:
            text = _TOKEN_RE.sub(self._sub_token, text)
        text = _IPV4_RE.sub(lambda m: self.pseudonym("ip", m.group(0)), text)
        text = _IPV6_RE.sub(lambda m: self.pseudonym("ip", m.group(0)), text)
        return text

    def _sub_fqdn(self, match: re.Match[str]) -> str:
        name = match.group(0)
        known = self._sub_known(name)
        if known is not None:
            return known
        labels = name.split(".")
        if labels[-1].lower() not in _DOMAIN_SUFFIXES or _SAFE_FQDN_RE.search(name):
            return name
        short = self._sub_known(labels[0])
        if short is not None:
            return short + "." + self.pseudonym("domain", ".".join(labels[1:]))
        return self.pseudonym("host", name)

    def _sub_known(self, found: str) -> str | None:
        if found.lower() in self.protected:
            return None
        known = self._known.get(found.lower())
        if known is None:
            return None
        kind, real = known
        return self.pseudonym(kind, real)

    def _sub_token(self, match: re.Match[str]) -> str:
        token = match.group(0)
        whole = self._sub_known(token)
        if whole is not None:
            return whole
        if "." not in token and "_" not in token:
            return token
        # Known names as part of a longer token, e.g. "<host>.<domain>",
        # "agent.<host>.<host>_Pool": try the longest dotted prefix/suffix,
        # then every part between dots and underscores.
        labels = token.split(".")
        for cut in range(len(labels) - 1, 0, -1):
            head = self._sub_known(".".join(labels[:cut]))
            if head is not None:
                tail = ".".join(labels[cut:])
                return head + "." + (self._sub_known(tail) or self._sub_parts(tail))
        for cut in range(1, len(labels)):
            tail = self._sub_known(".".join(labels[cut:]))
            if tail is not None:
                return self._sub_parts(".".join(labels[:cut])) + "." + tail
        return self._sub_parts(token)

    def _sub_parts(self, token: str) -> str:
        return "".join(
            (self._sub_known(part) or part) if len(part) >= 4 else part
            for part in re.split(r"([._])", token)
        )

    def deep(self, value: Any) -> Any:
        """Applies text() to all strings of a JSON-like structure."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, Mapping):
            return {self.text(k): self.deep(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set, frozenset)):
            return [self.deep(v) for v in value]
        return value


# ---------------------------------------------------------------------------
# Anonymization
# ---------------------------------------------------------------------------


def _check_plugin(check_command: str) -> str:
    """Check plug-in name of a Livestatus check_command, no arguments."""
    head = str(check_command).split("!")[0]
    if head.startswith("check_mk-"):
        return head[len("check_mk-"):]
    if head.startswith("check_mk_active-"):
        return "active:" + head[len("check_mk_active-"):]
    return "other"


# Evidence texts of the page that quote a service/process name or a data
# line of the agent output (see _match_condition() of the page).
_RUNTIME_EVIDENCE_RE = re.compile(
    r"^(Windows service|systemd unit|process) '(.*)'( running)?$|^(section '[^']*'): '(.*)'$"
)


def _mask_runtime_evidence(items: list[dict[str, Any]], ps: Pseudonymizer) -> list[dict[str, Any]]:
    """Without runtime names: replace quoted service/process names and agent
    data lines in the evidence by pseudonyms (the same name gets the same
    pseudonym, so matches stay comparable)."""
    out = []
    for item in items:
        evidence = []
        for text in item.get("evidence") or []:
            m = _RUNTIME_EVIDENCE_RE.match(str(text))
            if m and m.group(1):
                text = f"{m.group(1)} '{ps.pseudonym('name', m.group(2))}'{m.group(3) or ''}"
            elif m and m.group(4):
                text = f"{m.group(4)}: '{ps.pseudonym('line', m.group(5))}'"
            evidence.append(text)
        out.append({**item, "evidence": evidence})
    return out


def _without_comments(value: Any) -> Any:
    """Rule values without free-text comments (may contain anything)."""
    if isinstance(value, Mapping):
        return {k: ("<removed>" if k == "comment" else _without_comments(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_without_comments(v) for v in value]
    return value


def _systemd_unit_states(lines: Iterable[str]) -> list[str]:
    """systemd_units section reduced to '<unit> <active> <sub>' of the
    [all] subsection - descriptions and the status details are dropped."""
    out: list[str] = []
    current = ""
    for line in lines:
        line = str(line).strip()
        if line.startswith("[") and line.endswith("]"):
            current = line
            continue
        parts = line.split()
        if current == "[all]" and len(parts) >= 4:
            out.append(" ".join(parts[:1] + parts[2:4]))
    return out


def _rule_conditions_summary(conditions: Mapping[str, Any], ps: Pseudonymizer) -> dict[str, Any]:
    """Shape of a rule condition without names: counts only."""
    out: dict[str, Any] = {}
    host_name = conditions.get("host_name")
    if isinstance(host_name, Mapping) and "$nor" in host_name:
        out["host_name"] = {"negated": True, "count": len(host_name["$nor"] or [])}
    elif isinstance(host_name, list):
        out["host_name"] = {
            "count": len(host_name),
            "regex": sum(1 for h in host_name if isinstance(h, Mapping)),
        }
    if conditions.get("host_tags"):
        tags = conditions["host_tags"]
        out["host_tags"] = sorted(
            g if g in BUILTIN_TAG_GROUPS else ps.pseudonym("taggroup", g) for g in tags
        )
    if conditions.get("host_label_groups"):
        out["host_label_groups"] = len(conditions["host_label_groups"])
    if conditions.get("service_description"):
        out["service_description"] = True
    return out


_ITEM_STRUCTURAL = ("kind", "token", "title", "plugins")


def _mca(mca: Mapping[str, Any] | None, ps: Pseudonymizer, with_runtime: bool) -> Any:
    if mca is None:
        return None
    items = list(mca.get("items") or [])
    if not with_runtime:
        items = _mask_runtime_evidence(items, ps)
    # kind/token/title/plugins are names of the rules file or of check
    # plug-ins, never host data: kept as they are.
    items = [
        {k: (v if k in _ITEM_STRUCTURAL else ps.deep(v)) for k, v in item.items()} for item in items
    ]
    return {**ps.deep({k: v for k, v in mca.items() if k != "items"}), "items": items}


def technical_names(raw: Mapping[str, Any]) -> set[str]:
    """Section, check plug-in and token names of the data set (lower case)."""
    names: set[str] = set()
    for host in (raw.get("hosts") or {}).values():
        names.update((host.get("agent") or {}).get("sections") or {})
        names.update(_check_plugin(c).partition(":")[2] or _check_plugin(c) for c in host.get("check_commands", []))
        for item in (host.get("mca") or {}).get("items") or []:
            names.add(str(item.get("token", "")).removeprefix("generic:"))
            names.update(str(p) for p in item.get("plugins") or [])
    content = (raw.get("rules_file") or {}).get("content") or {}
    for key in ("titles", "aliases", "detect"):
        if isinstance(content.get(key), Mapping):
            names.update(content[key])
    return {n.lower() for n in names if n}


def anonymize(raw: Mapping[str, Any], key: bytes, with_runtime: bool) -> tuple[dict[str, Any], dict[str, str]]:
    """Builds the document to be sent and the mapping pseudonym -> name."""
    ps = Pseudonymizer(key)
    meta = raw.get("meta", {})
    ps.add("server", [meta.get("server", ""), meta.get("server_fqdn", "")])
    ps.add("site", raw.get("site_ids", []))
    ps.add("host", raw.get("all_host_names", []))
    ps.add("host", raw.get("piggyback_targets", []))
    ps.add("address", raw.get("all_addresses", []))
    ps.add("folder", [f for f in raw.get("folders", []) if f])
    ps.protected = technical_names(raw)
    root = str(meta.get("root") or "")

    def text(value: object) -> str:
        s = str(value)
        if root:
            s = s.replace(root, "$OMD_ROOT")
        return ps.text(s)

    out_meta = {
        "format": FORMAT,
        "anonymized": True,
        "with_runtime": with_runtime,
        "generated_at": meta.get("generated_at"),
        "checkmk_version": meta.get("checkmk_version"),
        "edition": meta.get("edition"),
        "mkp_version": meta.get("mkp_version"),
        "site": ps.name("site", meta.get("site", "")),
        "num_sites": len(raw.get("site_ids", [])),
        "num_hosts_total": len(raw.get("all_host_names", [])),
        "num_hosts_analyzed": len(raw.get("hosts", {})),
        "single_host": bool(meta.get("single_host")),
        "cron_active": meta.get("cron_active"),
        "piggyback_enabled": meta.get("piggyback_enabled"),
        "piggyback_interval_hours": meta.get("piggyback_interval_hours"),
        "last_full_run": meta.get("last_full_run"),
        "last_run_duration_seconds": meta.get("last_run_duration_seconds"),
        "errors": [{"where": e.get("where"), "error": text(e.get("error", ""))} for e in meta.get("errors", [])],
    }

    rules_file = dict(raw.get("rules_file") or {})
    if rules_file.get("modified") is False:
        rules_file.pop("content", None)
    elif "content" in rules_file:
        rules_file["content"] = ps.deep(rules_file["content"])

    setup_rules = []
    for rule in raw.get("setup_rules", []):
        value = _without_comments(rule.get("value") or {})
        setup_rules.append({
            "folder": ps.name("folder", rule["folder"]) if rule.get("folder") else "",
            "disabled": bool(rule.get("disabled")),
            "value": ps.deep(value),
            "conditions": _rule_conditions_summary(rule.get("conditions") or {}, ps),
        })

    hosts: dict[str, Any] = {}
    for name, host in sorted((raw.get("hosts") or {}).items()):
        labels = host.get("labels") or {}
        kept_labels = {
            text(k): (ps.name("site", v) if k == "cmk/site" else text(v))
            for k, v in sorted(labels.items()) if k.startswith(_KEEP_LABEL_PREFIX)
        }
        tags = host.get("tags") or {}
        entry: dict[str, Any] = {
            "site": ps.name("site", host.get("site", "")),
            "labels": kept_labels,
            "custom_labels": sum(1 for k in labels if not k.startswith(_KEEP_LABEL_PREFIX)),
            "tags": {
                g: (ps.name("site", v) if g == "site" else v)
                for g, v in sorted(tags.items()) if g in BUILTIN_TAG_GROUPS
            },
            "custom_tag_groups": sum(1 for g in tags if g not in BUILTIN_TAG_GROUPS),
            "num_services": host.get("num_services"),
            "check_plugins": sorted({_check_plugin(c) for c in host.get("check_commands", [])}),
            "mca": _mca(host.get("mca"), ps, with_runtime),
            "effective": ps.deep(host.get("effective")) if host.get("effective") is not None else None,
        }
        agent = host.get("agent")
        if agent is not None:
            a: dict[str, Any] = {
                "source": text(agent.get("source", "")),
                "error": text(agent["error"]) if agent.get("error") else None,
                "sections": dict(agent.get("sections") or {}),
                "piggyback_targets": {k: len(v) for k, v in (agent.get("piggyback") or {}).items()},
            }
            if with_runtime:
                a["windows_services"] = [text(x) for x in agent.get("windows_services", [])]
                a["systemd_units"] = [text(x) for x in _systemd_unit_states(agent.get("systemd_units", []))]
                a["processes"] = [text(x) for x in agent.get("processes", [])]
            entry["agent"] = a
        hosts[ps.name("host", name)] = entry

    doc = {"meta": out_meta, "rules_file": rules_file, "setup_rules": setup_rules, "hosts": hosts}
    mapping = dict(sorted(ps.mapping.items()))
    return doc, mapping


_HOST_PSEUDONYM_RE = re.compile(r"^(?:host|server)-[0-9a-f]{8}$")


def resolve_hosts(key: bytes, host_names: Iterable[str], pseudonyms: Iterable[str]) -> dict[str, list[str]]:
    """Real host names for host pseudonyms ("mcactl support-data resolve").
    Pseudonyms are derived from the site key and the name, so they can be
    recomputed from the current hosts instead of keeping a mapping file.
    The site's own server gets "server-..." if it is a monitored host too.
    Empty pseudonyms: all hosts. Unknown pseudonyms map to []."""
    ps = Pseudonymizer(key)
    table: dict[str, list[str]] = {}
    for name in sorted(set(host_names)):
        for kind in ("host", "server"):
            table.setdefault(ps.pseudonym(kind, name), []).append(name)
    wanted = [p.strip().lower() for p in pseudonyms if p.strip()]
    if not wanted:
        return {p: names for p, names in sorted(table.items(), key=lambda kv: kv[1]) if p.startswith("host-")}
    return {p: table.get(p, []) for p in wanted}


def is_host_pseudonym(value: str) -> bool:
    return bool(_HOST_PSEUDONYM_RE.match(value.strip().lower()))


def identifying_names(mapping: Mapping[str, str]) -> list[str]:
    """Real names of hosts, sites, servers, folders, domains and IPs from the
    mapping (not the masked service/process names), incl. short host names."""
    out: set[str] = set()
    for pseudonym, real in mapping.items():
        if pseudonym.split("-", 1)[0] in ("host", "site", "server", "folder", "domain", "ip"):
            out.add(real)
            if "." in real and not _IPV4_RE.fullmatch(real):
                out.add(real.partition(".")[0])
    return sorted(out)


def summary(doc: Mapping[str, Any], residual: Iterable[str] = ()) -> list[str]:
    """Overview of the actual content of the document, shown before the user
    decides whether to create the transport file."""
    meta = doc.get("meta") or {}
    hosts = doc.get("hosts") or {}
    plugins = sorted({p for h in hosts.values() for p in h.get("check_plugins") or []})
    sections = sorted({s for h in hosts.values() for s in ((h.get("agent") or {}).get("sections") or {})})
    example = next(iter(hosts), "")
    rules = doc.get("setup_rules") or []
    rules_file = doc.get("rules_file") or {}

    def names(values: list[str]) -> str:
        shown = ", ".join(values[:5])
        return f"{len(values)} names ({shown}{', ...' if len(values) > 5 else ''})" if values else "none"

    lines = [
        f"Checkmk:          {meta.get('checkmk_version')} {meta.get('edition') or ''}".rstrip(),
        f"MCA package:      {meta.get('mkp_version')}",
        f"Hosts:            {len(hosts)}" + (f" (pseudonymized, e.g. {example})" if example else "")
        + (" - single host" if meta.get("single_host") else f" of {meta.get('num_hosts_total')} on the site"),
        f"Setup rules:      {len(rules)} MCA rule(s), comments removed, conditions only as a count",
        "Rules file:       "
        + ("content included (modified locally)" if "content" in rules_file else "checksum only (unmodified)"),
        f"Check plug-ins:   {names(plugins)}",
        f"Agent sections:   {names(sections)}",
        "Service/process names: " + ("included (not anonymized)" if meta.get("with_runtime") else "not included"),
    ]
    residual = list(residual)
    if residual:
        lines.append(f"Still looks like a name or address: {len(residual)} entr{'y' if len(residual) == 1 else 'ies'}")
        lines.extend(f"  - {r}" for r in residual)
    else:
        lines.append("Still looks like a name or address: nothing found")
    return lines


def residual_ignore(raw: Mapping[str, Any]) -> set[str]:
    """Words the residual check does not report as a leftover real name:
    technical names and the words of subsystem titles (a folder called
    "server" vs. the title "Apache HTTP Server")."""
    words = technical_names(raw)
    for host in (raw.get("hosts") or {}).values():
        for item in (host.get("mca") or {}).get("items") or []:
            words.update(w.lower() for w in re.findall(r"[A-Za-z0-9_.-]+", str(item.get("title", ""))))
    return words


_RUNTIME_KEYS = frozenset({"windows_services", "systemd_units", "processes"})


def residual_findings(
    doc: Any, known_names: Iterable[str] = (), limit: int = 30, ignore: Iterable[str] = ()
) -> list[str]:
    """Strings in the document that still look like a host name, domain or
    IP address, plus any known real name that survived. Shown to the user
    before sending. The service/process name lists (sent only with consent)
    are checked for known names and IP addresses only."""
    known = {n.lower() for n in known_names if len(n) >= 3} - {n.lower() for n in ignore}
    found: dict[str, None] = {}

    def walk(value: Any, runtime: bool = False) -> None:
        if len(found) >= limit:
            return
        if isinstance(value, str):
            for m in _TOKEN_RE.finditer(value):
                token = m.group(0).lower()
                parts = token.split(".")
                for cand in {token, parts[0], ".".join(parts[1:])}:
                    if cand in known:
                        found.setdefault(f"known name: {cand}", None)
            for m in _IPV4_RE.finditer(value):
                found.setdefault(f"IP address: {m.group(0)}", None)
            for m in ([] if runtime else _FQDN_RE.finditer(value)):
                if (
                    m.group(0).rsplit(".", 1)[-1].lower() in _DOMAIN_SUFFIXES
                    and not _SAFE_FQDN_RE.search(m.group(0))
                    and not re.match(r"(host|site|folder|domain|server|ip)-[0-9a-f]{8}\b", m.group(0))
                ):
                    found.setdefault(f"looks like a host/domain name: {m.group(0)}", None)
        elif isinstance(value, Mapping):
            for k, v in value.items():
                walk(k, runtime)
                walk(v, runtime or k in _RUNTIME_KEYS)
        elif isinstance(value, (list, tuple)):
            for v in value:
                walk(v, runtime)

    walk(doc)
    return list(found)[:limit]


# ---------------------------------------------------------------------------
# Collection (GUI context, real names)
# ---------------------------------------------------------------------------


def _shipped_rules(root: str, version: str) -> bytes | None:
    """Rules file as shipped in the installed MKP, for the 'modified' check."""
    path = os.path.join(root, "var", "check_mk", "packages_local", f"{PKG}-{version}.mkp")
    try:
        with tarfile.open(path) as outer:
            member = outer.extractfile("web.tar")
            if member is None:
                return None
            with tarfile.open(fileobj=io.BytesIO(member.read())) as inner:
                f = inner.extractfile(f"plugins/pages/{PKG}_rules.json")
                return f.read() if f is not None else None
    except (OSError, KeyError, tarfile.TarError):
        return None


def collect_raw(
    page: Any, root: str, site: str, with_runtime: bool, only_host: str | None = None
) -> dict[str, Any]:
    """Collects all data with real names. Must run inside
    application_and_request_context() with the GUI page module loaded.
    only_host: restrict hosts and Setup rules to this host (the names of all
    hosts are still used for the pseudonyms, but not included)."""
    import ast
    import socket

    errors: list[dict[str, str]] = []

    def err(where: str) -> None:
        errors.append({"where": where, "error": traceback.format_exc(limit=3)})

    meta: dict[str, Any] = {
        "generated_at": time.time(), "site": site, "root": root,
        "server": socket.gethostname(), "server_fqdn": socket.getfqdn(), "errors": errors,
        "single_host": only_host is not None,
    }
    raw: dict[str, Any] = {"meta": meta}

    try:
        from cmk.ccc.version import __version__, edition
        from cmk.utils import paths as cmk_paths

        meta["checkmk_version"] = __version__
        meta["edition"] = edition(cmk_paths.omd_root).short
    except Exception:
        err("version")
    try:
        with open(os.path.join(root, "var", "check_mk", "packages", PKG), encoding="utf-8") as handle:
            meta["mkp_version"] = ast.literal_eval(handle.read()).get("version")
    except Exception:
        err("mkp_version")
    try:
        from cmk_addons.plugins.monitoring_coverage_analyzer.lib import runstate

        meta["piggyback_enabled"] = runstate.generate_piggyback_data_enabled()
        meta["piggyback_interval_hours"] = runstate.piggyback_interval_hours()
    except Exception:
        err("settings")

    # Rules file and whether it differs from the shipped one
    try:
        path = os.path.join(root, "local", "share", "check_mk", "web", "plugins", "pages", f"{PKG}_rules.json")
        with open(path, "rb") as handle:
            content = handle.read()
        shipped = _shipped_rules(root, str(meta.get("mkp_version") or ""))
        raw["rules_file"] = {
            "sha256": hashlib.sha256(content).hexdigest(),
            "modified": None if shipped is None else shipped != content,
            "content": json.loads(content),
        }
    except Exception:
        err("rules_file")

    # Setup rules of the MCA ruleset
    raw["setup_rules"] = []
    folders: set[str] = set()
    try:
        ruleset = page._load_mca_ruleset()
        rules = [] if ruleset is None else ruleset.get_rules()
        if only_host is not None and rules:
            _value, matching = ruleset.analyse_ruleset(
                only_host, None, page.PIGGYBACK_SERVICE_TITLE, {}, debug=False
            )
            ids = {r.id for _f, _i, r in matching}
            rules = [(f, i, r) for f, i, r in rules if r.id in ids]
        for folder, _index, rule in rules:
            path = folder.path()
            folders.update(p for p in path.split("/") if p)
            spec = rule.to_config()
            raw["setup_rules"].append({
                "folder": path, "disabled": rule.is_disabled(),
                "value": spec.get("value"), "conditions": spec.get("condition") or {},
            })
    except Exception:
        err("setup_rules")
    raw["folders"] = sorted(folders)

    # All hosts and services of all sites
    hosts_live: dict[str, dict[str, Any]] = {}
    site_ids: set[str] = {site}
    try:
        from cmk.gui import sites

        conn = sites.live()
        with sites.prepend_site():
            rows = conn.query("GET hosts\nColumns: name address labels tags num_services\n")
        for row_site, name, address, labels, tags, num in rows:
            site_ids.add(row_site)
            hosts_live[name] = {"site": row_site, "address": address, "labels": labels or {},
                                "tags": tags or {}, "num_services": num, "services": []}
        with sites.prepend_site():
            rows = conn.query("GET services\nColumns: host_name description check_command state\n")
        for _site, host, desc, cmd, state in rows:
            if host in hosts_live:
                hosts_live[host]["services"].append([desc, cmd, state])
    except Exception:
        err("livestatus")
    raw["site_ids"] = sorted(site_ids)
    raw["all_host_names"] = sorted(hosts_live)
    raw["all_addresses"] = sorted({h["address"] for h in hosts_live.values() if h.get("address")})

    # MCA result (cache) and the evaluation with the Setup rule
    try:
        loaded = page._load_cached_results()
        cache_path = page._cache_path()
        if cache_path:
            with open(cache_path, encoding="utf-8") as handle:
                cache = json.load(handle)
            meta["last_full_run"] = cache.get("last_full_run_timestamp")
            meta["last_run_duration_seconds"] = cache.get("last_run_duration_seconds")
    except Exception:
        loaded = None
        err("cache")
    results = list(loaded[1]) if loaded else []
    if only_host is not None:
        results = [r for r in results if r.host_name == only_host]
    effective: dict[str, Any] = {}
    try:
        for r in page._apply_rules(results, page._RuleLookup()):
            effective[r.host_name] = {
                "status": r.status, "coverage_pct": r.coverage_pct,
                "monitored_count": r.monitored_count, "total_count": r.total_count,
                "findings": r.findings,
            }
    except Exception:
        err("effective")

    # Agent sections per analyzed host (same source as the analysis)
    agent: dict[str, Any] = {}
    targets: set[str] = set()
    try:
        names = sorted(r.host_name for r in results)
        secs = page._collect_agent_sections(names, {h: hosts_live.get(h, {}).get("site", site) for h in names})
        for h in names:
            s = secs.get(h)
            if s is None:
                agent[h] = {"source": "", "error": "no agent sections"}
                continue
            pb = {k: sorted(v) for k, v in (s.piggyback or {}).items()}
            targets.update(t for v in pb.values() for t in v)
            entry: dict[str, Any] = {"source": s.source, "error": s.error,
                                     "sections": {k: len(v) for k, v in s.sections.items()}, "piggyback": pb}
            if with_runtime:
                rt = page._runtime_facts(s.sections)
                entry["windows_services"] = list(s.sections.get("services", []))
                entry["systemd_units"] = list(s.sections.get("systemd_units", []))
                entry["processes"] = sorted(set(rt.processes))
            agent[h] = entry
    except Exception:
        err("agent")
    raw["piggyback_targets"] = sorted(targets)

    hosts: dict[str, Any] = {}
    for r in results:
        live = hosts_live.get(r.host_name, {})
        hosts[r.host_name] = {
            "site": live.get("site", site), "address": live.get("address"),
            "labels": live.get("labels", {}), "tags": live.get("tags", {}),
            "num_services": live.get("num_services"),
            "check_commands": [svc[1] for svc in live.get("services", [])],
            "services": live.get("services", []),
            "mca": {
                "status": r.status, "coverage_pct": r.coverage_pct,
                "monitored_count": r.monitored_count, "total_count": r.total_count,
                "items": list(r.items), "source_lines": list(r.source_lines),
            },
            "effective": effective.get(r.host_name),
            "agent": agent.get(r.host_name),
        }
    raw["hosts"] = hosts
    return raw


def unanonymized(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Document for --no-anonymize: everything, marked as such."""
    doc = dict(raw)
    doc["meta"] = {**raw.get("meta", {}), "format": FORMAT, "anonymized": False, "with_runtime": True}
    return doc
