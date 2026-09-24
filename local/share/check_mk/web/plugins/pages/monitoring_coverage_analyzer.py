#!/usr/bin/env python3
"""GUI page "Analyze monitoring coverage".

Loaded as a Checkmk "legacy" GUI plugin (share/check_mk/web/plugins/pages/),
the same mechanism used by cmk.gui.utils.plugins.register() ->
utils.load_web_plugins("pages", globals()). Registration happens as an
import-time side effect (page_registry.register(...) at module level).

Ausbaustufe 1.3.0 (vs. 1.2.0 PoC):
  UI-Politur (Schritt 1):
  - Status-Zelle nutzt jetzt exakt dieselbe CSS-Klasse wie die normale
    Checkmk Service-Tabelle: "state svcstate state0/1/2" (siehe
    lib/python3/cmk/gui/bi/view.py: 'classes = "state svcstate state%s" %
    state["state"]' - live im System per grep nachgeschlagen, nicht
    geraten). OK=state0 (gruen), WARN=state1 (gelb), CRIT=state2 (rot) -
    das Farbschema kommt automatisch aus dem aktiven Checkmk-Theme
    (facelift/modern-dark), keine eigene Farbdefinition noetig.
  - Hostname ist jetzt ein Link auf die eingebaute "host"-View
    (view.py?view_name=host&host=<hostname>), erzeugt per
    cmk.gui.utils.urls.makeuri_contextless(request, [("view_name",
    "host"), ("host", host_name)], filename="view.py") - dieselbe
    Konvention, die Checkmk-eigene Painter fuer Host-Links verwenden.
  - Coverage-Zelle bekommt einen Perf-o-Meter-artigen Balken per inline
    CSS linear-gradient() im style-Attribut der <td>: gefuellter Anteil
    entspricht coverage_pct, Farbe gruen (>=80%), gelb (>=50%), rot
    (<50%) - vereinfachtes Ampel-Schema, nicht pixelgenau wie das
    Original-Perf-o-Meter, aber optisch klar als Balken erkennbar.

  Ergebnis-Caching (Schritt 2):
  - Das Analyse-Ergebnis wird nach jedem Lauf als einfache JSON-Datei
    unter var/check_mk/web/monitoring_coverage_analyzer_cache.json
    (OMD_ROOT-relativ, siehe _cache_path()) abgelegt (atomarer Rename via
    os.replace(), robust gg. gleichzeitige Worker-Prozesse) und beim
    naechsten Seitenaufruf wieder eingelesen - persistiert also ueber
    mehrere Apache/CMK-GUI-Worker-Prozesse hinweg (kein reiner
    In-Memory-Cache). Bewusst per json.dump()/json.load() statt
    cmk.ccc.store.save_object_to_file()/load_object_from_file()
    nachgebaut (deren Existenz live auf der Test-Site verifiziert wurde,
    siehe Recherche-Notiz unten), um keine harte Abhaengigkeit von
    internen cmk.*-Modulpfaden einzugehen, die sich zwischen
    Checkmk-Versionen verschieben koennen - fachlich aequivalent
    (atomares Schreiben + einfaches Lesen einer JSON-Datei unterhalb
    von var/check_mk/web/, der GUI-eigenen Datenablage).
  - Existiert beim Betreten der Seite noch kein Cache, wird die Analyse
    automatisch einmal ausgefuehrt (kein Leerzustand mehr).
  - Der Knopf heisst jetzt "Re-run analysis" (statt "Start analysis
    now"): passender, weil die Seite ab jetzt IMMER ein (ggf.
    zwischengespeichertes) Ergebnis zeigt und der Knopf nur noch fuer
    das explizite Neu-Berechnen gebraucht wird.

  Belege (seit 0.9.0-b14, siehe _analyze_host()):
  - Direkt aus der Agent-Ausgabe (get-agent-output @cached): Sections,
    deren Name auf ein Token abbildet und die echte Daten liefern
    (T_SECTION), die Plug-in-Liste checkmk_agent_plugins_lnx/_win
    (T_PLUGIN) und laufende Dienste/Prozesse aus systemd_units, ps_lnx/
    ps und Windows services ueber detect-Regeln der JSON (T_RUNTIME).
  - Das HW/SW-Inventory (installierte Pakete) ist nur noch Info, kein
    Beleg mehr - die frueheren Pakettreffer mussten nachtraeglich per
    requires_evidence wieder aussortiert werden.

Scope (weiterhin bewusst reduziert):
  - Nur ein manueller "Analyse jetzt starten"-Knopf, kein Scheduler, keine
    Notifications.
  - Die Analyse laeuft weiterhin SYNCHRON (kein cmk.gui.background_job):
    "cmk -L" wird via subprocess GENAU EINMAL pro Analyse-Lauf ausgefuehrt
    und fuer alle Hosts wiederverwendet (siehe _available_plugin_map()
    mit einfachem Zeit-basiertem Cache, analog available_plugins() im
    Referenz-Special-Agent-Skript). Fuer produktiv sehr viele Hosts /
    aufwendigere Logik sollte dennoch auf cmk.gui.background_job
    umgestellt werden.

Ausbaustufe 2.0.0 (Piggyback-Erweiterung, siehe
PLAN_piggyback_background_job.md - alle Entscheidungen dort sind final):
  - Neue Funktionen _build_piggyback_payload()/_write_piggyback_data()/
    _run_piggyback_full()/_run_piggyback_refresh(): erzeugen pro Host ein
    JSON-Payload (Findings/Coverage/beide Zeitstempel) und schreiben es
    per cmk.piggyback.backend.store_piggyback_raw_data() (offizielle API,
    KEIN rohes Dateisystem-Schreiben) unter der Piggyback-Quelle
    "monitoring_coverage_analyzer" fuer jeden Host ab.
  - Erweiterte Cache-Datei (_CACHE_FILE_REL): traegt jetzt zusaetzlich
    "last_full_run_timestamp" (echter Full-Run: neue Livestatus-Query +
    Regelauswertung) und "last_piggyback_refresh_timestamp" (letzter
    Refresh-Tick: gleicher Inhalt, nur neuer message_timestamp beim
    erneuten store_piggyback_raw_data()-Aufruf). Beide Zeitstempel werden
    vom Check-Plugin (siehe cmk_addons_plugins/monitoring_coverage_
    analyzer/agent_based/monitoring_coverage.py) als ZWEI GETRENNTE
    Zeilen im Service-Output angezeigt (Transparenzprinzip, Plan-
    Entscheidung 11) - "Content last computed" vs. "Piggyback transfer
    last refreshed".
  - Zwei neue GET-Parameter fuer den Cron-Trigger:
      ?_cron_fullrun=1  -> Full-Run (nur wenn piggyback_interval_hours
                           abgelaufen ist, ausser _force=1 ist gesetzt)
                           + volles Piggyback-Update (neuer Inhalt,
                           beide Zeitstempel neu).
      ?_cron_refresh=1  -> NUR Refresh-Tick: letzten gecachten Inhalt
                           erneut mit neuem message_timestamp schreiben,
                           KEINE Livestatus-Query. Nur der zweite
                           Zeitstempel aendert sich.
    Aufrufweg: beide Endpunkte werden PRAGMATISCH per direktem
    Python-Aufruf im Site-Kontext getriggert (cmk.gui.utils.
    script_helpers.application_and_request_context(), siehe
    local/bin/monitoring_coverage_analyzer_cron - analog zu
    Checkmk-eigenen CLI-Skripten wie cmk-update-config), NICHT per
    authentifiziertem HTTP-Request: fuer einen reinen Cron-Trigger ist
    das einfacher/robuster als eine automation-user-secret-Loesung
    (kein Netzwerk-Roundtrip, kein Secret-Handling noetig) und wurde vom
    Plan als bevorzugte Alternative ausdruecklich zugelassen ("falls ein
    einfacherer, sauberer Weg existiert ... das bevorzugen"). Der GET-
    Parameter-Mechanismus in dieser Datei bleibt trotzdem bestehen (auch
    per echtem HTTP-Request nutzbar, z.B. fuer manuelle Tests via curl
    mit GUI-Session-Cookie), nur das mitgelieferte Cron-Skript nutzt den
    direkten Python-Weg.
  - "Re-run analysis"-Knopf loest weiterhin sofort einen echten Full-Run
    aus UND stoesst danach (wenn generate_piggyback_data aktiv ist)
    sofort auch den Piggyback-Full-Write an (neuer Inhalt fuer beide
    Zeitstempel, siehe PageMonitoringCoverageAnalyzer._show_results()).
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

# 0.9.0-b21: gemeinsame Auswertung (Ignore-Regeln, Status, Coverage, Texte)
# mit dem Check-Plugin - beide wenden die Setup-Regel gleich an.
from cmk_addons.plugins.monitoring_coverage_analyzer.lib import evaluate as _ev

# Ausbaustufe 2.0.0: offizielle Piggyback-Schreib-API - live auf
# Test-Site/Checkmk 2.5.0p12 Ultimate per
# "python3 -c 'import inspect,cmk.piggyback.backend as b;
# print(inspect.signature(b.store_piggyback_raw_data))'" verifiziert:
# (source_hostname: HostAddress, piggybacked_raw_data: Mapping[HostName,
# Sequence[bytes]], message_timestamp: float, contact_timestamp: float |
# None, omd_root: Path) -> None - exakt der im Plan spezifizierte Import
# und die im Plan spezifizierte Signatur, keine Abweichung noetig.
try:
    from cmk.piggyback.backend import store_piggyback_raw_data as _store_piggyback_raw_data
except ImportError:  # pragma: no cover - defensiv, falls Modulpfad sich
    # in einer anderen Checkmk-Version verschiebt: Piggyback-Erzeugung
    # wird dann schlicht deaktiviert (mit klarer Fehlermeldung im
    # Log/GUI), statt den kompletten Seiten-Import zum Absturz zu
    # bringen.
    _store_piggyback_raw_data = None

PAGE_TITLE = _("Analyze monitoring coverage")

# Ausbaustufe 2.0.0: Konstanten fuer die Piggyback-Erweiterung (siehe
# PLAN_piggyback_background_job.md, Entscheidungen 4-7).
PIGGYBACK_SOURCE_HOSTNAME = "monitoring_coverage_analyzer"
PIGGYBACK_SECTION_NAME = "checkmk_monitoring_coverage"
PIGGYBACK_SERVICE_TITLE = "Checkmk Monitoring Coverage"
# Fest verdrahtetes Refresh-Tick-Intervall (Plan-Entscheidung 7, NICHT
# konfigurierbar) - wird von _run_piggyback_refresh() zwar nicht selbst
# durchgesetzt (das macht der Cron-Zeitplan alle 5 Minuten), aber hier
# als Konstante dokumentiert, damit sie an einer Stelle nachlesbar ist.
PIGGYBACK_REFRESH_INTERVAL_SECONDS = 5 * 60

# 0.9.0-b15: Spinner fuer den "Re-run analysis"-Knopf (reines CSS, keine
# Bilddatei - Farbe folgt dem Theme ueber currentColor).
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


# Anzeige-Reihenfolge/Farbe fuer die Status-Zelle, exakt wie in der
# normalen Checkmk Service-Tabelle (live nachgeschlagen, siehe
# lib/python3/cmk/gui/bi/view.py: 'classes = "state svcstate state%s" %
# state["state"]' - state 0=OK, 1=WARN, 2=CRIT, 3=UNKNOWN).
_STATUS_TO_STATE_NUM: dict[str, int] = {"OK": 0, "WARN": 1, "CRIT": 2, "UNKNOWN": 3}

# ---------------------------------------------------------------------------
# Coverage-Wissensbasis (Schritt 1.4.0: rein daten-basiert): die Regeln
# (Alias-/Titel-/Hinweis-Tabellen, Stop-Tokens) liegen AUSSCHLIESSLICH in
# der Datei monitoring_coverage_analyzer_rules.json im selben Verzeichnis -
# es gibt bewusst KEINE im Code hartcodierte Kopie/Fallback mehr. Damit ist
# fuer jedes Analyse-Ergebnis eindeutig: es kommt IMMER aus dieser einen
# JSON-Datei, nie aus einer stillen Code-Alternative. Erweiterungen/
# Korrekturen sind dadurch OHNE Code-Aenderung/Redeploy moeglich (Datei auf
# der Site editieren, naechster Analyse-Lauf liest sie automatisch neu ein,
# siehe _load_rules()/_reload_rules()).
#
# Konsequenz: fehlt die Datei, ist sie kaputtes JSON, oder ist der Inhalt
# strukturell unbrauchbar (leere aliases/titles), schlaegt der Analyse-Lauf
# mit einer klaren Fehlermeldung fehl (siehe _query_and_analyze_hosts()) -
# es wird NICHT stillschweigend mit einer unvollstaendigen/falschen
# Wissensbasis weitergearbeitet. Andernfalls waere spaeter unklar, ob ein
# Ergebnis aus der echten Regel-Datei oder aus einem Code-Fallback stammt.
# ---------------------------------------------------------------------------

_RULES_FILE_NAME = "monitoring_coverage_analyzer_rules.json"


class _Rules(NamedTuple):
    aliases: dict[str, str]
    titles: dict[str, str]
    hints: dict[str, str]
    stop_tokens: frozenset[str]
    # 0.9.0-b14: positive Erkennungsregeln statt nachtraeglichem
    # requires_evidence-Filter, siehe _match_condition().
    detect: dict[str, dict[str, list[str]]]
    section_data: dict[str, str]
    section_ignore: frozenset[str]
    no_data_lines: list[str]
    # 0.9.0-b21: Plug-in-Familien, die der generische Abgleich nie
    # vorschlaegt (zu allgemeine Namen wie "local", "win", "job").
    generic_ignore_families: frozenset[str] = frozenset()


class RulesLoadError(RuntimeError):
    """Die Coverage-Regel-Datei fehlt oder ist nicht nutzbar.

    Wird bewusst NICHT abgefangen und stillschweigend durch eine
    Code-Fallback-Wissensbasis ersetzt: die Regel-Erkennung ist rein
    daten-basiert (siehe Modul-Docstring oben), ein Analyse-Ergebnis soll
    niemals aus einer unklaren Quelle stammen koennen. Aufrufer (siehe
    _query_and_analyze_hosts()) fangen diesen Fehler ab und zeigen ihn dem
    Benutzer klar an, statt den Lauf mit falschen/fehlenden Regeln
    fortzusetzen.
    """


# Kapabilitaets-Typen (angelehnt an T_* im Referenz-Check).
T_LABEL = "host_label"
# 0.9.0-b14: installierte Pakete (HW/SW-Inventory) sind KEIN Beleg mehr -
# sie erzeugen fuer sich allein kein Finding, sondern erscheinen nur noch
# als Info-Zeile unter "Sources" (siehe _analyze_host()).
T_INV_PACKAGE = "inv_package"
# 0.9.0-b14: direkter Beleg - eine Section der Agent-Ausgabe (get-agent-
# output @cached), deren Name auf das Token abbildet, liefert echte Daten
# (bzw. eine "direct"-Regel aus detect greift). Vorher kam T_SECTION aus
# der Plug-in-Liste im HW/SW-Inventory.
T_SECTION = "agent_section"
# 0.9.0-b14: ausgeliefertes Agent-Plug-in / Local Check laut Section
# checkmk_agent_plugins_lnx/_win der Agent-Ausgabe.
T_PLUGIN = "agent_plugin"
# 0.9.0-b14: indirekter Beleg - Dienst/Prozess laeuft (systemd_units,
# ps_lnx/ps, Windows services), Regeln unter detect.<token>.runtime.
T_RUNTIME = "runtime"
# T_CHECK (0.9.0-b6): bereits ueberwachter check_command als eigener
# Kapabilitaets-Beleg - siehe Docstring in _analyze_host().
T_CHECK = "check_command"


def _rules_path() -> str:
    """Pfad zu monitoring_coverage_analyzer_rules.json.

    WICHTIG (seit 0.9.0-b3): auf manchen Sites/Checkmk-Versionen fasst der
    Legacy-Plugin-Loader (load_web_plugins) alle Page-Plugins zu einer
    synthetischen Datei unter lib/python3/cmk/gui/utils/ zusammen, bevor sie
    ausgefuehrt wird. In diesem Fall zeigt __file__ NICHT mehr auf das
    tatsaechliche Ablageverzeichnis share/check_mk/web/plugins/pages/,
    sondern auf diesen synthetischen Zwischenpfad - dort liegt die JSON-Datei
    nie, siehe Bug-Report des Users ("...lib/python3/cmk/gui/utils/
    monitoring_coverage_analyzer_rules.json ... konnte nicht gelesen
    werden").

    Deshalb: zuerst neben __file__ suchen (funktioniert auf den meisten
    Sites), und wenn die Datei dort nicht existiert, auf den
    OMD_ROOT-relativen Installationspfad zurueckfallen, unter dem das MKP
    die Datei tatsaechlich ablegt (share/check_mk/web/plugins/pages/).
    """
    candidate = os.path.join(os.path.dirname(os.path.abspath(__file__)), _RULES_FILE_NAME)
    if os.path.isfile(candidate):
        return candidate
    omd_root = os.environ.get("OMD_ROOT", "")
    if omd_root:
        # WICHTIG: MKP-Inhalte landen unter local/share/... (nicht direkt
        # unter share/...) - share/check_mk/... ist der Pfad fuer
        # Checkmk-Bordmittel, lokal installierte Erweiterungen liegen
        # immer unter local/.
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
    """Laedt die Coverage-Regeln AUSSCHLIESSLICH aus
    monitoring_coverage_analyzer_rules.json - rein daten-basiert, ohne
    Code-Fallback (siehe RulesLoadError-Docstring). Jeder Fehlerfall
    (Datei fehlt, ungueltiges JSON, leere/kaputte Struktur) fuehrt zu einer
    RulesLoadError mit einer fuer den Benutzer verstaendlichen Meldung, statt
    still auf eine im Code hinterlegte Alternative auszuweichen. Kein
    Zeit-basierter Cache noetig: die Datei wird nur einmal pro Analyse-Lauf
    gelesen (siehe Aufrufer in _query_and_analyze_hosts()).
    """
    path = _rules_path()
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise RulesLoadError(
            f"Regel-Datei {path!r} konnte nicht gelesen werden ({exc}). "
            "Die Datei muss neben diesem Python-Modul liegen."
        ) from exc
    except ValueError as exc:
        raise RulesLoadError(
            f"Regel-Datei {path!r} enthaelt kein gueltiges JSON ({exc})."
        ) from exc

    if not isinstance(data, dict):
        raise RulesLoadError(
            f"Regel-Datei {path!r} muss ein JSON-Objekt sein (aliases/titles/"
            "hints/stop_tokens), gefunden: {type(data).__name__}."
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
                for kind in ("direct", "runtime", "not_on_os")
            }
    section_data = {
        str(k): str(v) for k, v in (data.get("section_data") or {}).items()
    } if isinstance(data.get("section_data"), dict) else {}
    section_ignore = frozenset(str(x) for x in data.get("section_ignore") or [])
    no_data_lines = [str(x) for x in data.get("no_data_lines") or []]
    generic_ignore_families = frozenset(
        str(x).lower() for x in data.get("generic_ignore_families") or []
    )
    for pattern in [*section_data.values(), *no_data_lines]:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise RulesLoadError(
                f"Regel-Datei {path!r}: ungueltiger regulaerer Ausdruck {pattern!r} ({exc})."
            ) from exc
    if not aliases or not titles:
        raise RulesLoadError(
            f"Regel-Datei {path!r} ist strukturell unbrauchbar: 'aliases' "
            "und/oder 'titles' sind leer oder fehlen."
        )
    return _Rules(
        aliases, titles, hints, stop_tokens, detect, section_data, section_ignore, no_data_lines,
        generic_ignore_families,
    )


# Modul-globale Wissensbasis, per _reload_rules() befuellt - siehe
# Modul-Docstring oben: rein daten-basiert, kein Code-Fallback. Bleibt bis
# zum ersten erfolgreichen _reload_rules()-Aufruf leer; _query_and_analyze_
# hosts() ruft _reload_rules() zu Beginn jedes Laufs auf und bricht bei
# RulesLoadError mit einer klaren Fehlermeldung ab, statt mit leeren/alten
# Regeln weiterzulaufen.
ALIASES: dict[str, str] = {}
TITLES: dict[str, str] = {}
HINTS: dict[str, str] = {}
STOP_TOKENS: frozenset[str] = frozenset()
DETECT: dict[str, dict[str, list[str]]] = {}
SECTION_DATA: dict[str, str] = {}
SECTION_IGNORE: frozenset[str] = frozenset()
NO_DATA_LINES: list[str] = []
GENERIC_IGNORE_FAMILIES: frozenset[str] = frozenset()


def _reload_rules() -> None:
    """Liest monitoring_coverage_analyzer_rules.json neu ein und aktualisiert
    die modul-globalen Regel-Tabellen (ALIASES/TITLES/HINTS/STOP_TOKENS/
    DETECT/SECTION_DATA/SECTION_IGNORE/NO_DATA_LINES)
    in-place (dict.clear() + update(), damit bereits gebundene Referenzen -
    z.B. Closures - weiter auf dieselben Objekte zeigen)."""
    rules = _load_rules()
    ALIASES.clear()
    ALIASES.update(rules.aliases)
    TITLES.clear()
    TITLES.update(rules.titles)
    HINTS.clear()
    HINTS.update(rules.hints)
    global STOP_TOKENS, SECTION_IGNORE, GENERIC_IGNORE_FAMILIES
    STOP_TOKENS = rules.stop_tokens
    GENERIC_IGNORE_FAMILIES = rules.generic_ignore_families
    DETECT.clear()
    DETECT.update(rules.detect)
    SECTION_DATA.clear()
    SECTION_DATA.update(rules.section_data)
    SECTION_IGNORE = rules.section_ignore
    NO_DATA_LINES[:] = rules.no_data_lines


def _rules_source_status() -> str:
    """Menschenlesbare Herkunfts-Auskunft ueber die aktuell geladenen
    Findings-/Hint-Regeln - beantwortet direkt die Frage "woher kommt
    dieses Ergebnis": Pfad der JSON-Datei, ihr Aenderungszeitpunkt, und die
    Anzahl geladener Eintraege je Tabelle. Es gibt keine Code-Fallback-
    Quelle mehr (siehe Modul-Docstring), daher immer dieselbe Datei.
    """
    path = _rules_path()
    try:
        mtime = os.path.getmtime(path)
        mtime_txt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))
    except OSError:
        mtime_txt = "?"
    return (
        f"{path} (last modified: {mtime_txt}; "
        f"{len(ALIASES)} aliases, {len(TITLES)} titles, "
        f"{len(HINTS)} hints, {len(STOP_TOKENS)} stop-tokens, "
        f"{len(DETECT)} detect rules loaded)"
    )


try:
    _reload_rules()
except RulesLoadError:
    # Beim Modul-Import (z.B. beim ersten WATO-Seitenaufbau, bevor ueberhaupt
    # ein Analyse-Lauf gestartet wurde) soll ein Problem mit der Regel-Datei
    # NICHT den kompletten Seitenaufbau/die Menuregistrierung zum Absturz
    # bringen. ALIASES/TITLES/HINTS/STOP_TOKENS bleiben dann leer; der
    # eigentliche Analyse-Lauf ruft _reload_rules() erneut auf (siehe
    # _query_and_analyze_hosts()) und zeigt den Fehler dem Benutzer dort
    # klar an, statt hier eine Seite mit Stacktrace zu zerstoeren.
    pass

# Cache fuer "cmk -L" (available_plugins) - EINMAL pro Analyse-Lauf, nicht
# pro Host: analog available_plugins() im Referenz-Special-Agent-Skript
# libexec/agent_monitoring_coverage.
_AVAILABLE_PLUGINS_CACHE_TTL = 60.0
_available_plugins_cache: dict[str, tuple[float, Any]] = {}

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_SAFE_HOST_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def _canonical_token(raw: str) -> str:
    """Normalisiert einen rohen Plug-in-/Prozess-/Paketnamen auf einen Token

    Schritt-3-Fix: Agent-Plug-in-Dateinamen wie "mk_inventory" oder
    "mk_apt.py" enthalten Unterstriche, die vom urspruenglichen
    _TOKEN_RE ([a-z0-9]+) als Trenner behandelt wurden - dadurch wurde
    "mk_inventory" faelschlich zu "mk" verkuerzt (kein ALIASES-Treffer,
    T_SECTION fuer "inventory" ging verloren). Fix: zuerst eine bekannte
    Dateiendung (.py/.sh/.exe) abschneiden und den KOMPLETTEN (Punkt-/
    Leerzeichen-bereinigten) Namen gegen ALIASES pruefen, bevor auf den
    "erstes alphanumerisches Wort"-Fallback zurueckgefallen wird (der
    fuer Prozessnamen wie "mariadb 10.6.1" oder Paketnamen wie
    "cups-browsed" weiterhin das gewuenschte Verhalten liefert).
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
    """'cmk -L' -> [(plugin, typ, titel)], typ z.B. "agent", "snmp",
    "active". Gleicher TTL-Cache wie _available_plugin_map()."""
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
    except Exception:  # pragma: no cover - defensive, GUI-Kontext
        rows = []
    _available_plugins_cache["rows"] = (now, rows)
    return rows


def _available_plugin_map() -> dict[str, list[str]]:
    """Liefert token -> sortierte Liste der auf der Site per 'cmk -L'
    verfuegbaren Check-Plugin-Namen (kanonisiert via ALIASES), mit
    einfachem Zeit-basiertem Cache ueber den Prozesslebenszyklus hinweg.

    'cmk -L' wird GENAU EINMAL pro Analyse-Lauf (bzw. bis zum TTL-Ablauf)
    aufgerufen, unabhaengig von der Anzahl der zu analysierenden Hosts -
    Performance-Vorgabe aus der Aufgabenstellung.
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
    # Schritt 3: der aktive Check "cmk_inv" (Checkmk HW/SW Inventory) ist
    # auf JEDER Checkmk-Site verfuegbar, taucht aber nicht in "cmk -L"
    # auf (das listet nur agent_based Check-Plugins, keine aktiven
    # Checks) - deshalb hier unbedingt als verfuegbar seeden, sonst
    # koennte "inventory" nie als monitorbares Subsystem erkannt werden.
    result.setdefault("inventory", ["cmk_inv"])
    _available_plugins_cache["map"] = (now, result)
    return result


def _inventory_package_names(host_name: str) -> list[str]:
    """Liest die im HW/SW-Inventory der Site abgelegten installierten
    Pakete dieses Hosts. Seit 0.9.0-b14 nur noch Info (siehe
    T_INV_PACKAGE), kein Beleg fuer ein Finding.

    Quelle: var/check_mk/inventory/<host>.json (Checkmk HW/SW-Inventory-
    Baum), Pfad Nodes.software.Nodes.packages.Table.Rows[].name.
    Bewusst defensiv: fehlt die Datei / das Inventory fuer diesen Host,
    wird einfach eine leere Liste zurueckgegeben (kein Fehler in der GUI).
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

# 0.9.0-b7: Datenquelle fuer die Agent-Sections ist die (auf der Site
# gepatchte) Automation "get-agent-output <HOST> agent @cached" ueber den
# laufenden automation-helper (cmk-automation-client). "@cached" setzt
# FileCacheOptions(use_outdated=True) -> die Agent-Quellen lesen die vom
# Core ohnehin geschriebenen Cache-Dateien ohne Altersgrenze, statt den
# Agenten erneut abzufragen (live verifiziert auf der Test-Site: mtime der
# Cache-Datei bleibt unveraendert, ~0.2-0.5 s pro Host). Nur wenn fuer
# eine Quelle noch GAR KEINE Cache-Datei existiert, holt der Core live
# (Checkmk-eigenes Verhalten von use_outdated, nicht von uns steuerbar).
# Gegenueber dem direkten Lesen von tmp/check_mk/cache/<host> liefert die
# Automation zusaetzlich Special-Agent- und Piggyback-Daten des Hosts.
# 0.9.0-b8: "@cached" ist ab Checkmk 2.5.0p15 upstream (Mindestversion im
# MKP). Kein Fallback mehr auf die Cache-Datei - aeltere Sites ohne die
# Direktive werden per version.min_required ausgeschlossen.
_AGENT_OUTPUT_WORKERS = 4
_AGENT_OUTPUT_TIMEOUT = 60
SRC_AUTOMATION = "get-agent-output @cached"


class _AgentSections(NamedTuple):
    sections: dict[str, list[str]]
    source: str
    error: str | None


def _parse_agent_sections(raw_text: str) -> dict[str, list[str]]:
    """Zerlegt Roh-Agent-Ausgabe in section_name -> Zeilenliste (OHNE die
    <<<...>>>-Marker-Zeilen). Piggyback-Bloecke fuer ANDERE Hosts
    (<<<<fremder_host>>>> ... <<<<>>>>, z.B. von Proxmox-/HA-Special-
    Agents) werden uebersprungen - deren Sections gehoeren nicht zu diesem
    Host und duerfen keine Evidenz fuer ihn liefern."""
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
    """Holt die Agent-Ausgabe per
    'cmk-automation-client get-agent-output <HOST> agent @cached'.

    Antwortformat (live verifiziert): JSON mit
    "serialized_result_or_error_code" = repr() des Tupels
    (success: bool, details: str, raw_agent_data: bytes) - daher
    ast.literal_eval() (nur Literale, kein Code-Ausfuehren)."""
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
    except Exception as exc:  # pragma: no cover - defensiv, GUI-Kontext
        detail = (proc.stderr or proc.stdout or "").strip()[:300]
        return _AgentSections({}, SRC_AUTOMATION, f"unparsable response ({exc!r}): {detail}")

    raw_text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    error = None if success else (str(details).strip() or "fetch failed")
    return _AgentSections(_parse_agent_sections(raw_text), SRC_AUTOMATION, error)


def _agent_sections(host_name: str) -> _AgentSections:
    if not _SAFE_HOST_RE.match(host_name):
        return _AgentSections({}, "", "unsafe host name")
    return _automation_agent_sections(host_name)


def _collect_agent_sections(host_names: Sequence[str]) -> dict[str, _AgentSections]:
    """Agent-Sections fuer alle Hosts eines Analyse-Laufs, parallel
    (live gemessen auf der Test-Site, 46 Hosts: seriell 23 s, 4 Worker 8 s,
    8 Worker kein weiterer Gewinn)."""
    with ThreadPoolExecutor(max_workers=_AGENT_OUTPUT_WORKERS) as executor:
        return dict(zip(host_names, executor.map(_agent_sections, host_names)))


# ---------------------------------------------------------------------------
# 0.9.0-b14: Belege direkt aus der Agent-Ausgabe (get-agent-output @cached).
# Ersetzt die Plug-in-Liste aus dem HW/SW-Inventory (T_SECTION alt) und den
# nachtraeglichen requires_evidence-Filter fuer Inventory-Pakete.
# ---------------------------------------------------------------------------

# Unterabschnitts-Kopf innerhalb einer Section, z.B. "[df]", "[all]",
# "[processes]" - zaehlt nie als Datenzeile.
_SUBSECTION_RE = re.compile(r"^\[[^\]]*\]$")

# Prozesse in Containern/LXC gehoeren nicht zu diesem Host - ein Agent-
# Plug-in auf dem Host koennte sie auch nicht ueberwachen (auf der Test-Site
# gesehen: MariaDB in einem LXC-Container, nginx/redis in Docker).
_CONTAINER_CGROUP_RE = re.compile(r"/lxc/|/docker[-/]|/libpod-|/machine\.slice/|/kubepods")

_PLUGIN_SECTIONS = ("checkmk_agent_plugins_lnx", "checkmk_agent_plugins_win")
# '<pfad>:CMK_VERSION="..."' bzw. '<pfad>:__version__ = "..."' (Python-
# Plug-ins); Windows-Pfade enthalten selbst ein ':' ("C:\\...").
_PLUGIN_LINE_RE = re.compile(r"^(.*?):\s*(?:CMK_VERSION|__version__)\b")


def _subsection_lines(lines: Sequence[str], name: str) -> list[str]:
    """Zeilen eines Unterabschnitts ("[name]") einer Section."""
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
    """'<section>' oder '<section>/<unterabschnitt>' -> Zeilen, None wenn
    die Section fehlt."""
    name, _sep, sub = ref.partition("/")
    lines = sections.get(name)
    if lines is None:
        return None
    return _subsection_lines(lines, sub) if sub else lines


def _data_lines(lines: Sequence[str]) -> list[str]:
    """Echte Datenzeilen: nicht leer, kein Unterabschnitts-Kopf, kein
    no_data_lines-Treffer (z.B. "no pools available")."""
    patterns = [re.compile(p) for p in NO_DATA_LINES]
    return [
        line for line in lines
        if line.strip()
        and not _SUBSECTION_RE.match(line.strip())
        and not any(p.search(line) for p in patterns)
    ]


def _section_has_data(name: str, lines: Sequence[str]) -> bool:
    """Liefert die Section echte Daten? Standard: mind. eine Datenzeile.
    section_data.<name> (Regel-Datei) verschaerft das auf "mind. eine
    Datenzeile matcht <regex>" - z.B. FreeBSD-zfs_arc_cache, das nur
    Nullwerte liefert, wenn ZFS gar nicht benutzt wird."""
    data = _data_lines(lines)
    pattern = SECTION_DATA.get(name)
    if pattern is None:
        return bool(data)
    regex = re.compile(pattern)
    return any(regex.search(line) for line in data)


def _plugin_token(file_name: str) -> str:
    """Plug-in-Dateiname -> Token; 'mk_mysql' -> 'mysql' usw."""
    token = _canonical_token(file_name)
    if token in TITLES:
        return token
    lowered = file_name.strip().lower()
    for prefix in ("mk_", "mk-"):
        if lowered.startswith(prefix):
            return _canonical_token(lowered[len(prefix):])
    return token


def _agent_plugin_names(sections: Mapping[str, list[str]]) -> list[str]:
    """Dateinamen der ausgelieferten Agent-Plug-ins und Local Checks aus
    checkmk_agent_plugins_lnx/_win (Zeilen '<pfad>:CMK_VERSION=...')."""
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
    # 0.9.0-b21: Dienstname -> Anzeigename (fuer den generischen Abgleich)
    win_service_titles: dict[str, str] = {}


def _runtime_facts(sections: Mapping[str, list[str]]) -> _Runtime:
    """Laufende Dienste/Prozesse eines Hosts aus der Agent-Ausgabe.

    - systemd_units, Unterabschnitt [all]: '<unit> loaded active running ...'
    - ps_lnx [processes]: '<cgroup> <user> <vsz> <rss> <time> <elapsed>
      <pid> <command>'; Container-/LXC-Prozesse werden verworfen.
    - ps (Windows/BSD/aeltere Agenten): '(<user>,...)\t<command>'
    - services (Windows): '<name> <state>/<start_type> <anzeigename>'
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
        if command.startswith("["):  # Kernel-Threads
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
    """Wertet eine detect-Bedingung aus; liefert bei Treffer einen kurzen
    Beleg-Text, sonst None. Formate (monitoring_coverage_analyzer_rules.json):

      section:<sec>[/<sub>]:data              - Section liefert Daten
      section:<sec>[/<sub>]:contains:<regex>  - eine Datenzeile matcht
      systemd:<regex>     - laufende systemd-Unit ([all], active running)
      process:<regex>     - Prozessname (ohne Pfad), Container ausgenommen
      winservice:<regex>  - laufender Windows-Dienst (Dienstname)

    Regex immer per re.search - Anker (^...$) gehoeren in die Regel.
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
# 0.9.0-b21: generischer (unscharfer) Abgleich - findet Subsysteme OHNE
# Eintrag in der Regel-Datei: fuehrender Namensteil laufender systemd-
# Units, Prozesse und Windows-Dienste gegen die Familien der Agent-
# basierten Check-Plug-ins dieser Site ('cmk -L', Typ "agent"; SNMP-Plug-
# ins koennen fuer einen Agent-Host nie ein fehlendes Agent-Plug-in
# bedeuten). Filter gegen Unsinn:
#   - Familien, die die Regel-Datei kennt (TITLES/ALIASES), entscheidet
#     ausschliesslich die kuratierte Logik (z.B. ZFS braucht echte Daten).
#   - stop_tokens und generic_ignore_families der Regel-Datei.
#   - Familie auf dem Host schon ueberwacht (ein Check-Plug-in der
#     Familie laeuft).
#   - Familie laeuft auf fast allen Hosts desselben OS (Basis-OS-Dienst),
#     siehe _GENERIC_COMMON_* in _query_and_analyze_hosts().
# Eigene Ausnahmen: Setup-Regel "Monitoring coverage analysis".
# ---------------------------------------------------------------------------
_GENERIC_SUFFIX_RE = re.compile(r"\.(service|socket|timer|scope|exe)$", re.IGNORECASE)
_GENERIC_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+")
_GENERIC_MIN_LEN = 3
# Familie wird unterdrueckt, wenn sie auf >= 80 % der Hosts desselben OS
# vorkommt - nur bei mindestens 5 Hosts dieses OS.
_GENERIC_COMMON_MIN_HOSTS = 5
_GENERIC_COMMON_RATIO = 0.8
_GENERIC_MAX_EVIDENCE = 3


def _plugin_family(plugin: str) -> str:
    """'mssql_counters.locks' -> 'mssql', 'zpool_status' -> 'zpool'."""
    head = re.split(r"[_.\-]", plugin.strip().lower())[0]
    return re.sub(r"\d+$", "", head)


def _generic_name_tokens(*names: str) -> list[str]:
    """Fuehrender Namensteil (plus erstes CamelCase-Wort) je Name:
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
    """Gleich, oder Familie ist Praefix mit kurzem Rest ('postgresql' ->
    'postgres', 'mssqlserver' -> 'mssql'). Kurze Familien (3 Zeichen) nur
    exakt."""
    if token == family:
        return True
    return len(family) >= 4 and token.startswith(family) and len(token) - len(family) <= 6


class _GenericFamily(NamedTuple):
    title: str
    plugins: list[str]


def _generic_catalog() -> dict[str, _GenericFamily]:
    """Familie -> (Titel, Plug-ins) aller Agent-basierten Check-Plug-ins,
    die NICHT von der Regel-Datei abgedeckt sind. Titel aus dem Katalog-
    Titel ('ACME SBC: Health' -> 'ACME SBC')."""
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
    for family, entries in grouped.items():
        catalog[family] = _GenericFamily(
            _generic_title(family, [t for _n, t in entries]), sorted(n for n, _t in entries)
        )
    return catalog


def _generic_title(family: str, titles: Sequence[str]) -> str:
    """Titel aus den Katalog-Titeln: gemeinsame fuehrende Woerter der Teile
    vor ':' ('Couchbase Nodes: ...', 'Couchbase Buckets: ...' ->
    'Couchbase'); ohne ':' im Katalog-Titel der Familienname."""
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


def _generic_candidates(
    runtime: _Runtime,
    check_commands: Sequence[str],
    catalog: Mapping[str, _GenericFamily],
) -> dict[str, list[str]]:
    """Familie -> Belege fuer einen Host (ohne OS-Haeufigkeitsfilter)."""
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
            if token in STOP_TOKENS:
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
    """Generische Kandidaten aller Hosts, ohne Familien, die auf fast allen
    Hosts desselben OS (Label cmk/os_family) laufen."""
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
            _runtime_facts(sec.sections), check_commands_by_host.get(host_name, []), catalog
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
    # 0.9.0-b14: "delivered" (Section liefert Daten, Discovery fehlt) |
    # "deployed" (Plug-in ausgeliefert, aber keine eigene Section - z.B.
    # Ausgabe geht als Piggyback an andere Hosts) | "running" (Dienst/
    # Prozess laeuft, Plug-in fehlt) | "label" (nur Host-Label)
    kind: str = ""


class _HostResult(NamedTuple):
    host_name: str
    status: str
    coverage_pct: int
    fraction_text: str
    findings: str
    # Flache Darstellung inkl. Zwischenueberschriften ("Unmonitored:",
    # "Already monitored:", "Sources:") - wird so 1:1 als Long Output des
    # Piggyback-Service ausgegeben.
    detail_lines: list[str]
    capability_summary: str
    # 0.9.0-b13: strukturierte Detail-Abschnitte fuer die GUI-Seite. Defaults,
    # damit aeltere Cache-Dateien (ohne diese Felder) weiter ladbar sind -
    # die Seite faellt dann auf die flachen detail_lines zurueck.
    unmonitored_lines: list[str] = []
    monitored_lines: list[str] = []
    source_lines: list[str] = []
    # 0.9.0-b21: ungefilterte Items (siehe lib/evaluate.py). Status und
    # Texte oben sind die Auswertung OHNE Setup-Regel; Seite und Check
    # werten die Items mit der fuer den Host gueltigen Regel neu aus.
    items: list[dict[str, Any]] = []
    candidate_lines: list[str] = []
    ignored_lines: list[str] = []


def _monitored_map_for_host(check_commands: Sequence[str]) -> dict[str, list[str]]:
    """Ermittelt aus den Livestatus-check_command-Werten eines Hosts die
    Menge der bereits ueberwachten (kanonisierten) Plugin-Tokens, je Token
    mit den konkret ausloesenden Plugin-Namen (fuer die "via ..."-Anzeige).

    Checkmk-generierte Check-Commands haben das Praefix "check_mk-",
    gefolgt vom Plugin-Namen, z.B. "check_mk-mysql_capacity" oder
    "check_mk-apache_status". Der aktive Check fuer das HW/SW-Inventory
    ("Checkmk HW/SW Inventory"-Service) hat stattdessen das Praefix
    "check_mk_active-cmk_inv" (siehe Schritt 3 / T_SECTION "inventory"
    oben) - wird hier explizit auf den Token "inventory" abgebildet,
    damit "mk_inventory ist vorhanden, aber der HW/SW-Inventory-Service
    laeuft nicht" korrekt als offenes Finding erkannt wird.
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
    """Detail-Abschnitte der Seite (gleiche Reihenfolge wie im Long Output
    des Service); leere Abschnitte entfallen."""
    params = lookup.params_for(result.host_name) if result.items else {}
    mode = str(params.get("generic_candidates", _ev.GENERIC_INFO))
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


def _analyze_host(
    host_name: str,
    labels: Mapping[str, str],
    check_commands: Sequence[str],
    available_map: dict[str, list[str]],
    agent_sections: _AgentSections | None = None,
    generic_found: Mapping[str, list[str]] | None = None,
    generic_catalog: Mapping[str, _GenericFamily] | None = None,
) -> _HostResult:
    """Coverage-Korrelation fuer einen einzelnen Host (Stand 0.9.0-b14).

    Belege je Token (Typ -> Liste), nur Tokens aus TITLES zaehlen:
      - T_CHECK:   bereits aktiver check_command (Livestatus) - zugleich
                   "ueberwacht".
      - T_LABEL:   Host-Label (Name oder Wert) bildet auf das Token ab.
      - T_SECTION: Section der Agent-Ausgabe (get-agent-output @cached),
                   deren Name auf das Token abbildet und die echte Daten
                   liefert (siehe _section_has_data()), oder eine
                   detect.<token>.direct-Regel greift.
      - T_PLUGIN:  ausgeliefertes Agent-Plug-in / Local Check laut
                   checkmk_agent_plugins_lnx/_win.
      - T_RUNTIME: detect.<token>.runtime-Regel greift (laufende
                   systemd-Unit, Prozess ausserhalb von Containern,
                   laufender Windows-Dienst).

    Ein Token mit Beleg, fuer das auf der Site ein Check-Plugin existiert
    (available_map), ist ein "monitorable subsystem". Ohne passenden
    check_command ist es ein offenes Finding - seit b14 immer WARN, der
    Text unterscheidet "Daten kommen an / Plug-in ausgeliefert" (Discovery
    fehlt) von "laeuft" (Agent-Plug-in fehlt).

    0.9.0-b21: dazu generische Kandidaten (generic_found, siehe
    _generic_candidates()) und Rueckgabe der ungefilterten Items - die
    Auswertung (Status, Coverage, Texte) macht lib/evaluate.py.

    Installierte Pakete aus dem HW/SW-Inventory (T_INV_PACKAGE) sind seit
    b14 KEIN Beleg mehr (False Positives, z.B. lvm2 ohne ein einziges LV),
    sondern nur eine Info-Zeile unter "Sources". Hosts ohne Agent-Ausgabe
    (keine Cache-Datei) haben damit nur noch T_CHECK/T_LABEL.
    """
    monitored_map = _monitored_map_for_host(check_commands)
    inv_packages = _inventory_package_names(host_name)
    if agent_sections is None:
        agent_sections = _agent_sections(host_name)
    raw_sections = agent_sections.sections
    agent_plugins = _agent_plugin_names(raw_sections)
    runtime = _runtime_facts(raw_sections)

    # Kapabilitaeten je Token sammeln (Typ -> Belege).
    capability_evidence: dict[str, dict[str, list[str]]] = {}

    def _add_capability(token: str, cap_type: str, evidence: str) -> None:
        by_type = capability_evidence.setdefault(token, {})
        items = by_type.setdefault(cap_type, [])
        if evidence not in items:
            items.append(evidence)

    # T_CHECK (0.9.0-b6): ein bereits aktiver, ueberwachter check_command
    # ist selbst ein Kapabilitaets-Beleg (siehe Docstring oben) - rein
    # generisch ueber TITLES (JSON-gespeist), keine Subsystem-Sonderfaelle.
    for token, plugin_names in monitored_map.items():
        if token in TITLES:
            _add_capability(token, T_CHECK, f"check_command(s)='{', '.join(plugin_names)}'")

    for name, value in labels.items():
        for raw in (name, value):
            token = _canonical_token(str(raw))
            if token in TITLES:
                _add_capability(token, T_LABEL, f"host_label='{name}:{value}'")

    # T_SECTION (0.9.0-b14): Section-Name -> Token, nur wenn die Section
    # echte Daten liefert (leere Section / nur "[df]"-Kopf zaehlt nicht).
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

    # detect-Regeln: "direct" wirkt wie T_SECTION (Daten kommen an),
    # "runtime" ist der indirekte Beleg (Dienst/Prozess laeuft).
    for token, spec in DETECT.items():
        if token not in TITLES:
            continue
        for kind, cap_type in (("direct", T_SECTION), ("runtime", T_RUNTIME)):
            for condition in spec.get(kind, []):
                hit = _match_condition(condition, raw_sections, runtime)
                if hit:
                    _add_capability(token, cap_type, hit)

    # 0.9.0-b17: detect.<token>.not_on_os - Plug-in laeuft auf diesem
    # Betriebssystem nicht (z.B. openvpn_clients ist ein Bash-Skript, kein
    # Windows-Plug-in). Vergleich gegen das Host-Label cmk/os_family;
    # ohne Label keine Einschraenkung.
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
            # Kapabilitaet erkannt, aber auf dieser Site gar kein
            # passendes Check-Plugin verfuegbar - kein "monitorable
            # subsystem" (nur verfuegbare Plugins zaehlen in den Bruch).
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
        # 0.9.0-b14: alle offenen Findings erstmal WARN (User-Vorgabe, im
        # Livebetrieb zu bewerten). Die Art des Belegs bestimmt nur den
        # Text: Daten kommen schon an -> Discovery fehlt; Dienst laeuft ->
        # Agent-Plug-in fehlt.
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

    # 0.9.0-b21: ungefilterte Items; Status/Coverage/Texte entstehen in
    # lib/evaluate.py (gemeinsam mit dem Check-Plugin).
    items: list[dict[str, Any]] = []
    for s in subsystems:
        if s.monitored:
            items.append({
                "kind": "monitored", "token": s.token, "title": s.title,
                "plugins": list(s.via), "evidence": list(s.evidence), "state": "monitored",
            })
            continue
        state_txt = {
            "delivered": _("agent delivers data, not monitored"),
            "deployed": _("agent plug-in deployed, not monitored"),
            "running": _("running, not monitored"),
        }.get(s.kind, _("detected, not monitored"))
        if s.kind in ("delivered", "deployed"):
            # Daten sind schon da - ein Plug-in-Deployment-Hinweis waere
            # hier falsch, es fehlt nur die Service-Discovery.
            hint = _("Run service discovery for this host.")
        else:
            hint = HINTS.get(s.token, "")
        items.append({
            "kind": "open", "token": s.token, "title": s.title,
            "plugins": list(s.via), "evidence": list(s.evidence),
            "state": state_txt, "hint": hint,
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
            "state": _("running, not monitored (generic match)"),
            "hint": _("Check if an agent plug-in or special agent for '%s' exists and deploy it, then run discovery.") % entry.title,
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
    # T_INV_PACKAGE (0.9.0-b14): nur Info - Pakete, die auf ein
    # verfuegbares, nicht ueberwachtes Subsystem hindeuten, fuer das die
    # Agent-Ausgabe aber keinen Beleg liefert. Beeinflusst weder Status
    # noch Coverage.
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
    # Transparenz: ohne Agent-Ausgabe gibt es keine Section-/Laufzeit-
    # Belege - das soll sichtbar sein.
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
    """Auswertung der Items mit den Parametern der Setup-Regel (None = ohne
    Regel, Default-Verhalten) -> _HostResult fuer Anzeige/Cache."""
    evaluation = _ev.evaluate(items, params)
    mode = str((params or {}).get("generic_candidates", _ev.GENERIC_INFO))
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
    )


# 0.9.0-b22: Betriebssysteme, deren Hosts analysiert werden.
_AGENT_OS_TYPES = frozenset({"linux", "windows", "freebsd", "solaris", "aix"})


def _host_os(labels: Mapping[str, str]) -> str:
    """OS des Hosts aus den Agent-Labels: cmk/os_type (z.B. "linux" auch
    fuer UniFi OS / OpenWrt, deren cmk/os_family abweicht), bei aelteren
    Agenten ohne cmk/os_type ersatzweise cmk/os_family."""
    value = labels.get("cmk/os_type") or labels.get("cmk/os_family") or ""
    return str(value).strip().lower()


def _query_and_analyze_hosts() -> Sequence[_HostResult]:
    """Ermittelt per Livestatus alle Hosts, die per Checkmk-Agent (TCP)
    ueberwacht werden, und wendet auf jeden Host die Coverage-Korrelation
    aus _analyze_host() an.

    Die Filterung nach "agent = cmk-agent" erfolgt Python-seitig ueber die
    von Livestatus gelieferte tags-Spalte (statt als Livestatus-Filter-Zeile),
    weil Checkmk 2.5 fuer Host-Tag-Filter das Format
    "Filter: tags = <tag_group_id> <tag_id>" erwartet und keine
    "tags_<tag_group_id> = <tag_id>"-Kurzschreibweise mehr unterstuetzt.
    """
    connection = sites.live()
    try:
        _reload_rules()
    except RulesLoadError as exc:
        # Rein daten-basierte Regel-Erkennung (siehe Modul-Docstring): kein
        # stiller Code-Fallback. Ein Problem mit der Regel-Datei fuehrt zu
        # einem klaren, fuer den Benutzer sichtbaren Fehler statt zu einem
        # Analyse-Ergebnis unklarer Herkunft.
        html.show_error(
            "Coverage-Regeln konnten nicht geladen werden - Analyse "
            f"abgebrochen: {exc}"
        )
        return []
    try:
        rows = connection.query(
            "GET hosts\n"
            "Columns: name tags labels\n"
        )
        # services_with_info/-fullstate liefern KEIN check_command; dafuer
        # separat alle Service-Check-Commands je Host abfragen (eine
        # einzige Livestatus-Query fuer den gesamten Analyse-Lauf, nicht
        # pro Host).
        service_rows = connection.query(
            "GET services\n"
            "Columns: host_name check_command\n"
        )
    except Exception as exc:  # pragma: no cover - debug aid
        html.show_error(f"Livestatus-Fehler: {exc!r}")
        return []

    check_commands_by_host: dict[str, list[str]] = {}
    for host_name, check_command in service_rows:
        if isinstance(check_command, str) and check_command:
            check_commands_by_host.setdefault(host_name, []).append(check_command)

    # "cmk -L" GENAU EINMAL fuer den gesamten Analyse-Lauf, nicht pro Host.
    available_map = _available_plugin_map()

    agent_rows: list[tuple[str, dict[str, str]]] = []
    for host_name, tags, labels in rows:
        # Hinweis: Checkmk erlaubt eigene IDs fuer die Tag-Gruppe "agent"
        # (z.B. "all-agents" statt des Default-Werts "cmk-agent") - eine
        # Pruefung auf tags["agent"] == "cmk-agent" ist daher NICHT
        # site-unabhaengig. Robuster ist das eingebaute "tcp"-Hilfs-Tag
        # (aux_tag "tcp"), das gesetzt ist, sobald ein Host per
        # Checkmk-Agent (TCP) kontaktiert wird - unabhaengig von der
        # konkreten Tag-Gruppen-/ID-Konfiguration der Site.
        tag_map = tags if isinstance(tags, dict) else {}
        is_cmk_agent_host = tag_map.get("tcp") == "tcp" or tag_map.get(
            "checkmk-agent"
        ) == "checkmk-agent"
        if not is_cmk_agent_host:
            continue
        label_map = labels if isinstance(labels, dict) else {}
        # 0.9.0-b22: das tcp-Tag allein ist zu unscharf (auch Special-Agent-
        # und Piggyback-Hosts, Router mit eigenem Agenten). Zusaetzlich muss
        # der Agent ein Betriebssystem melden, fuer das es Agent-Plug-ins
        # gibt - siehe _host_os().
        if _host_os(label_map) not in _AGENT_OS_TYPES:
            continue
        agent_rows.append((host_name, label_map))

    # 0.9.0-b7: Agent-Ausgaben aller Hosts EINMAL pro Lauf, parallel, aus
    # dem Core-Cache (get-agent-output @cached) - keine Agent-Abfrage.
    sections_by_host = _collect_agent_sections([h for h, _labels in agent_rows])

    # 0.9.0-b21: generischer Abgleich - erst fuer alle Hosts sammeln, dann
    # Familien entfernen, die auf fast allen Hosts desselben OS laufen
    # (Basis-OS-Bestandteile, ohne gepflegte Liste).
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
        )
        results.append(result)

    results.sort(key=lambda r: r.host_name)
    return results


_CACHE_FILE_REL = os.path.join("var", "check_mk", "web", "monitoring_coverage_analyzer_cache.json")


def _cache_path() -> str | None:
    omd_root = os.environ.get("OMD_ROOT", "")
    if not omd_root:
        return None
    return os.path.join(omd_root, _CACHE_FILE_REL)


def _save_cached_results(
    results: Sequence[_HostResult],
    *,
    last_full_run_timestamp: float | None = None,
    last_piggyback_refresh_timestamp: float | None = None,
) -> None:
    """Schritt 2: persistiert das Analyse-Ergebnis als JSON-Datei unterhalb
    von var/check_mk/web/ auf der Site (analog cmk.ccc.store.
    save_object_to_file() - hier bewusst ohne Import aus dem Referenz-
    Paket per einfachem json.dump nachgebaut, um keine harte Abhaengigkeit
    von internen cmk.*-Modulpfaden einzugehen, die sich zwischen
    Checkmk-Versionen aendern koennen). Wichtig: eine JSON-Datei statt
    eines In-Memory-Caches, weil der Checkmk-GUI-Apache i.d.R. mehrere
    Worker-Prozesse hat, die sich sonst NICHT den gleichen Zustand teilen
    wuerden.

    Ausbaustufe 2.0.0: uebernimmt zusaetzlich last_full_run_timestamp/
    last_piggyback_refresh_timestamp aus dem vorherigen Cache-Stand (falls
    hier nicht explizit uebergeben), damit ein reiner GUI-Aufruf (ohne
    Piggyback-Bezug) diese Werte nicht versehentlich loescht.
    """
    path = _cache_path()
    if not path:
        return
    previous = _load_cache_raw()
    if last_full_run_timestamp is None:
        last_full_run_timestamp = previous.get("last_full_run_timestamp") if previous else None
    if last_piggyback_refresh_timestamp is None:
        last_piggyback_refresh_timestamp = (
            previous.get("last_piggyback_refresh_timestamp") if previous else None
        )
    payload = {
        "generated_at": time.time(),
        "last_full_run_timestamp": last_full_run_timestamp,
        "last_piggyback_refresh_timestamp": last_piggyback_refresh_timestamp,
        "results": [r._asdict() for r in results],
    }
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = f"{path}.tmp.{os.getpid()}"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(tmp_path, path)  # atomarer Rename, robust gg. Worker-Races
    except OSError:
        pass  # Caching ist best-effort, darf die Seite nie zum Absturz bringen.


def _load_cache_raw() -> dict[str, Any] | None:
    """Rohes JSON-Objekt aus der Cache-Datei, ungeparst (Ausbaustufe
    2.0.0: gebraucht von _save_cached_results() zum Erhalten der beiden
    Piggyback-Zeitstempel sowie von den Piggyback-Funktionen unten, die
    zusaetzlich zu den _HostResult-Feldern auch last_full_run_timestamp/
    last_piggyback_refresh_timestamp lesen muessen)."""
    path = _cache_path()
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _load_cached_results() -> tuple[float, Sequence[_HostResult]] | None:
    payload = _load_cache_raw()
    if payload is None:
        return None
    try:
        results = [_HostResult(**row) for row in payload["results"]]
        return float(payload["generated_at"]), results
    except (KeyError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Ausbaustufe 2.0.0: Pro-Host-Piggyback-Erzeugung.
# ---------------------------------------------------------------------------


def _build_piggyback_payload(
    result: _HostResult,
    *,
    last_full_run_timestamp: float,
    last_piggyback_refresh_timestamp: float,
) -> bytes:
    """Baut das JSON-Payload fuer die Piggyback-Section
    "checkmk_monitoring_coverage" eines einzelnen Hosts (sep(0), reine
    JSON-Zeile - analog zum Section-Aufbau des Referenzprojekts
    monitoring_coverage, dort aber NICHT importiert, nur strukturell
    nachgebaut). Enthaelt bewusst BEIDE Zeitstempel (Plan-Entscheidung
    11), damit das Check-Plugin sie unabhaengig von der Cache-Datei der
    GUI-Seite direkt aus der Piggyback-Section herauslesen kann.
    """
    payload = {
        "host_name": result.host_name,
        "status": result.status,
        "coverage_pct": result.coverage_pct,
        "fraction_text": result.fraction_text,
        "findings": result.findings,
        "detail_lines": list(result.detail_lines),
        "capability_summary": result.capability_summary,
        # 0.9.0-b21: ungefilterte Items + Quellen - das Check-Plugin wertet
        # sie mit der Setup-Regel des Hosts aus (status/findings oben sind
        # nur der Stand ohne Regel, fuer aeltere Check-Plugins).
        "items": list(result.items),
        "source_lines": list(result.source_lines),
        "last_full_run_timestamp": last_full_run_timestamp,
        "last_piggyback_refresh_timestamp": last_piggyback_refresh_timestamp,
    }
    line = json.dumps(payload, ensure_ascii=False)
    return line.encode("utf-8") + b"\n"


def _write_piggyback_data(
    results: Sequence[_HostResult],
    *,
    last_full_run_timestamp: float,
    last_piggyback_refresh_timestamp: float,
) -> tuple[int, str | None]:
    """Schreibt fuer jeden Host aus 'results' die Piggyback-Rohdaten via
    der offiziellen API cmk.piggyback.backend.store_piggyback_raw_data()
    (Plan-Entscheidung 3 - KEIN rohes Dateisystem-Schreiben). source_
    hostname ist IMMER PIGGYBACK_SOURCE_HOSTNAME ("monitoring_coverage_
    analyzer", Plan-Entscheidung 4) - store_piggyback_raw_data() isoliert
    Piggyback-Daten pro source_hostname, andere Piggyback-Quellen auf
    denselben Zielhosts werden also durch diesen Aufruf nicht beruehrt
    (Plan-Entscheidung 13).

    Rueckgabe: (Anzahl erfolgreich geschriebener Hosts, Fehlertext oder
    None). Best-effort: ein einzelner kaputter Host bricht nicht den
    gesamten Lauf ab (siehe try/except je Host).
    """
    if _store_piggyback_raw_data is None:
        return 0, (
            "cmk.piggyback.backend.store_piggyback_raw_data konnte nicht "
            "importiert werden - Piggyback-Erzeugung ist auf dieser "
            "Checkmk-Version nicht verfuegbar (Import-Pfad hat sich "
            "vermutlich geaendert)."
        )
    omd_root = os.environ.get("OMD_ROOT", "")
    if not omd_root:
        return 0, "OMD_ROOT ist nicht gesetzt - Piggyback-Erzeugung uebersprungen."

    from pathlib import Path as _Path

    message_timestamp = time.time()
    written = 0
    last_error: str | None = None
    for result in results:
        if not _SAFE_HOST_RE.match(result.host_name):
            continue
        section_line = _build_piggyback_payload(
            result,
            last_full_run_timestamp=last_full_run_timestamp,
            last_piggyback_refresh_timestamp=last_piggyback_refresh_timestamp,
        )
        # sep(0)-Konvention: Section-Header <<<name:sep(0)>>> gefolgt von
        # genau einer Zeile JSON (sep(0) bedeutet "kein Feldtrenner
        # noetig", die komplette Zeile ist ein einzelnes Feld) - identisch
        # zur Section-Konvention, die auch das Referenz-Check-Plugin fuer
        # JSON-Payloads verwendet (agent_section_monitoring_coverage_
        # serverinfo im Referenzprojekt, dort AgentSection ohne
        # separator=... Parameter = Standard sep(0)).
        raw = (
            f"<<<{PIGGYBACK_SECTION_NAME}:sep(0)>>>\n".encode("utf-8")
            + section_line
        )
        try:
            _store_piggyback_raw_data(
                source_hostname=PIGGYBACK_SOURCE_HOSTNAME,
                piggybacked_raw_data={result.host_name: [raw]},
                message_timestamp=message_timestamp,
                contact_timestamp=message_timestamp,
                omd_root=_Path(omd_root),
            )
            written += 1
        except Exception as exc:  # pragma: no cover - defensiv, PoC/GUI-Kontext
            last_error = f"{result.host_name}: {exc!r}"
    return written, last_error


def _remove_stale_piggyback(results: Sequence[_HostResult]) -> list[str]:
    """0.9.0-b22: entfernt die Piggyback-Dateien dieser Quelle fuer Hosts,
    die nicht mehr analysiert werden (z.B. nach Aenderung der Host-
    Auswahl) - sonst bliebe dort ein veraltetes Ergebnis liegen. Die
    Piggyback-API hat dafuer keine Funktion (cleanup_piggyback_files()
    raeumt nur nach Alter auf); betroffen ist ausschliesslich die Datei
    <piggyback>/<host>/monitoring_coverage_analyzer."""
    omd_root = os.environ.get("OMD_ROOT", "")
    if not omd_root:
        return []
    base = os.path.join(omd_root, "tmp", "check_mk", "piggyback")
    current = {r.host_name for r in results}
    removed: list[str] = []
    try:
        hosts = os.listdir(base)
    except OSError:
        return []
    for host_name in hosts:
        if host_name in current or not _SAFE_HOST_RE.match(host_name):
            continue
        path = os.path.join(base, host_name, PIGGYBACK_SOURCE_HOSTNAME)
        try:
            os.remove(path)
            removed.append(host_name)
        except FileNotFoundError:
            continue
        except OSError:
            continue
    return removed


def _run_piggyback_full(results: Sequence[_HostResult]) -> tuple[int, str | None, float]:
    """Voller Piggyback-Schreib-Lauf mit NEUEM Inhalt: wird direkt nach
    einem echten Full-Run aufgerufen (frisch berechnete 'results'). Setzt
    BEIDE Zeitstempel auf 'jetzt' und persistiert sie im Cache.
    """
    now = time.time()
    written, error = _write_piggyback_data(
        results,
        last_full_run_timestamp=now,
        last_piggyback_refresh_timestamp=now,
    )
    _remove_stale_piggyback(results)
    _save_cached_results(
        results,
        last_full_run_timestamp=now,
        last_piggyback_refresh_timestamp=now,
    )
    return written, error, now


def _run_piggyback_refresh() -> tuple[int, str | None]:
    """Reiner Refresh-Tick (Plan-Entscheidung 7/11): schreibt den ZULETZT
    GECACHTEN Inhalt (keine neue Livestatus-Query!) mit einem NEUEN
    message_timestamp erneut per store_piggyback_raw_data(). Nur
    "last_piggyback_refresh_timestamp" aendert sich dabei -
    "last_full_run_timestamp" bleibt unveraendert (zentrale
    Korrektheitsbedingung des Features, siehe Plan-Entscheidung 11/
    Deliverable 6).

    Gibt (0, Fehlertext) zurueck, wenn noch kein Cache existiert (z.B.
    unmittelbar nach Installation, bevor der erste Full-Run gelaufen
    ist) - ein Refresh-Tick kann ohne vorherigen Full-Run keinen Inhalt
    haben.
    """
    payload = _load_cache_raw()
    if payload is None:
        return 0, "Kein Cache vorhanden - noch kein Full-Run gelaufen."
    try:
        results = [_HostResult(**row) for row in payload["results"]]
        last_full_run_timestamp = float(payload["last_full_run_timestamp"])
    except (KeyError, TypeError, ValueError):
        return 0, "Cache-Datei enthaelt keinen gueltigen Full-Run-Zeitstempel."

    now = time.time()
    written, error = _write_piggyback_data(
        results,
        last_full_run_timestamp=last_full_run_timestamp,
        last_piggyback_refresh_timestamp=now,
    )
    # last_full_run_timestamp bewusst UNVERAENDERT weiterreichen - nur der
    # Refresh-Zeitstempel wird aktualisiert (siehe Docstring oben).
    _save_cached_results(
        results,
        last_full_run_timestamp=last_full_run_timestamp,
        last_piggyback_refresh_timestamp=now,
    )
    return written, error


def _perfometer_style(coverage_pct: int) -> str:
    """Schritt 1c: inline-CSS-Hintergrund analog zum Perf-o-Meter-Stil in
    Checkmk-Views - ein horizontaler linear-gradient-Balken, dessen
    gefuellter Anteil coverage_pct entspricht. Farben 1:1 aus den echten
    Ampelfarben des laufenden Systems uebernommen (siehe
    themes/facelift/theme.css: .state0{background-color:#13d389},
    .state1{background-color:#ffd703}, .state2{background-color:#c83232}),
    damit Perfometer und Status-Spalte farblich konsistent sind.
    Abgerundete Ecken analog zu span.state_rounded_fill (border-radius:2px)
    in der normalen Service-Tabelle. Schrift bewusst schwarz, wie es
    Checkmk fuer state0/state1 (helle Hintergruende) ebenfalls tut.
    """
    pct = max(0, min(100, coverage_pct))
    if pct >= 90:
        fill = "#13d389"  # gruen, wie state0
    elif pct >= 50:
        fill = "#ffd703"  # gelb, wie state1
    else:
        fill = "#c83232"  # rot, wie state2
    return (
        f"background: linear-gradient(to right, {fill} 0%, {fill} {pct}%, "
        f"#e0e0e0 {pct}%, #e0e0e0 100%); text-align:center; font-weight:bold; "
        "color:#000; border-radius:4px;"
    )


def _status_to_state_class(status: str) -> str:
    """Schritt 1a: exakt dieselbe CSS-Klasse wie die normale Checkmk
    Service-Tabelle. Live auf der Test-Site (Checkmk 2.5)
    nachgeschlagen: die Statuszelle einer Service-Zeile bekommt
    class="state svcstate state<N>" mit N=0 (OK), 1 (WARN), 2 (CRIT),
    3 (UNKNOWN) - siehe cmk.gui HTML-Renderer fuer Service-Tabellen
    sowie themes/facelift/theme.css (.state.state0/.state1/.state2
    Regeln fuer die Ampelfarben gruen/gelb/rot).
    """
    return {"OK": "state0", "WARN": "state1", "CRIT": "state2"}.get(status, "state3")


def _host_link(host_name: str) -> HTML:
    """Schritt 1b: anklickbarer Link auf die "Service of host"-Seite,
    identisch zur Konvention normaler Checkmk-Views. Live auf der
    Test-Site nachgeschlagen: der eingebaute View-Name fuer die
    Host-Detailansicht (Services eines einzelnen Hosts) ist "host",
    aufgerufen als view.py?view_name=host&host=<hostname> - siehe
    cmk.gui.views.builtin_views (View-ID "host") und
    cmk.gui.utils.urls.makeuri_contextless(), das genau diese
    Kontextlos-URLs fuer Views baut.
    """
    href = makeuri_contextless(
        request, [("view_name", "host"), ("host", host_name)], filename="view.py"
    )
    return html.render_a(host_name, href=href)


# ---------------------------------------------------------------------------
# Ausbaustufe 2.0.0: Zugriff auf die globalen Setup-Optionen (siehe
# plugins/wato/monitoring_coverage_analyzer_globals.py /
# plugins/config/monitoring_coverage_analyzer.py).
# ---------------------------------------------------------------------------


def _generate_piggyback_data_enabled() -> bool:
    """Liest die globale Option 'generate_piggyback_data' (Default True,
    siehe plugins/config/monitoring_coverage_analyzer.py). Bewusst
    defensiv per getattr(): falls active_config aus irgendeinem Grund
    (z.B. Aufruf ausserhalb eines GUI-Requests) die Variable nicht
    kennt, wird der dokumentierte Default (True) angenommen statt eines
    AttributeError.
    """
    try:
        from cmk.gui.config import active_config
        return bool(getattr(active_config, "generate_piggyback_data", True))
    except Exception:  # pragma: no cover - defensiv
        return True


def _piggyback_interval_hours() -> int:
    """Liest die globale Option 'piggyback_interval_hours' (Default 24,
    siehe plugins/config/monitoring_coverage_analyzer.py)."""
    try:
        from cmk.gui.config import active_config
        return int(getattr(active_config, "piggyback_interval_hours", 24))
    except Exception:  # pragma: no cover - defensiv
        return 24


# 0.9.0-b12: der fullrun-Cron laeuft nur noch EINMAL taeglich (05:00).
# last_full_run_timestamp wird erst am ENDE eines Laufs gesetzt (z.B.
# 05:00:10) - ohne Toleranz waere der Lauf am Folgetag um 05:00:00 mit
# "age < 24h" nicht faellig und es gaebe nur noch jeden 2. Tag einen
# Full-Run. Die Toleranz muss groesser als die Laufzeit eines Full-Runs
# und kleiner als der Cron-Abstand (24 h) sein.
_FULL_RUN_DUE_GRACE_SECONDS = 3600


def _is_full_run_due(*, force: bool) -> tuple[bool, str]:
    """Prueft, ob ein neuer ECHTER Full-Run faellig ist: entweder 'force'
    ist gesetzt (manueller Re-Run-Knopf, oder ?_cron_fullrun=1&_force=1),
    oder seit dem letzten last_full_run_timestamp im Cache ist mehr Zeit
    vergangen als piggyback_interval_hours minus
    _FULL_RUN_DUE_GRACE_SECONDS (Plan-Entscheidung 8). Kein Cache
    vorhanden -> immer faellig (Erstlauf).
    """
    if force:
        return True, "forced"
    payload = _load_cache_raw()
    if payload is None:
        return True, "no cache yet"
    try:
        last = float(payload["last_full_run_timestamp"])
    except (KeyError, TypeError, ValueError):
        return True, "no last_full_run_timestamp in cache"
    interval_seconds = _piggyback_interval_hours() * 3600
    threshold = max(0, interval_seconds - _FULL_RUN_DUE_GRACE_SECONDS)
    age = time.time() - last
    if age >= threshold:
        return True, f"age {age:.0f}s >= interval {interval_seconds}s - grace {_FULL_RUN_DUE_GRACE_SECONDS}s"
    return False, f"age {age:.0f}s < interval {interval_seconds}s - grace {_FULL_RUN_DUE_GRACE_SECONDS}s"


# 0.9.0-b21: Setup-Regel "Monitoring coverage analysis" (rulesets/
# monitoring_coverage.py) - dieselbe Regel, die das Check-Plugin als
# Parameter bekommt. Die Seite wertet sie pro Host ueber die Checkmk-
# eigene Regelauswertung aus (wie "Effective parameters of" im Setup).
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
        except Exception as exc:  # pragma: no cover - defensiv, GUI-Kontext
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
        except Exception as exc:  # pragma: no cover - defensiv, GUI-Kontext
            self.error = f"{host_name}: {exc!r}"
            return {}
        if isinstance(value, dict) and "tp_default_value" in value:
            # Zeitabhaengige Parameter: die Seite nutzt den Default-Wert.
            value = value.get("tp_default_value")
        return dict(value) if isinstance(value, dict) else {}


def _apply_rules(results: Sequence[_HostResult], lookup: _RuleLookup) -> list[_HostResult]:
    """Wertet die Items jedes Hosts mit seiner Setup-Regel neu aus."""
    out: list[_HostResult] = []
    for r in results:
        if not r.items:
            out.append(r)  # Cache vor b21 ohne Items
            continue
        out.append(
            _build_result(
                r.host_name, list(r.items), list(r.source_lines), r.capability_summary,
                lookup.params_for(r.host_name),
            )
        )
    return out


def _page_breadcrumb() -> Breadcrumb:
    """Setup > Maintenance > Analyze monitoring coverage - wie bei den
    eingebauten Maintenance-Seiten (z.B. "Analyze configuration", dort via
    WatoMode.breadcrumb(): Main-Menu + Topic aus dem MainModule + Seite).
    Import von MainModuleTopicMaintenance bewusst lokal: cmk.gui.wato ist
    beim Laden der Page-Plugins evtl. noch nicht vollstaendig initialisiert."""
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
        # Ausbaustufe 2.0.0: die beiden Cron-Trigger-GET-Parameter werden
        # VOR dem normalen Seitenaufbau behandelt und liefern eine reine
        # Text-Antwort statt HTML (analog zu Checkmk-eigenen
        # Automation-/Ajax-Endpunkten) - so bleibt der Endpunkt sowohl per
        # direktem Python-Aufruf im Site-Kontext (siehe local/bin/
        # monitoring_coverage_analyzer_cron) als auch per echtem
        # authentifiziertem HTTP-GET (z.B. curl mit GUI-Session-Cookie,
        # fuer manuelle Tests) nutzbar.
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
            results = _query_and_analyze_hosts()
            written = 0
            error: str | None = None
            if _generate_piggyback_data_enabled():
                written, error, _now = _run_piggyback_full(results)
            else:
                _save_cached_results(results, last_full_run_timestamp=time.time())
            html.write_text(
                f"OK fullrun hosts={len(results)} piggyback_written={written} "
                f"error={error}\n"
            )
            return None

        make_header(html, PAGE_TITLE, _page_breadcrumb())

        # 0.9.0-b15: sichtbare Rueckmeldung, solange der Re-run laeuft - die
        # Analyse ist synchron (Seite antwortet erst nach dem Lauf, auf
        # Test-Site ca. 10-15 s). Beim Absenden: Button deaktivieren (kein
        # Doppel-Klick), Spinner + Text einblenden. Ein deaktivierter
        # Submit-Button wird NICHT mitgesendet, daher wird "_analyze" als
        # hidden input nachgereicht.
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
                "The result is cached on disk and only refreshed when you "
                "click 'Re-run analysis' below (or on first visit, if no "
                "cached result exists yet). Click the arrow before a "
                "hostname to expand the per-subsystem detail, analogous "
                "to the long output of the 'Checkmk Monitoring Coverage' "
                "service."
            )
        )
        # Schritt 2: Button umbenannt von "Start analysis now" zu
        # "Re-run analysis", weil die Seite jetzt IMMER ein (ggf.
        # zwischengespeichertes) Ergebnis zeigt - der Button loest also
        # keinen Erst-Start mehr aus, sondern explizit einen erneuten Lauf.
        html.button("_analyze", _("Re-run analysis"), cssclass="hot")
        html.open_span(id_="mca_running", style="display:none")
        html.open_span(class_="mca_spinner")
        html.close_span()
        html.write_text(
            _("Analysis running - querying all hosts, this can take a while ...")
        )
        html.close_span()
        html.hidden_fields()
        html.end_form()

        force_rerun = ctx.request.has_var("_analyze")
        self._show_results(force_rerun=force_rerun)
        return None

    def _show_results(self, force_rerun: bool) -> None:
        cached = None if force_rerun else _load_cached_results()
        if cached is not None:
            generated_at, results = cached
        else:
            # Schritt 2: kein Cache vorhanden (Erstbesuch) ODER
            # Re-Run-Button gedrueckt -> Analyse einmal ausfuehren und
            # das Ergebnis fuer alle Worker-Prozesse persistieren.
            results = _query_and_analyze_hosts()
            # Ausbaustufe 2.0.0: der 'Re-run analysis'-Knopf loest bei
            # aktivierter Piggyback-Option (Default an) zusaetzlich sofort
            # den vollen Piggyback-Full-Write aus (neuer Inhalt + beide
            # Zeitstempel neu, siehe Plan-Entscheidung 12), statt nur die
            # GUI-Cache-Datei zu aktualisieren.
            if _generate_piggyback_data_enabled():
                _run_piggyback_full(results)
            else:
                _save_cached_results(results, last_full_run_timestamp=time.time())
            generated_at = time.time()

        lookup = _RuleLookup()
        results = _apply_rules(results, lookup)

        html.h3(_("Analysis result"))
        age_txt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(generated_at))
        html.p(_("Result from: %s (cached until next re-run)") % age_txt)
        html.p(_("Findings/hints rules source: %s") % _rules_source_status())
        rules_txt = _("Setup rule 'Monitoring coverage analysis': %d rule(s)") % lookup.rule_count
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
        html.th("")  # Aufklapp-Pfeil-Spalte
        html.th(_("Hostname"))
        html.th(_("Status"))
        html.th(_("Coverage"))
        html.th(_("Subsystems"))
        html.th(_("Findings"))
        html.close_tr()
        for idx, result in enumerate(results):
            row_id = f"mca_detail_{idx}"
            html.open_tr(class_="even0" if idx % 2 == 0 else "odd0")
            html.open_td()
            # Reines onclick + style.display-Umschalten, ohne externe
            # JS-Libraries: der Pfeil dreht sich per CSS-Transform und die
            # Detailzeile wird per style.display ein-/ausgeblendet.
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
            # Schritt 1b: Hostname als klickbarer Link auf "Service of host".
            html.open_td()
            html.write_html(_host_link(result.host_name))
            html.close_td()
            # Schritt 1a: exakt dieselbe CSS-Klasse UND dieselbe innere
            # Markup-Struktur wie die normale Checkmk Service-Tabelle
            # ("state svcstate state<N>" + inneres span.state_rounded_fill
            # fuer die abgerundeten Ecken - live auf der Test-Site per
            # curl gegen view.py?view_name=host verifiziert).
            html.open_td(class_=f"state svcstate {_status_to_state_class(result.status)}")
            html.open_span(class_="state_rounded_fill")
            html.write_text(result.status)
            html.close_span()
            html.close_td()
            # Schritt 1c: Perf-o-Meter-artiger Hintergrundbalken.
            html.open_td(style=_perfometer_style(result.coverage_pct))
            html.write_text(f"{result.coverage_pct}%")
            html.close_td()
            html.td(result.fraction_text)
            html.td(result.findings)
            html.close_tr()

            html.open_tr(id_=row_id, style="display:none")
            html.open_td()
            html.close_td()
            html.open_td(colspan=5)
            sections = _detail_sections_for(result, lookup)
            if sections:
                for heading, lines in sections:
                    # Kein html.h4() in dieser Checkmk-Version (nur h1-h3);
                    # fettes div statt h3, das auf der Seite schon als
                    # Abschnittstitel ("Analysis result") benutzt wird.
                    html.div(heading, style="font-weight:bold; margin-top:6px")
                    html.open_ul()
                    for line in lines:
                        html.li(line)
                    html.close_ul()
            else:
                # Aeltere Cache-Datei ohne strukturierte Felder.
                html.open_ul()
                for line in result.detail_lines:
                    html.li(line)
                html.close_ul()
            html.close_td()
            html.close_tr()
        html.close_table()
        html.p(_("Number of hosts checked: %d") % len(results))


page_registry.register(PageEndpoint("monitoring_coverage_analyzer", PageMonitoringCoverageAnalyzer()))
