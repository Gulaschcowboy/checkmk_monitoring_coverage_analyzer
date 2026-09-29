#!/usr/bin/env python3
"""Run state of monitoring_coverage_analyzer WITHOUT the Checkmk GUI.

Everything the cron script needs for the frequent, fast paths
(piggyback refresh every 5 min, check "full run due?"), plus the state
of the background analysis run. Deliberately without cmk.gui imports:
starting up the GUI (main_modules.register) takes 3-20 s depending on the
system and is only needed for an actual analysis run.

Also used by the GUI page (cache file, piggyback writing, start and
status of the background run), so that there is only ONE implementation.
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

# The fullrun cron runs once a day. last_full_run_timestamp is only set at
# the END of a run - without a grace period, the run on the following day at
# the same time would be "not yet due" and there would only be a full run
# every 2nd day. The grace period must be longer than a full run takes.
FULL_RUN_DUE_GRACE_SECONDS = 3600

# Defaults of the global settings (see web/plugins/config/...)
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
# Read global settings without the GUI
# ---------------------------------------------------------------------------


def global_setting(name: str) -> Any:
    """Reads a global setting of this package from the GUI configuration
    files (etc/check_mk/multisite.mk and multisite.d/**/*.mk, in the same
    order as the GUI - later assignments win).
    Only simple assignments 'name = <literal>' are evaluated; anything
    else yields the default."""
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
# Cache file (result of the last analysis run)
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _file_lock(name: str) -> Iterator[None]:
    """Short exclusive lock (blocking) - prevents a refresh tick from
    overwriting a just-finished new result with the old one between
    reading and writing."""
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
    """Writes the result as JSON (atomic rename, Apache has several worker
    processes). Timestamps/duration not passed are taken over from the
    previous state. Best-effort: errors are swallowed."""
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
    """New full run due? 'force' (re-run button) or piggyback_interval_hours
    minus grace period has passed since the last full run. Without a cache
    always due (first run)."""
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
# Piggyback data
# ---------------------------------------------------------------------------


def _piggyback_section(
    result: Mapping[str, Any],
    *,
    last_full_run_timestamp: float,
    last_piggyback_refresh_timestamp: float,
) -> bytes:
    """Section "checkmk_monitoring_coverage" (sep(0), one JSON line) of a
    host. Contains both timestamps so that the check plugin can read them
    directly from the section."""
    payload = {
        "host_name": result.get("host_name"),
        "status": result.get("status"),
        "coverage_pct": result.get("coverage_pct"),
        "fraction_text": result.get("fraction_text"),
        "findings": result.get("findings"),
        "detail_lines": list(result.get("detail_lines") or []),
        "capability_summary": result.get("capability_summary"),
        # Unfiltered items + sources - the check plugin evaluates them with the
        # host's Setup rule (status/findings above = state without rule).
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
    """Writes the piggyback data of all hosts via the official API
    cmk.piggyback.backend.store_piggyback_raw_data(). Other piggyback
    sources of the same hosts remain untouched (own source_hostname).
    Returns: (number of hosts written, last error or None)."""
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
        except Exception as exc:  # pragma: no cover - defensive
            last_error = f"{host_name}: {exc!r}"
    return written, last_error


def remove_stale_piggyback(results: Sequence[Mapping[str, Any]]) -> list[str]:
    """Removes this source's piggyback file for hosts that are no longer
    analyzed - otherwise an outdated result would remain there.
    The piggyback API only cleans up by age; only
    <piggyback>/<host>/monitoring_coverage_analyzer is affected."""
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
    """Refresh tick: rewrites the last stored result (no new analysis)
    with a new message_timestamp. Only last_piggyback_refresh_timestamp
    changes, last_full_run_timestamp stays unchanged."""
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
    # Only update the refresh timestamp - under the cache lock and only if
    # no new run has stored a different result in the meantime.
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
# Background analysis run
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def job_lock() -> Iterator[bool]:
    """Exclusive, NON-blocking lock for an analysis run. Yields False if a
    run is already active. The lock ends automatically with the process -
    even if it is aborted."""
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
    """Starts an analysis run as a separate process outside of Apache
    (cron script, mode "rerun"). Returns: (started, message)."""
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
    # Set the status BEFORE starting (the process overwrites it with
    # "running"), so that the page shows "running" immediately, even if the
    # process has not acquired the lock yet.
    save_job_state(state="starting", requested=time.time(), error=None)
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "ab") as log:
            subprocess.Popen(  # noqa: S603 - fixed path, no user input
                [python, script, "rerun"],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=root,
                start_new_session=True,  # survives the Apache request
                close_fds=True,
            )
    except OSError as exc:
        save_job_state(state="failed", finished=time.time(), error=f"start failed: {exc!r}")
        return False, f"{exc!r}"
    return True, "started"


# A "starting" status without a running process is considered orphaned
# after this time (process could not start).
_STARTING_TIMEOUT_SECONDS = 120


def job_status() -> dict[str, Any]:
    """Status for the page: state (idle/running/done/failed), started,
    finished, error. "running" only if the lock is held or the start has
    just been requested."""
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
        # Status says "running", but no process holds the lock anymore ->
        # process was aborted (e.g. kill, restart).
        state["state"] = "failed"
        state["error"] = state.get("error") or "analysis process ended unexpectedly (see var/log/monitoring_coverage_analyzer.log)"
    state.setdefault("state", "idle")
    return state
