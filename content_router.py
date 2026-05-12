"""
Content pipeline router — content_router.py

Mounted into main.py under /content.
Call register_jobs(scheduler) from main's lifespan to add the 22:30 cron job.
"""

import asyncio
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

router   = APIRouter(prefix="/content", tags=["content"])
security = HTTPBasic()

_last_run: dict = {}


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


def _generate_metadata_sync(script_path: str) -> dict:
    import os
    import json
    from pathlib import Path
    from generate_metadata import generate_lottery_metadata

    account_id = os.environ["CLOUDFLARE_ACCOUNT_ID"]
    api_token  = os.environ["CLOUDFLARE_API_TOKEN"]
    script_content = Path(script_path).read_text(encoding="utf-8")
    metadata = generate_lottery_metadata(script_content, account_id, api_token)

    out = Path(__file__).parent / "metadata.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(metadata, fh, indent=2, ensure_ascii=False)
    print(f"[PIPELINE] Metadata saved → {out.name}  title: {metadata.get('title','')[:60]}")
    return metadata


def _facebook_upload_sync(video_path: str, description: str) -> dict:
    from facebook import upload_reel_to_facebook
    return upload_reel_to_facebook(video_path=video_path, description=description)


def _youtube_upload_sync(video_path: str, metadata: dict, privacy: str) -> dict:
    from upload_youtube import get_authenticated_service, initialize_upload
    youtube = get_authenticated_service()
    return initialize_upload(youtube, video_path, metadata, privacy_status=privacy)


async def _run_predict(tts: bool = True, voice: str = "Aoede", day: int | None = None) -> dict:
    return await asyncio.to_thread(_predict_sync, tts, voice, day)


async def _run_video(
    script_path: str | None = None,
    audio_path: str | None = None,
    no_upload: bool = False,
    privacy: str = "public",
) -> None:
    await asyncio.to_thread(_video_sync, script_path, audio_path, no_upload, privacy)


async def _generate_metadata(script_path: str) -> dict:
    return await asyncio.to_thread(_generate_metadata_sync, script_path)


async def _run_facebook_upload(video_path: str, description: str) -> dict:
    return await asyncio.to_thread(_facebook_upload_sync, video_path, description)


async def _run_youtube_upload(video_path: str, metadata: dict, privacy: str) -> dict:
    return await asyncio.to_thread(_youtube_upload_sync, video_path, metadata, privacy)


def _cleanup_pipeline_files(predict: dict, output_dir: str = "lottery_output") -> None:
    removed = []
    txt = predict.get("txt")
    mp3 = predict.get("mp3")

    for path in [txt, mp3]:
        if path and os.path.exists(path):
            os.remove(path)
            removed.append(path)

    if txt:
        info = os.path.join(os.path.dirname(txt), "audio_info.json")
        if os.path.exists(info):
            os.remove(info)
            removed.append(info)

    if os.path.isdir(output_dir):
        shutil.rmtree(output_dir)
        removed.append(output_dir)

    print(f"[CLEANUP] Removed {len(removed)} item(s): {[os.path.basename(p) for p in removed]}")


_VIDEO_PATH = "lottery_output/lottery_video.mp4"


async def _full_pipeline() -> dict:
    now = datetime.now().isoformat()
    try:
        # 1. Generate TTS script + audio
        predict = await _run_predict(tts=True)

        # 2. Generate metadata from the TTS script and save metadata.json
        print("[PIPELINE] Generating metadata…")
        metadata = await _generate_metadata(predict["txt"])

        # 3. Generate video only (upload handled separately below)
        await _run_video(
            script_path=predict.get("txt"),
            audio_path=predict.get("mp3"),
            no_upload=True,
        )

        description = metadata.get("description", "")

        # 4. Upload to Facebook Reels FIRST (best-effort — failure won't block YouTube)
        fb_error: str | None = None
        try:
            print("[PIPELINE] Uploading to Facebook Reels…")
            await _run_facebook_upload(_VIDEO_PATH, description)
        except Exception as fb_exc:
            fb_error = str(fb_exc)
            print(f"[PIPELINE] Facebook upload failed (continuing to YouTube): {fb_exc}")

        # 5. Upload to YouTube
        print("[PIPELINE] Uploading to YouTube…")
        await _run_youtube_upload(_VIDEO_PATH, metadata, privacy="public")

        await asyncio.to_thread(_cleanup_pipeline_files, predict)
        result: dict = {
            "status":   "ok",
            "predict":  predict,
            "ran_at":   now,
            "facebook": "error" if fb_error else "ok",
            **({"facebook_error": fb_error} if fb_error else {}),
        }
    except Exception as exc:
        result = {"status": "error", "error": str(exc), "ran_at": now}
        print(f"[PIPELINE] error: {exc}")
    _last_run.clear()
    _last_run.update(result)
    return result


# ── Scheduler registration ─────────────────────────────────────────────────────

async def _scheduled_job() -> None:
    print(f"[SCHEDULER] content pipeline starting {datetime.now().isoformat()}")
    result = await _full_pipeline()
    print(f"[SCHEDULER] content pipeline done: status={result['status']}")


def register_jobs(scheduler) -> None:
    """Add content pipeline cron job to an existing APScheduler instance."""
    scheduler.add_job(
        _scheduled_job,
        CronTrigger(hour=18, minute=0, day_of_week="mon-fri", timezone="Asia/Bangkok"),
        id="content_pipeline",
        replace_existing=True,
    )


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/health")
def content_health(_: None = Depends(require_auth)):
    return {"status": "ok", "last_run": _last_run or None}


@router.post("/pipeline/run")
async def trigger_full(_: None = Depends(require_auth)):
    """Full pipeline: predict → TTS → video → YouTube upload."""
    result = await _full_pipeline()
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
