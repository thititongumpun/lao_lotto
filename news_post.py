"""
News text posts for the Facebook page — news_post.py

Three kinds of text-only posts (no URL in the body; the link goes in the first
comment so Content Monetization keeps paying and the 2-free-link-posts/month
cap on Meta One is never hit):

  hot     — one Gemini-rewritten story from /api/hot (dedupe per video id)
  digest  — 5 headlines, no LLM (dedupe per video id so two digests never repeat)
  lotto   — today's Lao lottery numbers from the lao_lottery table

Run as a SHORT-LIVED subprocess (same contract as pipeline.py / horoscope.py):
    python -m news_post --kind hot|digest|lotto [--hours N] [--label TEXT] [--dry-run]
Last stdout line is RESULT_SENTINEL + json; everything else is logs.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

import psycopg2
import requests
from dotenv import load_dotenv

from horoscope import BANGKOK, thai_date
from pipeline import RESULT_SENTINEL

load_dotenv()

HOT_URL = "https://www.1minhotspot.com/api/hot"
SITE_URL = "https://www.1minhotspot.com/"
CATEGORY_TAGS = {
    "society": "#ข่าวสังคม", "entertainment": "#บันเทิง", "politics": "#การเมือง",
    "viral": "#ไวรัล", "economy": "#เศรษฐกิจ",
}
DIGIT_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
MIN_HOT_CHARS = 120

SYSTEM = (
    "คุณคือนักเขียนข่าวภาษาไทยสำหรับโพสต์ข้อความบนเพจ Facebook "
    "หน้าที่ของคุณคือเรียบเรียงข่าวที่ได้รับใหม่ทั้งหมดด้วยสำนวนของคุณเอง ห้ามลอกประโยคต้นฉบับ "
    "แต่ต้องคงข้อเท็จจริง ชื่อ ตัวเลข และสถานที่ให้ถูกต้อง "
    "ผู้อ่านอายุ 40–70 ปี ใช้ประโยคสั้น คำง่าย อ่านแล้วเข้าใจทันที "
    "ตอบเป็นข้อความธรรมดาเท่านั้น ห้ามใช้ markdown (ไม่มี ** # หัวข้อ หรือ -) "
    "ห้ามใส่ลิงก์หรือ URL ใด ๆ ห้ามระบุชื่อสำนักข่าวหรือแหล่งที่มา "
    "ห้ามชวนกดไลค์ กดแชร์ หรือกดติดตาม "
    "ความยาวรวมไม่เกิน 500 ตัวอักษร "
    "ห้ามเพิ่มคำนำ คำลงท้าย หรือคำอธิบายใด ๆ นอกโครงสร้างที่กำหนด"
)


POSTER_SYSTEM = (
    "You design a square Facebook news poster from a news photo and its Thai story. "
    "Answer JSON only with keys layout, has_text, sensitive, bg_subject, l1, l2, l3. "
    'layout: "portrait" when ONE main person fills a large part of the photo and could be cut out cleanly '
    '(headshot, press photo, studio shot); "scene" for anything else (places, accidents, crowds, '
    "several people, objects, blurred faces, screenshots). "
    "has_text: true only when big headline or caption text is laid over the photo (a ready-made news thumbnail "
    "or collage with a title); a small corner watermark or logos on clothing, signs or products do NOT count. "
    "sensitive: true when the story involves death, a child victim, serious injury, sexual crime, suicide, or a "
    "disaster with casualties; false otherwise (politics, court rulings without deaths, celebrity, economy, viral). "
    "bg_subject: English, ONE jaw-dropping, story-specific scene a viewer would stop scrolling for: "
    "exaggerated scale, motion and drama around the story's key symbols, placed on the LEFT half of the frame (the person stands right of centre) "
    '(e.g. "towering Thai Supreme Court facade under a stormy sky split by lightning, a giant golden judge '
    'gavel slamming down on the left with sparks and shattering marble, scattered ballot papers swirling in the wind"), '
    "no people. "
    "l1, l2, l3: Thai headline in three lines, facts only from the story, no source name, no emoji. "
    "l1 = what happened (max 24 chars), l2 = the key name or keyword (max 14 chars), "
    "l3 = one supporting detail (max 30 chars)."
)
BG_STYLE = ("epic cinematic movie-poster key art, volumetric god rays, storm clouds, lens flare, glowing sparks and "
            "embers, deep blue versus fiery red-orange colour contrast, hyper-detailed, main subject on the left, "
            "darker open space on the right for a person cutout, no people, no text, no letters, no logos, no watermark")
# Source sites the 1minhotspot article page links to -> credit shown on the poster.
PUBLISHERS = {"khaosod.co.th": "ข่าวสด", "sanook.com": "Sanook", "thaipbs.or.th": "Thai PBS"}
SOURCE_RE = re.compile(r'https://(?:www\.|news\.)?(' + "|".join(map(re.escape, PUBLISHERS)) + r')/[^"\'\s<>\\]+')
OG_IMAGE_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]*content=["\']([^"\']+)'
    r'|<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']og:image["\']'
)
NO_POSTER = {"sanook.com"}
MAX_POSTER_TRIES = 5  # stories checked for a usable photo before falling back to a text post
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130"}


# ── Source ─────────────────────────────────────────────────────────────────────

def fetch_hot(hours: int, limit: int) -> list[dict]:
    resp = requests.get(HOT_URL, params={"hours": hours, "limit": limit}, timeout=20)
    resp.raise_for_status()
    items = resp.json()
    print(f"[NEWS] /api/hot hours={hours} limit={limit}: {len(items)} item(s)")
    return items


def fetch_source_photo(story: dict) -> tuple[bytes, str]:
    """/api/hot has no photo: 1minhotspot article page -> original source link -> its og:image.
    Returns (jpeg/png bytes, publisher credit name)."""
    # ponytail: scrapes the article HTML; add sourceUrl to /api/hot (D1 clip_scripts.source_url) if this breaks
    page = requests.get(story["url"], headers=UA, timeout=20).text
    m = SOURCE_RE.search(page)
    if not m:
        raise RuntimeError("no source link on the article page")
    if m.group(1) in NO_POSTER:
        raise RuntimeError(f"{m.group(1)} thumbnails carry their own headline")
    src = requests.get(m.group(0), headers=UA, timeout=20).text
    og = OG_IMAGE_RE.search(src)
    if not og:
        raise RuntimeError(f"no og:image on {m.group(0)}")
    resp = requests.get(og.group(1) or og.group(2), headers=UA, timeout=30)
    resp.raise_for_status()
    print(f"[NEWS] photo: {m.group(0)} ({len(resp.content)} bytes)")
    return resp.content, PUBLISHERS[m.group(1)]


# ── Dedupe table ───────────────────────────────────────────────────────────────

def _conn():
    # ponytail: copied from main.get_conn — importing main pulls fastapi + the scheduler
    dsn = os.environ.get("LOTTO_DB_URL")
    if not dsn:
        raise RuntimeError("LOTTO_DB_URL environment variable not set.")
    return psycopg2.connect(dsn)


def ensure_table() -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS fb_text_posts ("
            "video_id text, kind text, post_id text, "
            "posted_at timestamptz default now(), primary key (video_id, kind))"
        )


def already_posted(video_id: str, kind: str) -> bool:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM fb_text_posts WHERE video_id=%s AND kind=%s", (video_id, kind))
        return cur.fetchone() is not None


def mark_posted(video_id: str, kind: str, post_id: str) -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO fb_text_posts (video_id, kind, post_id) VALUES (%s, %s, %s) "
            "ON CONFLICT DO NOTHING",
            (video_id, kind, post_id),
        )


# ── Messages ───────────────────────────────────────────────────────────────────

def build_hot_prompt(story: dict) -> str:
    tag = CATEGORY_TAGS.get(story.get("category", ""), "#ข่าวสังคม")
    return "\n".join([
        "ข่าวต้นฉบับ (ห้ามลอกคำ ให้เขียนใหม่):",
        f"หัวข้อ: {story['title']}",
        f"เนื้อหา: {story['body']}",
        "",
        "รูปแบบผลลัพธ์ที่ต้องส่งกลับ (แทนที่ <...> ด้วยข้อความของคุณ ส่วนที่ไม่ใช่ <...> ให้คงไว้ตามนี้):",
        "",
        "🔥 <หัวข้อสั้น ไม่เกิน 60 ตัวอักษร>",
        "<บรรทัด 1: เกิดอะไรขึ้น>",
        "<บรรทัด 2: รายละเอียดสำคัญ>",
        "<บรรทัด 3: ผลกระทบหรือความคืบหน้า>",
        "❓ <คำถามชวนคุย 1 ประโยค>",
        f"#ข่าววันนี้ {tag}",
    ])


def validate_hot(text: str) -> str:
    """Strip markdown (same regex as horoscope.rewrite) and reject unusable output."""
    text = re.sub(r"\*\*|__|^#{1,6}\s+|^```.*$", "", text, flags=re.M).strip()
    if len(text) < MIN_HOT_CHARS:
        raise RuntimeError(f"hot message too short ({len(text)} chars): {text!r}")
    if "http" in text.lower():
        raise RuntimeError(f"hot message contains a URL: {text!r}")
    return text


def build_hot_message(story: dict) -> str:
    from gen_predict import call_gemini
    return validate_hot(call_gemini(build_hot_prompt(story), SYSTEM))


def validate_poster_plan(plan: dict) -> dict:
    """Gemini's poster JSON -> {layout, has_text, sensitive, bg_prompt, lines[3]}; rejects missing or empty lines."""
    lines = [str(plan.get(k) or "").strip() for k in ("l1", "l2", "l3")]
    if not all(lines):
        raise RuntimeError(f"poster plan missing headline lines: {plan!r}")
    layout = plan.get("layout") if plan.get("layout") in ("portrait", "scene") else "scene"
    subject = str(plan.get("bg_subject") or "").strip()
    return {"layout": layout, "has_text": plan.get("has_text") is True,
            "sensitive": plan.get("sensitive") in (True, "true"),  # fail safe: a string "true" still blocks the AI background
            "bg_prompt": f"{subject}, {BG_STYLE}" if subject else "", "lines": lines}


