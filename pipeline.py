"""
Content pipeline runner — pipeline.py

Runs the full predict → metadata → video → Facebook → YouTube flow as one
synchronous pass. Designed to be launched as a SHORT-LIVED subprocess
(`python -m pipeline`): all heavy imports (google.genai/grpc, googleapiclient,
requests) and peak buffers (PCM audio, generated images) live in this child
process, which exits and returns every byte to the OS — so the long-lived
FastAPI server in main.py never accumulates that footprint.

content_router._full_pipeline spawns this and reads back the result as a single
JSON line prefixed with RESULT_SENTINEL on stdout. All other prints are logs.
"""

import json
import os
import shutil
from datetime import datetime
from pathlib import Path

RESULT_SENTINEL = "__PIPELINE_RESULT__"
_VIDEO_PATH = "lottery_output/lottery_video.mp4"


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


def run_pipeline() -> dict:
    now = datetime.now().isoformat()
    try:
        # 1. Generate TTS script + audio
        from gen_predict import run_predict
        predict = run_predict(tts=True, voice="Aoede", day=None)

        # 2. Generate metadata from the TTS script and save metadata.json
        print("[PIPELINE] Generating metadata…")
        from generate_metadata import generate_lottery_metadata
        account_id = os.environ["CLOUDFLARE_ACCOUNT_ID"]
        api_token = os.environ["CLOUDFLARE_API_TOKEN"]
        script_content = Path(predict["txt"]).read_text(encoding="utf-8")
        metadata = generate_lottery_metadata(script_content, account_id, api_token)
        out = Path(__file__).parent / "metadata.json"
        out.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[PIPELINE] Metadata saved → {out.name}  title: {metadata.get('title','')[:60]}")

        # 3. Generate video only (upload handled separately below)
        from gen_image import lottery_pipeline
        lottery_pipeline(
            script_path=predict.get("txt"),
            audio_path=predict.get("mp3"),
            output_dir="lottery_output",
            video_path=_VIDEO_PATH,
            privacy_status="public",
            upload=False,
        )

        description = metadata.get("description", "")

        # 4. Upload to Facebook Reels FIRST (best-effort — failure won't block YouTube)
        fb_error = None
        try:
            print("[PIPELINE] Uploading to Facebook Reels…")
            from facebook import upload_reel_to_facebook
            upload_reel_to_facebook(video_path=_VIDEO_PATH, description=description)
        except Exception as fb_exc:
            fb_error = str(fb_exc)
            print(f"[PIPELINE] Facebook upload failed (continuing to YouTube): {fb_exc}")

        # 5. Upload to YouTube
        print("[PIPELINE] Uploading to YouTube…")
        from upload_youtube import get_authenticated_service, initialize_upload
        youtube = get_authenticated_service()
        initialize_upload(youtube, _VIDEO_PATH, metadata, privacy_status="public")

        _cleanup_pipeline_files(predict)
        result = {
            "status": "ok",
            "predict": predict,
            "ran_at": now,
            "facebook": "error" if fb_error else "ok",
            **({"facebook_error": fb_error} if fb_error else {}),
        }
    except Exception as exc:
        result = {"status": "error", "error": str(exc), "ran_at": now}
        print(f"[PIPELINE] error: {exc}")
    return result


if __name__ == "__main__":
    res = run_pipeline()
    print(RESULT_SENTINEL + json.dumps(res, ensure_ascii=False))
