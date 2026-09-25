#!/usr/bin/env python3
"""Laufzustand von monitoring_coverage_analyzer OHNE Checkmk-GUI.

Alles, was das Cron-Skript fuer die haeufigen, schnellen Wege braucht
(Piggyback-Refresh alle 5 min, Pruefung "Full-Run faellig?"), sowie der
Zustand des Analyse-Laufs im Hintergrund. Bewusst ohne cmk.gui-Importe:
das Hochfahren der GUI (main_modules.register) kostet je nach System
3-20 s und wird nur noch fuer einen echten Analyse-Lauf gebraucht.

Wird auch von der GUI-Seite benutzt (Cache-Datei, Piggyback-Schreiben,
Start und Status des Hintergrund-Laufs), damit es nur EINE Implementierung
gibt.
"""
from __future__ import annotations

import ast
import contextlib
import fcntl
import glob
import json
import os
import re
import subprocess
import time
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

PIGGYBACK_SOURCE_HOSTNAME = "monitoring_coverage_analyzer"
PIGGYBACK_SECTION_NAME = "checkmk_monitoring_coverage"

SAFE_HOST_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")

_VAR_DIR_REL = os.path.join("var", "check_mk", "web")
CACHE_FILE_NAME = "monitoring_coverage_analyzer_cache.json"
_CACHE_LOCK_NAME = "monitoring_coverage_analyzer_cache.lock"
_JOB_LOCK_NAME = "monitoring_coverage_analyzer_job.lock"
_JOB_STATE_NAME = "monitoring_coverage_analyzer_job.json"
_JOB_LOG_REL = os.path.join("var", "log", "monitoring_coverage_analyzer.log")
CRON_SCRIPT_REL = os.path.join("local", "bin", "monitoring_coverage_analyzer_cron")

# Der fullrun-Cron laeuft einmal taeglich. last_full_run_timestamp wird erst
# am ENDE eines Laufs gesetzt - ohne Toleranz waere der Lauf am Folgetag zur
# selben Uhrzeit "noch nicht faellig" und es gaebe nur jeden 2. Tag einen
# Full-Run. Die Toleranz muss groesser als die Laufzeit eines Full-Runs sein.
FULL_RUN_DUE_GRACE_SECONDS = 3600

# Defaults der globalen Einstellungen (siehe web/plugins/config/...)
_GLOBAL_DEFAULTS: dict[str, Any] = {
    "generate_piggyback_data": True,
    "piggyback_interval_hours": 24,
}


def omd_root() -> str:
    return os.environ.get("OMD_ROOT", "")


def _var_path(name: str) -> str | None:
    root = omd_root()
    return os.path.join(root, _VAR_DIR_REL, name) if root else None


def cache_path() -> str | None:
    return _var_path(CACHE_FILE_NAME)


# ---------------------------------------------------------------------------
# Globale Einstellungen ohne GUI lesen
# ---------------------------------------------------------------------------


def global_setting(name: str) -> Any:
    """Liest eine globale Einstellung dieses Pakets aus den Konfigurations-
    dateien der GUI (etc/check_mk/multisite.mk und multisite.d/**/*.mk, in
    derselben Reihenfolge wie die GUI - spaetere Zuweisungen gewinnen).
    Ausgewertet werden nur einfache Zuweisungen 'name = <Literal>'; alles
    andere liefert den Default."""
    root = omd_root()
    value = _GLOBAL_DEFAULTS.get(name)
    if not root:
        return value
    base = os.path.join(root, "etc", "check_mk")
    files = [os.path.join(base, "multisite.mk")]
    files += sorted(glob.glob(os.path.join(base, "multisite.d", "**", "*.mk"), recursive=True))
    for path in files:
        try:
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
        except OSError:
            continue
        if name not in source:
            continue
        try:
            tree = ast.parse(source, path)
        except SyntaxError:
            continue
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == name
            ):
                try:
                    value = ast.literal_eval(node.value)
                except ValueError:
                    pass
    return value


def generate_piggyback_data_enabled() -> bool:
    return bool(global_setting("generate_piggyback_data"))


