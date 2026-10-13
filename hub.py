"""Warehouse dashboard events (1min-warehouse hub).

Fire-and-forget: a slow or missing hub never blocks or fails a job. No-op unless
HUB_URL is set; HUB_KEY must match the hub's.
"""

import json
import os
import queue
import threading
import time
import urllib.request
from datetime import datetime, timezone

_q: queue.Queue = queue.Queue(maxsize=256)
_started = False
_lock = threading.Lock()


def _sender(url: str, key: str) -> None:
    while True:
        body = _q.get()
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json", "X-Hub-Key": key},
        )
        try:
            urllib.request.urlopen(req, timeout=2).close()
        except Exception:
            pass  # dashboard only; never matters to the job


def emit(job: str, run: str, stage: str, status: str, f: dict | None = None) -> None:
    """status: start|ok|fail|skip|info."""
    global _started
    url = os.getenv("HUB_URL")
    if not url:
        return
    with _lock:
        if not _started:
            threading.Thread(target=_sender, args=(url, os.getenv("HUB_KEY", "")), daemon=True).start()
            _started = True
    body = json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(), "app": "laolotto",
        "job": job, "run": run, "stage": stage, "status": status, "f": f,
    }, ensure_ascii=False, default=str).encode()
    try:
        _q.put_nowait(body)
    except queue.Full:
        pass  # hub backed up: drop


def new_run() -> str:
    return format(int(time.time() * 1000), "x")


_STATUS = {"ok": "ok", "error": "fail", "skipped": "skip", "dry_run": "skip"}


def end(job: str, run: str, result: dict) -> None:
    """Emit the job-end event for a RESULT_SENTINEL / run_fetch_job result dict."""
    status = _STATUS.get(str(result.get("status")), "info")
    # scalars only, trimmed: results can carry long scripts and paths
    f = {k: (v[:200] if isinstance(v, str) else v) for k, v in result.items()
         if isinstance(v, (str, int, float, bool)) and k != "status"}
    if status == "fail":
        f["reason"] = str(result.get("error") or result.get("message") or "error")[:200]
    emit(job, run, "job", status, f)
