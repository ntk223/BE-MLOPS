"""UI-triggered evaluation runs (POST /evaluations/run).

Spawns ``learnguard evaluate all`` in the ml-data repo from a daemon
thread and keeps a single in-memory run state for
``GET /evaluations/run-status``. One run at a time: a second start while
``running`` raises :class:`EvaluationAlreadyRunning`.

No secrets are ever stored or logged here: the subprocess inherits the
backend environment (LLM_API_KEY included) implicitly and only the last
500 characters of stderr — on failure — are kept.
"""

import os
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# evaluate all runs SHAP + the Gemini judge; 10 minutes is a safe ceiling.
SUBPROCESS_TIMEOUT_SECONDS = 600
ERROR_TAIL_CHARS = 500

Runner = Callable[[str], int]


def _idle_state() -> dict[str, Any]:
    return {
        "status": "idle",
        "run_id": None,
        "started_at": None,
        "finished_at": None,
        "error": None,
        "summary": None,
    }


# Module-level singleton state (one run at a time, process-wide).
_STATE_LOCK = threading.Lock()
_STDERR_TAIL_LOCK = threading.Lock()
_STDERR_TAIL = ""
_STATE: dict[str, Any] = _idle_state()


class EvaluationAlreadyRunning(Exception):
    """Raised by :func:`start_run` when a run is already in flight."""


class RunnerNotConfigured(Exception):
    """Raised by :func:`start_run` when required settings are empty."""


def get_state() -> dict[str, Any]:
    """Snapshot of the current run state (a plain copy, safe to mutate)."""
    with _STATE_LOCK:
        return dict(_STATE)


def reset_state() -> None:
    """Clear module state (test fixture helper — never call from routes)."""
    global _STDERR_TAIL
    with _STATE_LOCK:
        _STATE.clear()
        _STATE.update(_idle_state())
    with _STDERR_TAIL_LOCK:
        _STDERR_TAIL = ""


def start_run(config: dict[str, Any], runner: Runner | None = None) -> str:
    """Start one evaluation run; returns its ``run_id``.

    ``config`` carries the resolved settings (ml_repo_dir, learnguard_bin,
    model_dir, preprocessing_dir, pg_dsn, judge_sample, evaluations_dir).
    ``runner`` is injectable for tests; the default implementation runs the
    ml-data CLI via subprocess and returns its exit code.

    Raises :class:`EvaluationAlreadyRunning` (busy) or
    :class:`RunnerNotConfigured` (empty required settings).
    """
    with _STATE_LOCK:
        if _STATE["status"] == "running":
            raise EvaluationAlreadyRunning(str(_STATE["run_id"]))
        if not str(config.get("ml_repo_dir") or "").strip():
            raise RunnerNotConfigured(
                "ml_runner_not_configured (set ML_REPO_DIR)"
            )
        if not str(config.get("evaluations_dir") or "").strip():
            raise RunnerNotConfigured(
                "evaluations_dir_not_configured (set EVALUATIONS_DIR)"
            )
        run_id = datetime.now(timezone.utc).strftime("eval-ui-%Y%m%d-%H%M%S")
        _STATE.clear()
        _STATE.update(
            {
                "status": "running",
                "run_id": run_id,
                "started_at": _utc_now_iso(),
                "finished_at": None,
                "error": None,
                "summary": None,
            }
        )

    effective_runner: Runner = (
        runner if runner is not None else lambda rid: _subprocess_runner(
            config, rid
        )
    )
    thread = threading.Thread(
        target=_execute, args=(config, run_id, effective_runner), daemon=True
    )
    thread.start()
    return run_id


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _subprocess_runner(config: dict[str, Any], run_id: str) -> int:
    """Run the ml-data CLI; records the stderr tail for error reporting."""
    global _STDERR_TAIL
    ml_repo_dir = str(config["ml_repo_dir"])
    cmd = [
        str(config.get("learnguard_bin") or ".venv/bin/learnguard"),
        "evaluate",
        "all",
    ]
    if str(config.get("model_dir") or "").strip():
        cmd += ["--model-artifact-dir", str(config["model_dir"])]
    if str(config.get("preprocessing_dir") or "").strip():
        cmd += ["--preprocessing-artifact-dir", str(config["preprocessing_dir"])]
    cmd += [
        "--pg-dsn",
        str(config.get("pg_dsn") or ""),
        "--judge-sample",
        str(config.get("judge_sample", 4)),
        "--run-id",
        run_id,
    ]
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
            cmd,
            cwd=ml_repo_dir,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired:
        with _STDERR_TAIL_LOCK:
            _STDERR_TAIL = (
                f"subprocess timeout after {SUBPROCESS_TIMEOUT_SECONDS}s"
            )
        return 124
    stderr = proc.stderr or ""
    with _STDERR_TAIL_LOCK:
        _STDERR_TAIL = stderr[-ERROR_TAIL_CHARS:]
    return proc.returncode


def _error_tail() -> str:
    with _STDERR_TAIL_LOCK:
        return _STDERR_TAIL


def _execute(config: dict[str, Any], run_id: str, runner: Runner) -> None:
    """Thread body: run the runner and publish done/error state."""
    try:
        returncode = runner(run_id)
        error = None if returncode == 0 else (
            _error_tail()
            or f"learnguard evaluate all exited with code {returncode}"
        )
        summary = (
            _read_run_summary(config, run_id) if returncode == 0 else None
        )
        status = "done" if returncode == 0 else "error"
    except Exception as exc:  # noqa: BLE001 — the thread must never die silently
        status, error, summary = "error", str(exc)[-ERROR_TAIL_CHARS:], None
    with _STATE_LOCK:
        if _STATE["run_id"] != run_id or _STATE["status"] != "running":
            return  # a newer run owns the state — do not overwrite
        _STATE["status"] = status
        _STATE["finished_at"] = _utc_now_iso()
        _STATE["error"] = error
        _STATE["summary"] = summary


def _read_run_summary(config: dict[str, Any], run_id: str) -> dict[str, int] | None:
    """Combined summary of the published scorecards, or ``None``.

    Checks the backend EVALUATIONS_DIR first (what GET /evaluations serves),
    then the ml-data default ``artifacts/evaluations`` inside the repo.
    Helpers are imported lazily from app.py to avoid a circular import.
    """
    from learnguard_backend.api.app import (  # noqa: PLC0415 — lazy, see above
        _combined_summary,
        _load_manifest,
        _load_scorecard,
    )

    candidates = [str(config.get("evaluations_dir") or "")]
    ml_repo_dir = str(config.get("ml_repo_dir") or "").strip()
    if ml_repo_dir:
        candidates.append(
            str(Path(ml_repo_dir) / "artifacts" / "evaluations")
        )
    for base in candidates:
        if not base:
            continue
        run_dir = Path(base) / run_id
        if _load_manifest(run_dir) is None:
            continue
        scorecards = []
        for path in sorted(run_dir.glob("eval_*.json")):
            card = _load_scorecard(path)
            if card is not None:
                scorecards.append(card)
        return _combined_summary(scorecards) if scorecards else None
    return None
