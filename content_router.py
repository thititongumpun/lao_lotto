"""
Content pipeline router — content_router.py

Mounted into main.py under /content.
Call register_jobs(scheduler) from main's lifespan to add the 22:30 content,
00:30 horoscope, news (hot / digest / lotto) and traffic (TomTom, every 15 min) cron jobs.
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from pipeline import RESULT_SENTINEL

router   = APIRouter(prefix="/content", tags=["content"])
security = HTTPBasic()

_last_run: dict = {}
_last_horoscope: dict = {}
_last_news: dict[str, dict] = {}


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


async def _news_pipeline(
    kind: str,
    dry_run: bool = False,
    hours: int | None = None,
    label: str | None = None,
) -> dict:
    args = ["--kind", kind]
    if dry_run:
        args.append("--dry-run")
    if hours is not None:
        args += ["--hours", str(hours)]
    if label:
        args += ["--label", label]
    result = await _run_subprocess("news_post", *args)
    _last_news[kind] = result
    return result


async def _traffic_pipeline(dry_run: bool = False) -> dict:
    result = await _run_subprocess("traffic", *(["--dry-run"] if dry_run else []))
    _last_news["traffic"] = result
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


async def _scheduled_news(kind: str, hours: int | None = None, label: str | None = None) -> None:
    tag = f"news {kind}" + (f" ({label})" if label else "")
    print(f"[SCHEDULER] {tag} starting {datetime.now().isoformat()}")
    for attempt in range(1, NEWS_ATTEMPTS + 1):
        result = await _news_pipeline(kind, hours=hours, label=label)
        if not _is_network_error(result) or attempt == NEWS_ATTEMPTS:
            break
        print(f"[SCHEDULER] {tag} network error, retry {attempt + 1}/{NEWS_ATTEMPTS} in {NEWS_RETRY_S}s")
        await asyncio.sleep(NEWS_RETRY_S)
    print(f"[SCHEDULER] {tag} done: status={result['status']}")


# homelab DNS drops for a few minutes now and then (2026-09-30 07:00: pm25 + digest lost)
NEWS_ATTEMPTS, NEWS_RETRY_S = 3, 300
TRAFFIC_INTERVAL_MIN = int(os.getenv("TRAFFIC_INTERVAL_MIN", "15"))
TRAFFIC_HOURS = os.getenv("TRAFFIC_HOURS", "6-21")  # last run 21:45
# connect-stage failures only: a read timeout may come after Facebook already took the post
NETWORK_ERRORS = ("NameResolutionError", "Failed to resolve", "Max retries exceeded")


def _is_network_error(result: dict) -> bool:
    """Failed before anything was posted because a site was unreachable: safe to run again."""
    return result.get("status") == "error" and any(m in str(result.get("error", "")) for m in NETWORK_ERRORS)


async def _scheduled_news_hot() -> None:
    await _scheduled_news("hot")


async def _scheduled_news_digest_morning() -> None:
    await _scheduled_news("digest", hours=12, label="สรุปข่าวเช้า")


async def _scheduled_news_digest_evening() -> None:
    await _scheduled_news("digest", hours=12, label="สรุปข่าวค่ำ")


async def _scheduled_news_lotto() -> None:
    await _scheduled_news("lotto")


async def _scheduled_news_pm25() -> None:
    await _scheduled_news("pm25")


async def _scheduled_news_gold() -> None:
    await _scheduled_news("gold")


async def _scheduled_traffic() -> None:
    # no retry loop: the next tick is the retry
    print(f"[SCHEDULER] traffic starting {datetime.now().isoformat()}")
    result = await _traffic_pipeline()
    print(f"[SCHEDULER] traffic done: status={result['status']}")


async def _scheduled_stats() -> None:
    result = await _run_subprocess("stats")
    print(f"[SCHEDULER] fb stats done: {result}")


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
    scheduler.add_job(  # read-only on Facebook, so not behind NEWS_POSTS_ENABLED
        _scheduled_stats,
        CronTrigger(hour=3, minute=10, timezone="Asia/Bangkok"),
        id="fb_stats",
        replace_existing=True,
    )
    news_jobs = [
        ("news_hot",       _scheduled_news_hot,            {"hour": "8-22/2", "minute": 0}),
        # 09:30 moved to 22:30: photo/text posts at 07-09 avg ~230 views, 19-22 ~420 (/content/stats, Sep 2026)
        ("news_hot_b",     _scheduled_news_hot,            {"hour": "11-21/2,22", "minute": 30}),
        ("news_digest_am", _scheduled_news_digest_morning, {"hour": 7, "minute": 0}),
        ("news_digest_pm", _scheduled_news_digest_evening, {"hour": 19, "minute": 0}),
        ("news_lotto",     _scheduled_news_lotto,          {"hour": 22, "minute": 15, "day_of_week": "mon,wed,fri"}),
        ("news_pm25",      _scheduled_news_pm25,           {"hour": 7, "minute": 5}),
        ("news_gold_am",   _scheduled_news_gold,           {"hour": 9, "minute": 30}),
        ("news_gold_pm",   _scheduled_news_gold,           {"hour": 15, "minute": 0}),
        ("traffic",        _scheduled_traffic,             {"minute": f"*/{TRAFFIC_INTERVAL_MIN}", "hour": TRAFFIC_HOURS}),
    ]
    # Deploy-safe: the scheduler posts to the real Page, so the news jobs stay
    # off until NEWS_POSTS_ENABLED=1 — dry-run the /content/news/* endpoints first.
    if os.getenv("NEWS_POSTS_ENABLED") != "1":
        print("[SCHEDULER] news jobs disabled (NEWS_POSTS_ENABLED != 1)")
        news_jobs = []
    for job_id, fn, cron in news_jobs:
        scheduler.add_job(
            fn,
            CronTrigger(timezone="Asia/Bangkok", **cron),
            id=job_id,
            replace_existing=True,
        )


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/health")
def content_health(_: None = Depends(require_auth)):
    return {
        "status": "ok",
        "last_run": _last_run or None,
        "last_horoscope": _last_horoscope or None,
        "last_news": _last_news or None,
    }


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


async def _news_endpoint(kind: str, dry_run: bool, hours: int | None = None, label: str | None = None) -> dict:
    result = await _news_pipeline(kind, dry_run=dry_run, hours=hours, label=label)
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result)
    return result


@router.post("/news/hot")
async def trigger_news_hot(
    dry_run: bool = False,
    hours: int | None = None,
    _: None = Depends(require_auth),
):
    """Post the hottest news story from the last N hours to Facebook."""
    return await _news_endpoint("hot", dry_run, hours=hours)


@router.post("/news/digest")
async def trigger_news_digest(
    dry_run: bool = False,
    label: str | None = None,
    hours: int | None = None,
    _: None = Depends(require_auth),
):
    """Post a news digest (สรุปข่าว) covering the last N hours."""
    return await _news_endpoint("digest", dry_run, hours=hours, label=label)


@router.post("/news/lotto")
async def trigger_news_lotto(
    dry_run: bool = False,
    _: None = Depends(require_auth),
):
    """Post lottery-related news after the evening scrape."""
    return await _news_endpoint("lotto", dry_run)


@router.post("/news/pm25")
async def trigger_news_pm25(
    dry_run: bool = False,
    _: None = Depends(require_auth),
):
    """Post this morning's PM2.5 for 4 provinces (Air4Thai) as a text post."""
    return await _news_endpoint("pm25", dry_run)


@router.post("/news/gold")
async def trigger_news_gold(
    dry_run: bool = False,
    _: None = Depends(require_auth),
):
    """Post today's gold price (สมาคมค้าทองคำ) as a text post."""
    return await _news_endpoint("gold", dry_run)


@router.post("/traffic/run")
async def trigger_traffic(dry_run: bool = False, _: None = Depends(require_auth)):
    """Poll TomTom for Bangkok traffic and post if the heavy/slow roads changed."""
    result = await _traffic_pipeline(dry_run)
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result)
    return result


@router.get("/traffic/usage")
async def traffic_usage(days: int = Query(7, ge=1, le=90), _: None = Depends(require_auth)):
    """Daily TomTom request counts."""
    from traffic import usage
    return await asyncio.to_thread(usage, days)


@router.get("/stats")
async def content_stats(days: int = 30, _: None = Depends(require_auth)):
    """Avg views/reactions/comments/shares per lane and per posting hour, plus daily Page earnings."""
    from stats import report
    return await asyncio.to_thread(report, days)


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