def build_poster(story: dict, out_path: str) -> str | None:
    """Photo poster for a hot story, or None (caller posts text only). Never raises."""
    try:
        import io

        from PIL import Image

        import poster
        from gen_predict import call_gemini_json

        photo_bytes, credit = fetch_source_photo(story)
        photo = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
        thumb = io.BytesIO()
        photo.copy().resize((min(photo.width, 768), round(photo.height * min(photo.width, 768) / photo.width))) \
            .save(thumb, "JPEG", quality=85)  # small copy for Gemini: fewer image tokens
        plan = validate_poster_plan(call_gemini_json(
            f"หัวข้อ: {story['title']}\nเนื้อหา: {story['body'][:1500]}", POSTER_SYSTEM, thumb.getvalue()))
        print(f"[NEWS] poster plan: {plan}")
        person = background = None
        if plan["sensitive"]:
            print("[NEWS] sensitive story -> scene layout, no AI background")
        elif plan["layout"] == "portrait" and plan["bg_prompt"]:
            person = poster.cutout(photo)
            if person is None:
                print("[NEWS] cutout rejected -> scene layout")
            else:
                from gen_image import generate_image_klein
                bg_path = out_path + ".bg.jpg"
                generate_image_klein(plan["bg_prompt"], bg_path)
                background = Image.open(bg_path)
        if background is None and plan["has_text"]:  # scene layout would print our headline over theirs
            print("[NEWS] source photo already has headline text -> text-only post")
            return None
        return poster.render(photo, plan["lines"], f"ภาพ: {credit}", out_path, person, background)
    except Exception as exc:
        print(f"[NEWS] poster skipped: {exc}")
        return None