def piggyback_interval_hours() -> int:
    try:
        return int(global_setting("piggyback_interval_hours"))
    except (TypeError, ValueError):
        return int(_GLOBAL_DEFAULTS["piggyback_interval_hours"])


# ---------------------------------------------------------------------------
# Cache-Datei (Ergebnis des letzten Analyse-Laufs)
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _file_lock(name: str) -> Iterator[None]:
    """Kurzer exklusiver Lock (blockierend) - verhindert, dass ein Refresh-
    Tick zwischen Lesen und Schreiben ein gerade fertiges neues Ergebnis
    mit dem alten ueberschreibt."""
    path = _var_path(name)
    if not path:
        yield
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def load_cache_raw() -> dict[str, Any] | None:
    path = cache_path()
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json_atomic(path: str, payload: Mapping[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp.{os.getpid()}"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    os.replace(tmp_path, path)


def save_results(
    results: Sequence[Mapping[str, Any]],
    *,
    last_full_run_timestamp: float | None = None,
    last_piggyback_refresh_timestamp: float | None = None,
    last_run_duration_seconds: float | None = None,
    generated_at: float | None = None,
) -> None:
    """Schreibt das Ergebnis als JSON (atomarer Rename, Apache hat mehrere
    Worker-Prozesse). Nicht uebergebene Zeitstempel/Laufzeit werden aus dem
    vorherigen Stand uebernommen. Best-effort: Fehler werden verschluckt."""
    path = cache_path()
    if not path:
        return
    try:
        with _file_lock(_CACHE_LOCK_NAME):
            previous = load_cache_raw() or {}
            payload = {
                "generated_at": generated_at if generated_at is not None else time.time(),
                "last_run_duration_seconds": (
                    last_run_duration_seconds
                    if last_run_duration_seconds is not None
                    else previous.get("last_run_duration_seconds")
                ),
                "last_full_run_timestamp": (
                    last_full_run_timestamp
                    if last_full_run_timestamp is not None
                    else previous.get("last_full_run_timestamp")
                ),
                "last_piggyback_refresh_timestamp": (
                    last_piggyback_refresh_timestamp
                    if last_piggyback_refresh_timestamp is not None
                    else previous.get("last_piggyback_refresh_timestamp")
                ),
                "results": list(results),
            }
            _write_json_atomic(path, payload)
    except OSError:
        pass


def is_full_run_due(*, force: bool) -> tuple[bool, str]:
    """Neuer Full-Run faellig? 'force' (Re-run-Knopf) oder seit dem letzten
    Full-Run ist piggyback_interval_hours minus Toleranz vergangen. Ohne
    Cache immer faellig (Erstlauf)."""
    if force:
        return True, "forced"
    payload = load_cache_raw()
    if payload is None:
        return True, "no cache yet"
    try:
        last = float(payload["last_full_run_timestamp"])
    except (KeyError, TypeError, ValueError):
        return True, "no last_full_run_timestamp in cache"
    interval_seconds = piggyback_interval_hours() * 3600
    threshold = max(0, interval_seconds - FULL_RUN_DUE_GRACE_SECONDS)
    age = time.time() - last
    if age >= threshold:
        return True, f"age {age:.0f}s >= interval {interval_seconds}s - grace {FULL_RUN_DUE_GRACE_SECONDS}s"
    return False, f"age {age:.0f}s < interval {interval_seconds}s - grace {FULL_RUN_DUE_GRACE_SECONDS}s"


# ---------------------------------------------------------------------------
# Piggyback-Daten
# ---------------------------------------------------------------------------


def _piggyback_section(
    result: Mapping[str, Any],
    *,
    last_full_run_timestamp: float,
    last_piggyback_refresh_timestamp: float,
) -> bytes:
    """Section "checkmk_monitoring_coverage" (sep(0), eine JSON-Zeile) eines
    Hosts. Enthaelt beide Zeitstempel, damit das Check-Plugin sie direkt
    aus der Section lesen kann."""
    payload = {
        "host_name": result.get("host_name"),
        "status": result.get("status"),
        "coverage_pct": result.get("coverage_pct"),
        "fraction_text": result.get("fraction_text"),
        "findings": result.get("findings"),
        "detail_lines": list(result.get("detail_lines") or []),
        "capability_summary": result.get("capability_summary"),
        # Ungefilterte Items + Quellen - das Check-Plugin wertet sie mit der
        # Setup-Regel des Hosts aus (status/findings oben = Stand ohne Regel).
        "items": list(result.get("items") or []),
        "source_lines": list(result.get("source_lines") or []),
        "last_full_run_timestamp": last_full_run_timestamp,
        "last_piggyback_refresh_timestamp": last_piggyback_refresh_timestamp,
    }
    line = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
    return f"<<<{PIGGYBACK_SECTION_NAME}:sep(0)>>>\n".encode("utf-8") + line


def write_piggyback(
    results: Sequence[Mapping[str, Any]],
    *,
    last_full_run_timestamp: float,
    last_piggyback_refresh_timestamp: float,
) -> tuple[int, str | None]:
    """Schreibt die Piggyback-Daten aller Hosts ueber die offizielle API
    cmk.piggyback.backend.store_piggyback_raw_data(). Andere Piggyback-
    Quellen derselben Hosts bleiben unberuehrt (eigener source_hostname).
    Rueckgabe: (Anzahl geschriebener Hosts, letzter Fehler oder None)."""
    try:
        from cmk.piggyback.backend import store_piggyback_raw_data
    except ImportError:
        return 0, (
            "cmk.piggyback.backend.store_piggyback_raw_data is not available "
            "on this Checkmk version"
        )
    root = omd_root()
    if not root:
        return 0, "OMD_ROOT is not set"
    message_timestamp = time.time()
    written = 0
    last_error: str | None = None
    for result in results:
        host_name = str(result.get("host_name") or "")
        if not SAFE_HOST_RE.match(host_name):
            continue
        raw = _piggyback_section(
            result,
            last_full_run_timestamp=last_full_run_timestamp,
            last_piggyback_refresh_timestamp=last_piggyback_refresh_timestamp,
        )
        try:
            store_piggyback_raw_data(
                source_hostname=PIGGYBACK_SOURCE_HOSTNAME,
                piggybacked_raw_data={host_name: [raw]},
                message_timestamp=message_timestamp,
                contact_timestamp=message_timestamp,
                omd_root=Path(root),
            )
            written += 1
        except Exception as exc:  # pragma: no cover - defensiv
            last_error = f"{host_name}: {exc!r}"
    return written, last_error


def remove_stale_piggyback(results: Sequence[Mapping[str, Any]]) -> list[str]:
    """Entfernt die Piggyback-Datei dieser Quelle fuer Hosts, die nicht mehr
    analysiert werden - sonst bliebe dort ein veraltetes Ergebnis liegen.
    Die Piggyback-API raeumt nur nach Alter auf; betroffen ist ausschliesslich
    <piggyback>/<host>/monitoring_coverage_analyzer."""
    root = omd_root()
    if not root:
        return []
    base = os.path.join(root, "tmp", "check_mk", "piggyback")
    current = {str(r.get("host_name")) for r in results}
    removed: list[str] = []
    try:
        hosts = os.listdir(base)
    except OSError:
        return []
    for host_name in hosts:
        if host_name in current or not SAFE_HOST_RE.match(host_name):
            continue
        try:
            os.remove(os.path.join(base, host_name, PIGGYBACK_SOURCE_HOSTNAME))
            removed.append(host_name)
        except OSError:
            continue
    return removed


def run_piggyback_refresh() -> tuple[int, str | None]:
    """Refresh-Tick: schreibt das zuletzt gespeicherte Ergebnis (keine neue
    Analyse) mit neuem message_timestamp erneut. Nur last_piggyback_refresh_
    timestamp aendert sich, last_full_run_timestamp bleibt unveraendert."""
    payload = load_cache_raw()
    if payload is None:
        return 0, "no cached result yet - no full run has completed"
    try:
        results = list(payload["results"])
        last_full_run_timestamp = float(payload["last_full_run_timestamp"])
    except (KeyError, TypeError, ValueError):
        return 0, "cache file has no valid full run timestamp"
    now = time.time()
    written, error = write_piggyback(
        results,
        last_full_run_timestamp=last_full_run_timestamp,
        last_piggyback_refresh_timestamp=now,
    )
    # Nur den Refresh-Zeitstempel fortschreiben - unter dem Cache-Lock und
    # nur, wenn in der Zwischenzeit kein neuer Lauf ein anderes Ergebnis
    # gespeichert hat.
    path = cache_path()
    if path:
        try:
            with _file_lock(_CACHE_LOCK_NAME):
                current = load_cache_raw()
                if current is not None and current.get("generated_at") == payload.get("generated_at"):
                    current["last_piggyback_refresh_timestamp"] = now
                    _write_json_atomic(path, current)
        except OSError:
            pass
    return written, error


# ---------------------------------------------------------------------------
# Analyse-Lauf im Hintergrund
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def job_lock() -> Iterator[bool]:
    """Exklusiver, NICHT blockierender Lock fuer einen Analyse-Lauf. Liefert
    False, wenn bereits ein Lauf aktiv ist. Der Lock endet automatisch mit
    dem Prozess - auch wenn dieser abgebrochen wird."""
    path = _var_path(_JOB_LOCK_NAME)
    if not path:
        yield True
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def job_running() -> bool:
    path = _var_path(_JOB_LOCK_NAME)
    if not path or not os.path.exists(path):
        return False
    with open(path, "a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
    return False


def load_job_state() -> dict[str, Any]:
    path = _var_path(_JOB_STATE_NAME)
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_job_state(**fields: Any) -> None:
    path = _var_path(_JOB_STATE_NAME)
    if not path:
        return
    state = load_job_state()
    state.update(fields)
    try:
        _write_json_atomic(path, state)
    except OSError:
        pass


def start_background_rerun() -> tuple[bool, str]:
    """Startet einen Analyse-Lauf als eigenen Prozess ausserhalb von Apache
    (Cron-Skript, Modus "rerun"). Rueckgabe: (gestartet, Meldung)."""
    if job_running():
        return False, "already running"
    root = omd_root()
    if not root:
        return False, "OMD_ROOT is not set"
    script = os.path.join(root, CRON_SCRIPT_REL)
    python = os.path.join(root, "bin", "python3")
    if not os.path.exists(script):
        return False, f"{CRON_SCRIPT_REL} not found"
    log_path = os.path.join(root, _JOB_LOG_REL)
    # Status VOR dem Start setzen (der Prozess ueberschreibt ihn mit
    # "running"), damit die Seite sofort "running" zeigt, auch wenn der
    # Prozess den Lock noch nicht geholt hat.
    save_job_state(state="starting", requested=time.time(), error=None)
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "ab") as log:
            subprocess.Popen(  # noqa: S603 - fester Pfad, keine Benutzereingabe
                [python, script, "rerun"],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=root,
                start_new_session=True,  # ueberlebt den Apache-Request
                close_fds=True,
            )
    except OSError as exc:
        save_job_state(state="failed", finished=time.time(), error=f"start failed: {exc!r}")
        return False, f"{exc!r}"
    return True, "started"


# Ein "starting"-Status ohne laufenden Prozess gilt nach dieser Zeit als
# verwaist (Prozess konnte nicht starten).
_STARTING_TIMEOUT_SECONDS = 120


def job_status() -> dict[str, Any]:
    """Status fuer die Seite: state (idle/running/done/failed), started,
    finished, error. "running" nur, wenn der Lock gehalten wird oder der
    Start gerade erst angefordert wurde."""
    state = load_job_state()
    if job_running():
        state["state"] = "running"
    elif state.get("state") == "starting":
        requested = float(state.get("requested") or 0)
        if time.time() - requested < _STARTING_TIMEOUT_SECONDS:
            state["state"] = "running"
            state.setdefault("started", requested)
        else:
            state["state"] = "failed"
            state["error"] = "analysis process did not start (see var/log/monitoring_coverage_analyzer.log)"
    elif state.get("state") == "running":
        # Status sagt "running", aber kein Prozess haelt den Lock mehr ->
        # Prozess wurde abgebrochen (z.B. kill, Neustart).
        state["state"] = "failed"
        state["error"] = state.get("error") or "analysis process ended unexpectedly (see var/log/monitoring_coverage_analyzer.log)"
    state.setdefault("state", "idle")
    return state
