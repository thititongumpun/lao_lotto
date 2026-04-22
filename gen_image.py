import argparse
import base64
import os
import re
import sys
import time
import subprocess
from io import BytesIO
from pathlib import Path

import ollama
import requests
from PIL import Image

from generate_metadata import generate_lottery_metadata
from upload_youtube import get_authenticated_service, initialize_upload

# ── Config ─────────────────────────────────────────────────────────────────────

OLLAMA_MODEL = "gemma3"
MAX_RETRIES  = 5

LOTTERY_STYLE = (
    "Thai lottery YouTube video background, vibrant flat illustration, "
    "glowing golden lottery balls, large bold numbers floating in air, colorful confetti, "
    "Thai traditional pattern border, festive celebration atmosphere, "
    "digital art, no people, no faces, no readable text"
)
LOTTERY_NEGATIVE = (
    "photorealistic, people, faces, body, hands, text, letters, words, watermark, "
    "dark, gloomy, violent, 3d render, realistic"
)

# ── Utilities ──────────────────────────────────────────────────────────────────

def with_retry(fn, *args, **kwargs):
    wait = 30
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            if attempt == MAX_RETRIES:
                raise
            print(f"  Error ({e}). Retrying in {wait}s ({attempt}/{MAX_RETRIES})...")
            time.sleep(wait)
            wait = min(wait * 2, 120)


def ollama_generate(prompt: str) -> str:
    response = ollama.chat(
        model=OLLAMA_MODEL,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0.3, "num_predict": 512},
    )
    return response["message"]["content"].strip()


def generate_image_cloudflare(prompt: str, negative_prompt: str, output_path: str) -> None:
    account_id = os.environ["CLOUDFLARE_ACCOUNT_ID"]
    api_token  = os.environ["CLOUDFLARE_API_TOKEN"]

    def _call():
        resp = requests.post(
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
            f"/ai/run/@cf/black-forest-labs/flux-1-schnell",
            headers={"Authorization": f"Bearer {api_token}"},
            json={"prompt": prompt, "num_steps": 4, "width": 1280, "height": 720},
        )
        resp.raise_for_status()
        return base64.b64decode(resp.json()["result"]["image"])

    image_bytes = with_retry(_call)
    Image.open(BytesIO(image_bytes)).save(output_path)
    print(f"  Saved: {output_path}")


def get_audio_duration(audio_path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def build_video(output_dir: str, image_count: int, audio_path: str, output_path: str) -> None:
    print("\nBuilding video...")
    duration  = get_audio_duration(audio_path)
    scene_dur = duration / image_count
    print(f"  Audio: {duration:.1f}s — {scene_dur:.2f}s per scene")

    abs_dir     = os.path.abspath(output_dir)
    concat_file = os.path.join(abs_dir, "concat.txt")
    with open(concat_file, "w") as f:
        for i in range(1, image_count + 1):
            img = os.path.join(abs_dir, f"{i}.png").replace("\\", "/")
            f.write(f"file '{img}'\n")
            f.write(f"duration {scene_dur:.4f}\n")
        last = os.path.join(abs_dir, f"{image_count}.png").replace("\\", "/")
        f.write(f"file '{last}'\n")

    subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", concat_file,
        "-i", audio_path,
        "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", output_path,
    ], check=True)
    print(f"  Video saved: {output_path}")


def upload_lottery_video(video_path: str, script_content: str, privacy_status: str = "public") -> None:
    print("\nGenerating metadata...")
    account_id = os.environ["CLOUDFLARE_ACCOUNT_ID"]
    api_token  = os.environ["CLOUDFLARE_API_TOKEN"]
    metadata   = generate_lottery_metadata(script_content, account_id, api_token)
    print(f"  Title: {metadata['title']}")
    print("\nUploading to YouTube...")
    youtube  = get_authenticated_service()
    response = initialize_upload(youtube, video_path, metadata, privacy_status)
    print(f"  Published: https://youtu.be/{response.get('id')}")