def build_digest_message(stories: list[dict], label: str) -> str:
    lines = [f"📰 {label} {thai_date(datetime.now(BANGKOK).date())}", ""]
    lines += [f"{DIGIT_EMOJI[i]} {s['title'].strip()}" for i, s in enumerate(stories[:5])]
    lines += ["", "อยากรู้ข่าวไหนเพิ่ม คอมเมนต์เลขไว้ได้เลย", "#สรุปข่าว #ข่าววันนี้"]
    return "\n".join(lines)


def build_lotto_message(row: dict) -> str:
    return "\n".join([
        f"🎰 ผลหวยลาว {thai_date(row['date'])}",
        "",
        f"เลข 4 ตัว: {row['digit4']}",
        f"เลข 3 ตัว: {row['digit3']}",
        f"เลข 2 ตัว: {row['digit2']}",
        f"สัตว์นำโชค: {row['animal']}",
        f"เลขเสริม: {row['dev_lottery']}",
        "",
        "#หวยลาว #ผลหวยลาว",
    ])


# ── Post ───────────────────────────────────────────────────────────────────────

def _publish(message: str, comment: str, image_path: str | None = None) -> str:
    from facebook import post_comment, post_photo_to_facebook, post_text_to_facebook
    post_id = None
    if image_path:
        try:
            post_id = post_photo_to_facebook(image_path, message)["post_id"]
        except Exception as exc:  # the story still goes out as text
            print(f"[NEWS] photo post failed, posting text: {exc}")
    if post_id is None:
        post_id = post_text_to_facebook(message)["id"]
    post_comment(post_id, comment)
    return post_id


# ── Orchestration ──────────────────────────────────────────────────────────────

