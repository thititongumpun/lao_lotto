"""
Facebook Reels upload helper — facebook.py

Implements the 5-step Facebook Graph API video upload flow:
  1. create_container()   — POST upload_phase=start → video_id + upload_url
  2. upload_chunk()       — POST raw binary to upload_url
  3. get_upload_status()  — GET video status fields
  4. wait_for_upload()    — polls until video_status == "upload_complete"
  5. publish_reel()       — POST upload_phase=finish → publishes the reel

Environment variables required:
    FACEBOOK_ACCESS_TOKEN  — Page / User access token with pages_read_engagement
                             and pages_manage_posts permissions

Usage:
    from facebook import upload_reel_to_facebook
    upload_reel_to_facebook(
        video_path="lottery_output/lottery_video.mp4",
        description="วิเคราะห์หวยลาว...",
    )
"""

import os
import time

import requests

# ── Constants ──────────────────────────────────────────────────────────────────

PAGE_ID            = "598514650638901"
GRAPH_BASE         = "https://graph.facebook.com"
VERSION_UPLOAD     = "v24.0"   # used for container creation
VERSION_MANAGE     = "v23.0"   # used for status check & publish


# ── Auth helper ────────────────────────────────────────────────────────────────

def _get_access_token() -> str:
    """Read FACEBOOK_ACCESS_TOKEN from environment."""
    token = os.getenv("FACEBOOK_ACCESS_TOKEN")
    if not token:
        raise RuntimeError(
            "FACEBOOK_ACCESS_TOKEN environment variable is not set. "
            "Add it to your .env file."
        )
    return token


# ── Step 1: Create upload container ───────────────────────────────────────────

def create_container(access_token: str) -> dict:
    """
    POST https://graph.facebook.com/v24.0/{page_id}/video_reels
         ?upload_phase=start&access_token=...

    Returns dict with 'video_id' and 'upload_url'.
    """
    url = f"{GRAPH_BASE}/{VERSION_UPLOAD}/{PAGE_ID}/video_reels"
    params = {
        "upload_phase":  "start",
        "access_token":  access_token,
    }

    resp = requests.post(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if "video_id" not in data or "upload_url" not in data:
        raise RuntimeError(f"[FACEBOOK] Unexpected container response: {data}")

    print(f"[FACEBOOK] Container created — video_id={data['video_id']}")
    return data   # {"video_id": "...", "upload_url": "..."}


# ── Step 2: Upload video binary ────────────────────────────────────────────────

def upload_chunk(upload_url: str, video_path: str, access_token: str) -> None:
    """
    POST {upload_url}
    Headers: Authorization, offset, file_size
    Body:    raw video binary

    Raises RuntimeError if the API does not return success=true.
    """
    file_size = os.path.getsize(video_path)
    headers = {
        "Authorization": f"OAuth {access_token}",
        "offset":        "0",
        "file_size":     str(file_size),
    }

    print(f"[FACEBOOK] Uploading {os.path.basename(video_path)} ({file_size:,} bytes)…")
    with open(video_path, "rb") as fh:
        resp = requests.post(upload_url, headers=headers, data=fh, timeout=600)

    resp.raise_for_status()
    data = resp.json()

    if not data.get("success"):
        raise RuntimeError(f"[FACEBOOK] Chunk upload failed: {data}")

    print("[FACEBOOK] Chunk upload accepted.")


# ── Step 3: Query upload status ────────────────────────────────────────────────

def get_upload_status(video_id: str, access_token: str) -> dict:
    """
    GET https://graph.facebook.com/v23.0/{video_id}?fields=status&access_token=...

    Returns the full response dict, e.g.:
        {"id": "...", "status": {"video_status": "upload_complete", ...}}
    """
    url = f"{GRAPH_BASE}/{VERSION_MANAGE}/{video_id}"
    params = {
        "fields":       "status",
        "access_token": access_token,
    }

    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ── Step 4: Poll until upload_complete ────────────────────────────────────────

def wait_for_upload(
    video_id: str,
    access_token: str,
    timeout: int = 300,
    interval: int = 5,
) -> None:
    """
    Poll get_upload_status() every `interval` seconds until
    status.video_status == 'upload_complete' or timeout is reached.
    """
    print(f"[FACEBOOK] Waiting for server processing (video_id={video_id})…")
    elapsed = 0

    while elapsed < timeout:
        data         = get_upload_status(video_id, access_token)
        status_obj   = data.get("status", {})
        video_status = status_obj.get("video_status", "unknown")

        print(f"[FACEBOOK]   video_status={video_status}  ({elapsed}s elapsed)")

        if video_status == "upload_complete":
            print("[FACEBOOK] Processing complete — ready to publish.")
            return

        if video_status in ("error", "processing_failed"):
            raise RuntimeError(
                f"[FACEBOOK] Server processing failed: {data}"
            )

        time.sleep(interval)
        elapsed += interval

    raise TimeoutError(
        f"[FACEBOOK] Upload did not complete within {timeout}s "
        f"(video_id={video_id})"
    )


# ── Step 5: Publish reel ───────────────────────────────────────────────────────

def publish_reel(video_id: str, description: str, access_token: str) -> dict:
    """
    POST https://graph.facebook.com/v23.0/{page_id}/video_reels
         ?video_id=...&upload_phase=finish&video_state=PUBLISHED
           &description=...&access_token=...

    Returns the API response dict.
    """
    url = f"{GRAPH_BASE}/{VERSION_MANAGE}/{PAGE_ID}/video_reels"
    params = {
        "video_id":     video_id,
        "upload_phase": "finish",
        "video_state":  "PUBLISHED",
        "description":  description,
        "access_token": access_token,
    }

    resp = requests.post(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    print(f"[FACEBOOK] Reel published — {data}")
    return data


# ── Public entry point ─────────────────────────────────────────────────────────

def upload_reel_to_facebook(video_path: str, description: str) -> dict:
    """
    Full 5-step Facebook Reels upload flow.

    Args:
        video_path:  Local path to the .mp4 file.
        description: Caption / description text for the reel
                     (typically from generate_metadata.py → metadata['description']).

    Returns:
        Publish API response dict.
    """
    access_token = _get_access_token()

    # 1 — Create upload container
    container  = create_container(access_token)
    video_id   = container["video_id"]
    upload_url = container["upload_url"]

    # 2 — Upload binary
    upload_chunk(upload_url, video_path, access_token)

    # 3 & 4 — Wait until server finishes processing
    wait_for_upload(video_id, access_token)

    # 5 — Publish
    return publish_reel(video_id, description, access_token)


# ── Text-only page post ────────────────────────────────────────────────────────

def post_text_to_facebook(message: str) -> dict:
    """
    POST https://graph.facebook.com/v23.0/{page_id}/feed
         message=...&access_token=...

    Plain text post on the page (no media). Returns {"id": "<page>_<post>"}.
    """
    url = f"{GRAPH_BASE}/{VERSION_MANAGE}/{PAGE_ID}/feed"
    resp = requests.post(
        url,
        data={"message": message, "access_token": _get_access_token()},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    print(f"[FACEBOOK] Text post published — {data}")
    return data