# ── Scene templates ────────────────────────────────────────────────────────────

_LOTTERY_SCENE_TEMPLATES = [
    {
        "id": "intro",
        "label": "Intro — channel opening",
        "keywords": [],
        "base_prompt": (
            "Grand opening title card for a Thai lottery prediction YouTube channel. "
            "Radiant golden lottery balls arranged in a circle, sparkles and light rays, "
            "festive Thai pattern border, deep blue and gold background, celebration energy"
        ),
    },
    {
        "id": "recap",
        "label": "Previous draw recap",
        "keywords": ["งวดที่แล้ว", "ผลออก", "ออกมา", "ทบทวน", "มาแล้ว"],
        "base_prompt": (
            "Lottery result announcement board with large glowing numbers displayed, "
            "confetti falling, Thai temple silhouette in background, "
            "warm golden light, celebratory mood"
        ),
    },
    {
        "id": "stats",
        "label": "Statistics analysis",
        "keywords": ["สถิติ", "วิเคราะห์", "ตัวร้อน", "ตัวเย็น", "ความถี่", "แนวโน้ม", "pattern"],
        "base_prompt": (
            "Data visualization concept with colorful bar charts and glowing number graphs, "
            "lottery balls with frequency labels floating around, "
            "blue and purple digital background, analytical mood"
        ),
    },
    {
        "id": "pred2",
        "label": "2-digit prediction",
        "keywords": ["สองตัว", "2 ตัว", "เลขสองตัว", "เลขท้าย 2", "เลขเด็ดสอง"],
        "base_prompt": (
            "Two huge glowing lucky numbers spotlighted on a dark stage, "
            "golden star bursts, red lucky envelopes flying around, "
            "Thai traditional lucky symbols, excitement and anticipation"
        ),
    },
    {
        "id": "pred4",
        "label": "4-digit prediction",
        "keywords": ["สี่ตัว", "4 ตัว", "เลขสี่ตัว", "เลขท้าย 4", "เลขเด็ดสี่"],
        "base_prompt": (
            "Four bold golden lottery numbers in a row, dramatic spotlight effect, "
            "lottery ticket texture background, silver and gold tones, "
            "Thai auspicious symbols, premium feel"
        ),
    },
    {
        "id": "dev",
        "label": "Dev lottery prediction",
        "keywords": ["พัฒนา", "หวยพัฒนา", "ลาวพัฒนา"],
        "base_prompt": (
            "Five lottery balls in a row each showing a number, "
            "Laos-inspired decorative border, green and gold color palette, "
            "development lottery concept art, joyful and prosperous mood"
        ),
    },
    {
        "id": "outro",
        "label": "Outro — subscribe CTA",
        "keywords": [],
        "base_prompt": (
            "YouTube subscribe button concept surrounded by lottery balls and golden confetti, "
            "notification bell icon, Thai good luck symbols, "
            "bright warm colors, cheerful farewell energy"
        ),
    },
]


def _detect_scene_type(para: str) -> str:
    for tmpl in _LOTTERY_SCENE_TEMPLATES[1:-1]:
        for kw in tmpl["keywords"]:
            if kw in para:
                return tmpl["id"]
    return "stats"


def _get_template(scene_id: str) -> dict:
    for t in _LOTTERY_SCENE_TEMPLATES:
        if t["id"] == scene_id:
            return t
    return _LOTTERY_SCENE_TEMPLATES[2]


# ── Scene splitting ────────────────────────────────────────────────────────────

