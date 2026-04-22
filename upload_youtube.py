"""
YouTube upload helpers — upload_youtube.py

get_authenticated_service() — returns an authenticated YouTube service.
  First run opens a browser for OAuth consent; token is cached in youtube_token.pickle.

initialize_upload(youtube, video_path, metadata, privacy_status) — uploads the video.
"""

import pickle
from pathlib import Path

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES         = ["https://www.googleapis.com/auth/youtube.upload"]
BASE_DIR       = Path(__file__).parent
CLIENT_SECRETS = BASE_DIR / "client_secrets.json"
TOKEN_FILE     = BASE_DIR / "youtube_token.pickle"


def get_authenticated_service():
    creds = None
    if TOKEN_FILE.exists():
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow  = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS), SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)

    return build("youtube", "v3", credentials=creds)


def initialize_upload(
    youtube,
    video_path: str,
    metadata: dict,
    privacy_status: str = "public",
) -> dict:
    body = {
        "snippet": {
            "title":       metadata.get("title", "Lao Lottery Analysis"),
            "description": metadata.get("description", ""),
            "tags":        metadata.get("tags", []),
            "categoryId":  "22",  # People & Blogs
        },
        "status": {"privacyStatus": privacy_status},
    }

    media   = MediaFileUpload(video_path, chunksize=-1, resumable=True)
    request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"  Upload {int(status.progress() * 100)}%")

    return response
