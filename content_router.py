"""
Content pipeline router — content_router.py

Mounted into main.py under /content.
Call register_jobs(scheduler) from main's lifespan to add the 22:30 content
and 00:30 horoscope cron jobs.
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from pipeline import RESULT_SENTINEL

router   = APIRouter(prefix="/content", tags=["content"])
security = HTTPBasic()

_last_run: dict = {}
_last_horoscope: dict = {}


# ── Auth (mirrors main.py — keep in sync or extract to deps.py) ───────────────

def require_auth(credentials: HTTPBasicCredentials = Depends(security)):
    if credentials.username != "admin" or credentials.password != "admin":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


# ── Pipeline helpers ───────────────────────────────────────────────────────────

def _predict_sync(tts: bool, voice: str, day: int | None) -> dict:
    from gen_predict import run_predict
    return run_predict(tts=tts, voice=voice, day=day)


def _video_sync(script_path: str | None, audio_path: str | None, no_upload: bool, privacy: str) -> None:
    from gen_image import lottery_pipeline
    lottery_pipeline(
        script_path=script_path,
        audio_path=audio_path,
        output_dir="lottery_output",
        video_path="lottery_output/lottery_video.mp4",
        privacy_status=privacy,
        upload=not no_upload,
    )


async def _run_predict(tts: bool = True, voice: str = "Aoede", day: int | None = None) -> dict:
    return await asyncio.to_thread(_predict_sync, tts, voice, day)


async def _run_video(
    script_path: str | None = None,
    audio_path: str | None = None,
    no_upload: bool = False,
    privacy: str = "public",
) -> None:
    await asyncio.to_thread(_video_sync, script_path, audio_path, no_upload, privacy)


def _extract_result(line: str) -> dict | None:
    """Parse the JSON result line emitted by pipeline.py, or None for log lines."""
    if line.startswith(RESULT_SENTINEL):
        try:
            return json.loads(line[len(RESULT_SENTINEL):])
        except json.JSONDecodeError:
            return None
    return None


async def _run_subprocess(module: str, *args: str) -> dict:
    """Run `python -m <module> [args]` as a short-lived subprocess so its heavy
    imports and peak buffers never accumulate in the long-lived server process.
    Returns the last RESULT_SENTINEL payload, or an error dict if none arrived."""
    now = datetime.now().isoformat()
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", module, *args,
        cwd=str(Path(__file__).parent),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    result: dict | None = None
    assert proc.stdout is not None
    async for raw in proc.stdout:
        line = raw.decode("utf-8", "replace").rstrip("\n")
        parsed = _extract_result(line)
        if parsed is not None:
            result = parsed
        else:
            print(line)  # stream child logs to the server's stdout
    await proc.wait()

    if result is None:
        result = {
            "status": "error",
            "error": f"{module} subprocess produced no result (exit {proc.returncode})",
            "ran_at": now,
        }
    return result


async def _full_pipeline() -> dict:
    result = await _run_subprocess("pipeline")
    _last_run.clear()
    _last_run.update(result)
    return result


async def _horoscope_pipeline(date: str | None = None, dry_run: bool = False) -> dict:
    args = (["--date", date] if date else []) + (["--dry-run"] if dry_run else [])
    result = await _run_subprocess("horoscope", *args)
    _last_horoscope.clear()
    _last_horoscope.update(result)
    return result


# ── Scheduler registration ─────────────────────────────────────────────────────

async def _scheduled_job() -> None:
    print(f"[SCHEDULER] content pipeline starting {datetime.now().isoformat()}")
    result = await _full_pipeline()
    print(f"[SCHEDULER] content pipeline done: status={result['status']}")


async def _scheduled_horoscope() -> None:
    print(f"[SCHEDULER] horoscope post starting {datetime.now().isoformat()}")
    result = await _horoscope_pipeline()
    print(f"[SCHEDULER] horoscope post done: status={result['status']}")


def register_jobs(scheduler) -> None:
    """Add content pipeline + horoscope cron jobs to an existing APScheduler instance."""
    scheduler.add_job(
        _scheduled_job,
        CronTrigger(hour=22, minute=30, day_of_week="mon-fri", timezone="Asia/Bangkok"),
        id="content_pipeline",
        replace_existing=True,
    )
    scheduler.add_job(
        _scheduled_horoscope,
        CronTrigger(hour=0, minute=30, timezone="Asia/Bangkok"),
        id="horoscope_post",
        replace_existing=True,
    )


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/health")
def content_health(_: None = Depends(require_auth)):
    return {"status": "ok", "last_run": _last_run or None, "last_horoscope": _last_horoscope or None}


@router.post("/pipeline/run")
async def trigger_full(_: None = Depends(require_auth)):
    """Full pipeline: predict → TTS → video → YouTube upload."""
    result = await _full_pipeline()
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result)
    return result


@router.post("/horoscope/run")
async def trigger_horoscope(
    date: str | None = None,
    dry_run: bool = False,
    _: None = Depends(require_auth),
):
    """Fetch today's 7 Sanook horoscopes, rewrite with Gemini, post text to Facebook."""
    result = await _horoscope_pipeline(date=date, dry_run=dry_run)
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result)
    return result


@router.post("/pipeline/predict")
async def trigger_predict(
    tts: bool = True,
    voice: str = "Aoede",
    day: int | None = None,
    _: None = Depends(require_auth),
):
    """Generate TTS script and optionally MP3 audio."""
    try:
        return await _run_predict(tts=tts, voice=voice, day=day)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/pipeline/video")
async def trigger_video(
    no_upload: bool = False,
    privacy: str = "public",
    _: None = Depends(require_auth),
):
    """Generate video from latest TTS files and upload to YouTube."""
    try:
        await _run_video(no_upload=no_upload, privacy=privacy)
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