def run_hot(hours: int = 6, dry_run: bool = False) -> dict:
    try:
        ensure_table()
        candidates = [s for s in fetch_hot(hours, 20)
                      if (s.get("body") or "").strip() and not already_posted(s["id"], "hot")]
        if not candidates:
            print("[NEWS] hot: no new story")
            return {"status": "skipped", "reason": "no_new_story"}
        # first story that yields a poster; none in the first few -> the newest one as a text post
        story, image = candidates[0], None
        for s in candidates[:MAX_POSTER_TRIES]:
            print(f"[NEWS] hot candidate: {s['id']} {s['title']}")
            poster_path = build_poster(s, f"/tmp/poster_{s['id']}.jpg")
            if poster_path:
                story, image = s, poster_path
                break
        print(f"[NEWS] hot: {story['id']} {story['title']} ({'poster' if image else 'text only'})")
        message = build_hot_message(story)
        print(message)
        if dry_run:
            return {"status": "dry_run", "video_id": story["id"], "message": message, "poster": image}
        # story["url"] is the /news/<slug> article link from /api/hot. Facebook
        # scrapes the comment link for its card; the /v/<id> redirect got a bare
        # "1minhotspot.com" card, the article URL gets the headline + image.
        post_id = _publish(message, f"อ่านฉบับเต็ม 👉 {story['url']}", image)
        mark_posted(story["id"], "hot", post_id)
        return {"status": "ok", "video_id": story["id"], "post_id": post_id, "poster": bool(image)}
    except Exception as exc:
        print(f"[NEWS] hot error: {exc}")
        return {"status": "error", "error": str(exc)}


def run_digest(hours: int = 12, label: str = "สรุปข่าวเช้า", dry_run: bool = False) -> dict:
    try:
        ensure_table()
        stories = [
            s for s in fetch_hot(hours, 20)
            if (s.get("title") or "").strip() and not already_posted(s["id"], "digest")
        ][:5]
        if not stories:
            print("[NEWS] digest: no new story")
            return {"status": "skipped", "reason": "no_new_story"}
        message = build_digest_message(stories, label)
        print(message)
        ids = [s["id"] for s in stories]
        if dry_run:
            return {"status": "dry_run", "video_ids": ids, "message": message}
        comment = "\n".join(
            f"{DIGIT_EMOJI[i]} {s['url']}" for i, s in enumerate(stories)
        )
        post_id = _publish(message, comment)
        for vid in ids:
            mark_posted(vid, "digest", post_id)
        return {"status": "ok", "video_ids": ids, "post_id": post_id}
    except Exception as exc:
        print(f"[NEWS] digest error: {exc}")
        return {"status": "error", "error": str(exc)}


def run_lotto(dry_run: bool = False) -> dict:
    try:
        ensure_table()
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT date, digit4, digit3, digit2, animal, dev_lottery "
                        "FROM lao_lottery ORDER BY date DESC LIMIT 1")
            r = cur.fetchone()
        if r is None:
            return {"status": "skipped", "reason": "no_rows"}
        row = dict(zip(["date", "digit4", "digit3", "digit2", "animal", "dev_lottery"], r))
        key = row["date"].isoformat()
        if row["date"] != datetime.now(BANGKOK).date():
            print(f"[NEWS] lotto: latest draw {key} is not today")
            return {"status": "skipped", "reason": "not_today", "date": key}
        if already_posted(key, "lotto"):
            return {"status": "skipped", "reason": "already_posted", "date": key}
        message = build_lotto_message(row)
        print(message)
        if dry_run:
            return {"status": "dry_run", "video_id": key, "message": message}
        post_id = _publish(message, f"ดูผลย้อนหลังและวิเคราะห์เลขเด็ด 👉 {SITE_URL}")
        mark_posted(key, "lotto", post_id)
        return {"status": "ok", "video_id": key, "post_id": post_id}
    except Exception as exc:
        print(f"[NEWS] lotto error: {exc}")
        return {"status": "error", "error": str(exc)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["hot", "digest", "lotto"], required=True)
    ap.add_argument("--hours", type=int, default=None)
    ap.add_argument("--label", default="สรุปข่าวเช้า")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.kind == "hot":
        res = run_hot(a.hours or 6, a.dry_run)
    elif a.kind == "digest":
        res = run_digest(a.hours or 12, a.label, a.dry_run)
    else:
        res = run_lotto(a.dry_run)
    res["ran_at"] = datetime.now().isoformat()
    sys.stdout.flush()
    print(RESULT_SENTINEL + json.dumps(res, ensure_ascii=False))