def split_script_into_scenes(script: str, n_scenes: int = 7) -> list[dict]:
    # 1. Double-newline paragraphs
    units = [p.strip() for p in script.split("\n\n") if p.strip()]
    # 2. Single-newline lines
    if len(units) < 3:
        units = [p.strip() for p in script.split("\n") if p.strip()]
    # 3. Thai sentence endings
    if len(units) < 3:
        units = [s.strip() for s in re.split(r"(?<=ค่ะ)\s+|(?<=นะคะ)\s+|(?<=เลยค่ะ)\s+", script) if s.strip()]
    # 4. Fixed char chunks
    if len(units) < 3:
        chunk = max(1, len(script) // n_scenes)
        units = [script[i:i + chunk].strip() for i in range(0, len(script), chunk) if script[i:i + chunk].strip()]

    n_middle    = max(1, n_scenes - 2)
    bucket_size = max(1, len(units) // n_middle)
    buckets: list[str] = []
    for i in range(0, len(units), bucket_size):
        buckets.append(" ".join(units[i:i + bucket_size]))
    while len(buckets) < n_middle:
        buckets.append(buckets[-1] if buckets else "")
    buckets = buckets[:n_middle]

    middle_scenes: list[dict] = []
    for bucket in buckets:
        tmpl = _get_template(_detect_scene_type(bucket))
        middle_scenes.append({"label": tmpl["label"], "text": bucket, "template": tmpl})

    # Merge consecutive identical templates (keep longest text)
    merged: list[dict] = []
    for s in middle_scenes:
        if merged and merged[-1]["template"]["id"] == s["template"]["id"]:
            if len(s["text"]) > len(merged[-1]["text"]):
                merged[-1]["text"] = s["text"]
        else:
            merged.append(s)

    intro_tmpl = _get_template("intro")
    outro_tmpl = _get_template("outro")
    scenes = (
        [{"label": intro_tmpl["label"], "text": buckets[0], "template": intro_tmpl}]
        + merged
        + [{"label": outro_tmpl["label"], "text": buckets[-1], "template": outro_tmpl}]
    )
    return scenes[:n_scenes]


# ── Image prompt generation ────────────────────────────────────────────────────

def _extract_lottery_numbers(text: str, scene_id: str) -> list[str]:
    raw        = re.findall(r"\b\d+\b", text)
    four_digit = [n for n in raw if len(n) == 4 and not (2500 <= int(n) <= 2599)]
    two_digit  = [n for n in raw if len(n) == 2]

    if scene_id in ("pred4", "recap"):
        nums = four_digit[:3] + two_digit[:2]
    elif scene_id in ("pred2", "dev"):
        nums = two_digit[:5]
    else:
        nums = four_digit[:2] + two_digit[:3]

    seen: set[str] = set()
    result = []
    for n in nums:
        if n not in seen:
            seen.add(n)
            result.append(n)
    return result


def _numbers_to_visual_phrase(numbers: list[str]) -> str:
    if not numbers:
        return ""
    if len(numbers) == 1:
        return f"featuring the number {numbers[0]} as a giant glowing neon digit, center-stage focal point"
    listed = ", ".join(numbers[:-1]) + f" and {numbers[-1]}"
    return (
        f"featuring the lucky numbers {listed} as large glowing neon digits "
        f"prominently displayed, each number inside its own golden spotlight circle"
    )


def lottery_scene_to_image_prompt(scene: dict) -> str:
    base     = scene["template"]["base_prompt"]
    scene_id = scene["template"]["id"]
    text     = scene["text"] or ""

    numbers     = _extract_lottery_numbers(text, scene_id)
    number_hint = _numbers_to_visual_phrase(numbers)

    if scene_id in ("intro", "outro") or not text:
        return f"{base}, {number_hint}" if number_hint else base

    number_instruction = (
        f"The image MUST visually show these specific numbers: {', '.join(numbers)} "
        f"as large bold glowing digits — this is the most important requirement.\n"
        if numbers else
        "Focus on the lottery/statistics atmosphere.\n"
    )

    ollama_prompt = (
        f"Write an English image generation prompt for a Thai lottery YouTube video background.\n\n"
        f"Scene type: {scene['label']}\n"
        f"Base concept: {base}\n"
        f"Script context (Thai): {text[:200]}\n\n"
        f"{number_instruction}"
        f"Rules:\n"
        f"- 2-3 sentences total\n"
        f"- Vivid, specific visual description\n"
        f"- No people, no faces\n"
        f"- Numbers as glowing neon or golden 3D digits if present\n"
        f"- Return ONLY the image prompt, nothing else"
    )

    try:
        enhanced = ollama_generate(ollama_prompt).strip()
        if len(enhanced) < 20 or enhanced.lower().startswith(("i ", "here", "this ", "sure")):
            enhanced = base
    except Exception:
        enhanced = base

    return f"{enhanced}, {number_hint}" if number_hint else enhanced


# ── Pipeline ───────────────────────────────────────────────────────────────────

def find_latest_lottery_files(base_dir: Path) -> tuple[Path | None, Path | None]:
    txts = sorted(base_dir.glob("lottery_tts_*.txt"), reverse=True)
    if not txts:
        return None, None
    txt = txts[0]
    mp3 = txt.with_suffix(".mp3")
    return txt, mp3 if mp3.exists() else None


def lottery_pipeline(
    script_path: str | None,
    audio_path:  str | None,
    output_dir:  str,
    video_path:  str,
    privacy_status: str,
    n_scenes: int = 7,
    upload: bool = True,
) -> None:
    base_dir = Path(__file__).parent
    os.makedirs(output_dir, exist_ok=True)

    if not script_path or not audio_path:
        auto_txt, auto_mp3 = find_latest_lottery_files(base_dir)
        if not script_path:
            if not auto_txt:
                raise RuntimeError("No lottery_tts_*.txt found. Run gen_predict.py --tts first.")
            script_path = str(auto_txt)
            print(f"[INFO] Auto-detected script : {auto_txt.name}")
        if not audio_path:
            if not auto_mp3:
                raise RuntimeError("No lottery_tts_*.mp3 found. Run gen_predict.py --tts first.")
            audio_path = str(auto_mp3)
            print(f"[INFO] Auto-detected audio  : {auto_mp3.name}")

    script = Path(script_path).read_text(encoding="utf-8")
    scenes = split_script_into_scenes(script, n_scenes=n_scenes)

    print(f"\n[INFO] Generating {len(scenes)} scenes:")
    for i, s in enumerate(scenes, 1):
        print(f"  {i}. {s['label']}")

    for i, scene in enumerate(scenes, start=1):
        print(f"\n[{i}/{len(scenes)}] {scene['label']}")
        img_prompt  = lottery_scene_to_image_prompt(scene)
        full_prompt = f"{img_prompt}, {LOTTERY_STYLE}"
        print(f"  Prompt: {full_prompt[:120]}...")
        generate_image_cloudflare(full_prompt, LOTTERY_NEGATIVE, os.path.join(output_dir, f"{i}.png"))

    build_video(output_dir, len(scenes), audio_path, video_path)

    if upload:
        upload_lottery_video(video_path, script, privacy_status)
    else:
        print(f"\n[DONE] Video ready: {video_path}  (skipped upload)")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate lottery prediction YouTube video")
    parser.add_argument("--script",     default=None,  help="Path to lottery_tts_*.txt (auto-detect if omitted)")
    parser.add_argument("--audio",      default=None,  help="Path to lottery_tts_*.mp3 (auto-detect if omitted)")
    parser.add_argument("--output-dir", default="lottery_output")
    parser.add_argument("--video",      default="lottery_output/lottery_video.mp4")
    parser.add_argument("--scenes",     default=7, type=int, help="Number of visual scenes (default: 7)")
    parser.add_argument("--privacy",    default="public")
    parser.add_argument("--no-upload",  action="store_true", help="Skip YouTube upload")
    args = parser.parse_args()

    lottery_pipeline(
        script_path    = args.script,
        audio_path     = args.audio,
        output_dir     = args.output_dir,
        video_path     = args.video,
        privacy_status = args.privacy,
        n_scenes       = args.scenes,
        upload         = not args.no_upload,
    )


if __name__ == "__main__":
    main()
