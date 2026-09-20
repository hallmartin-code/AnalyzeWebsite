"""File-backed store for analyses running in the background.

An analysis takes minutes. Delivering the .docx as the body of that same
request meant the document existed only for as long as the browser connection
did — and over four minutes it often did not survive. The e-mail went out from
a daemon thread and was unaffected, which is exactly how the fault presented:
the analysis arrived by e-mail, the download never happened.

So the work moved off the request. /analyze starts a job and returns at once;
the browser polls a cheap status endpoint and fetches the document when it is
ready. No request is ever held open long enough to be dropped.

State lives in files rather than a module-level dict because gunicorn runs
several worker *processes*. The worker that polls a job is routinely not the
one that started it, and a dict in one process is invisible to the others.
They do share a filesystem, so that is where the state has to go.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import tempfile
import time
from pathlib import Path

log = logging.getLogger("analyzewebsite.jobs")

# Both workers are in one container, so the system temp directory is shared.
# Override with JOB_DIR if the deployment ever gets a real volume.
JOB_DIR = Path(os.getenv("JOB_DIR") or Path(tempfile.gettempdir()) / "analyzewebsite_jobs")

# How long a finished job stays downloadable. Long enough to survive a slow
# download or a second click, short enough that an ephemeral container's disk
# does not fill with documents nobody came back for.
RETENTION_SECONDS = 2 * 60 * 60

# A job whose worker died mid-run leaves its state file saying "running"
# forever. Nothing will ever update it, so past this age it is reported as
# failed rather than left spinning in the browser.
STALE_SECONDS = 400

RUNNING = "running"
DONE = "done"
FAILED = "failed"


def _path(job_id: str, suffix: str) -> Path:
    return JOB_DIR / f"{job_id}{suffix}"


def _valid(job_id: str) -> bool:
    """Job ids are our own hex tokens; anything else is a path traversal try."""
    return bool(job_id) and len(job_id) <= 64 and all(c in "0123456789abcdef" for c in job_id)


def create(url: str, company_name: str) -> str:
    """Register a new job and return its id."""
    JOB_DIR.mkdir(parents=True, exist_ok=True)
    sweep()
    job_id = secrets.token_hex(16)
    _write(job_id, {
        "state": RUNNING,
        "url": url,
        "company_name": company_name,
        "filename": "",
        "message": "",
        "created": time.time(),
    })
    return job_id


def _write(job_id: str, payload: dict) -> None:
    """Replace the state file atomically.

    A poll landing between a truncate and a write would otherwise read half a
    file and fail to parse; os.replace swaps the name over in one step.
    """
    payload["updated"] = time.time()
    tmp = _path(job_id, f".{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, _path(job_id, ".json"))


def read(job_id: str) -> dict | None:
    """Current state, or None when there is no such job.

    A job left `running` by a dead worker is reported as failed; see
    STALE_SECONDS.
    """
    if not _valid(job_id):
        return None
    try:
        payload = json.loads(_path(job_id, ".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    if payload.get("state") == RUNNING:
        if time.time() - payload.get("updated", 0) > STALE_SECONDS:
            payload["state"] = FAILED
            payload["message"] = (
                "The analysis stopped unexpectedly before it finished. This is "
                "usually the server restarting mid-run. Please try again."
            )
    return payload


def finish(job_id: str, *, docx_bytes: bytes, filename: str) -> None:
    """Store the document and mark the job downloadable."""
    _path(job_id, ".docx").write_bytes(docx_bytes)
    payload = read(job_id) or {}
    payload.update(state=DONE, filename=filename, message="")
    _write(job_id, payload)


def fail(job_id: str, message: str) -> None:
    payload = read(job_id) or {}
    payload.update(state=FAILED, message=message)
    _write(job_id, payload)


def document(job_id: str) -> tuple[bytes, str] | None:
    """The finished document and its filename, if the job produced one."""
    payload = read(job_id)
    if not payload or payload.get("state") != DONE:
        return None
    try:
        return _path(job_id, ".docx").read_bytes(), payload.get("filename") or "analysis.docx"
    except OSError:
        return None


def sweep() -> None:
    """Delete jobs past RETENTION_SECONDS. Best effort — never fails a request."""
    cutoff = time.time() - RETENTION_SECONDS
    try:
        entries = list(JOB_DIR.iterdir())
    except OSError:
        return
    for entry in entries:
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink()
        except OSError:
            log.debug("could not sweep %s", entry, exc_info=True)
