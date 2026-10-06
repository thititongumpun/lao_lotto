"""
News text posts for the Facebook page — news_post.py

Six kinds of text-only posts (no URL in the body; the link goes in the first
comment so Content Monetization keeps paying and the 2-free-link-posts/month
cap on Meta One is never hit):

  hot     — one Gemini-rewritten story from /api/hot (dedupe per video id)
  digest  — 5 headlines, no LLM (dedupe per video id so two digests never repeat)
  lotto   — today's Lao lottery numbers from the lao_lottery table
  pm25    — morning PM2.5 readings from Air4Thai, plus an optional affiliate comment
  gold    — goldtraders.or.th announce prices, plus an optional affiliate comment
  hone    — daily summary of today's โหนกระแส episode (honekrasae.com write-up), text only

Run as a SHORT-LIVED subprocess (same contract as pipeline.py / horoscope.py):
    python -m news_post --kind hot|digest|lotto|pm25|gold|hone [--hours N] [--label TEXT] [--dry-run]
Last stdout line is RESULT_SENTINEL + json; everything else is logs.
"""

import argparse
from html import unescape
import json
import os
import re
import sys
from datetime import date, datetime, timezone

import psycopg2
import requests
import urllib3
from dotenv import load_dotenv

from horoscope import BANGKOK, thai_date
import poster
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
    "Answer JSON only with keys layout, has_text, sensitive, people, bg_subject, fx, emoji, tone, tag, l1, l2, l3. "
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
    "people: true only when real people (faces clearly visible) are the main subject of the photo; "
    "false for objects, vehicles, machines, buildings, places, animals or tiny distant figures. "
    'fx: the cartoon effect that fits the story best: "rain" (rain, flood, weather), "storm" (storms, '
    'shocking or angry news, scandal, clash), "fire" (fire, heat, heated conflict, crime), "money" (economy, '
    'prices, lottery, winnings, business), "party" (celebration, win, wedding, happy viral), "justice" (court, '
    'lawsuit, verdict, petition, legal fight), "police" (arrest, police raid, manhunt, crime scene), "sport" '
    '(sport, match, athlete, competition), "alert" (scam, warning, fraud, danger notice, recall), "none" when '
    'none of these fit. '
    "emoji: when fx is \"none\", 1-3 emoji naming the story's key things (e.g. elephant story -> [\"🐘\"], "
    "temple dispute -> [\"🛕\", \"📜\"]); [] otherwise. "
    "tone: when fx is \"none\", two hex colours [dark, light] for the backdrop that suit the story's mood "
    "(dark nearly black, light vivid); [] otherwise. "
    "tag: a Thai stamp word for the story, max 10 chars (e.g. ข่าวด่วน, เตือนภัย!, ช็อก!, ดราม่า, ถูกหวย!). "
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
HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")
MAX_POSTER_TRIES = 5  # stories checked for a usable photo before falling back to a text post
# n8n saves each reel's full poster (assets/card/cover.html) as <reel video_id>.jpg here after publishing
# (host /root/n8n/n8n_ffmpeg/covers, mounted read-only; kept 3 days). /api/hot story ids are those video ids.
COVER_DIR = os.getenv("REEL_COVER_DIR", "/covers")
COVER_CROP = (285, 1350)  # old 1080x1920 covers -> 1080x1350 (4:5): top offset + height (n8n now renders 4:5 directly)
HONE_URL = "https://www.honekrasae.com/group"
HONE_SITE = "https://www.honekrasae.com/content/"
# the show's write-ups are tagged either group; the text decides (see pick_hone)
HONE_GROUPS = "โหนกระแส,ข่าวกำลังโหน"
HONE_MARKERS = ("โหนกระแสวันนี้", "หนุ่ม กรรชัย", "ในรายการ", "โฟนอิน")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130"}

AIR4THAI_URL = "https://air4thai.pcd.go.th/services/getNewAQI_JSON.php"
PM25_PROVINCES = ["กรุงเทพ", "เชียงใหม่", "ขอนแก่น", "ภูเก็ต"]
PM25_DOT = {"1": "🔵", "2": "🟢", "3": "🟡", "4": "🟠", "5": "🔴"}  # Air4Thai color_id
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

GOLD_URL = "https://classic.goldtraders.or.th/"
GOLD_RE = re.compile(r'id="DetailPlace_uc_goldprices1_lbl(\w+)">(.*?)</span>', re.S)
GOLD_TIME_RE = re.compile(r"(\d\d)/(\d\d)/(\d{4}) เวลา (\d\d:\d\d) น\. \(ครั้งที่ (\d+)\)")

NOCODB_URL = os.getenv("NOCODB_BASE_URL", "").rstrip("/")
NOCODB_TOKEN = os.getenv("NOCODB_API_TOKEN", "")
NOCODB_TABLE = os.getenv("NOCODB_TABLE_NAME", "")


# ── Source ─────────────────────────────────────────────────────────────────────

def fetch_hot(hours: int, limit: int) -> list[dict]:
    resp = requests.get(HOT_URL, params={"hours": hours, "limit": limit}, timeout=20)
    resp.raise_for_status()
    items = resp.json()
    print(f"[NEWS] /api/hot hours={hours} limit={limit}: {len(items)} item(s)")
    return items


def parse_hone(html: str) -> list[dict]:
    """Next.js RSC payload (self.__next_f.push chunks) -> [{id, title, url, public_date, body}]."""
    # ponytail: scrapes the RSC stream; breaks if honekrasae changes its page shape
    text = "".join(json.loads(f'"{c}"') for c in re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.S))
    # long strings are hoisted into text rows "<hex id>:T<hex utf-8 byte length>,<text>" and referenced as "$<hex id>"
    raw = text.encode()
    rows = {m.group(1).decode(): raw[m.end():m.end() + int(m.group(2), 16)].decode("utf-8", "ignore")
            for m in re.finditer(rb"(?<![0-9A-Za-z])([0-9a-f]{1,6}):T([0-9a-f]+),", raw)}
    items, dec, seen = [], json.JSONDecoder(), set()
    for m in re.finditer(r'\{"id":"[A-Za-z0-9]{20}"', text):
        try:
            obj, _ = dec.raw_decode(text, m.start())
        except ValueError:
            continue
        if obj["id"] in seen or "content_detail" not in obj or not obj.get("public_date"):
            continue
        seen.add(obj["id"])
        detail = obj.get("content_detail") or ""
        if detail.startswith("$") and detail[1:] in rows:
            detail = rows[detail[1:]]
        body = re.sub(r"<[^>]+>", "\n", detail)
        body = "\n".join(l.strip() for l in unescape(body).splitlines() if l.strip())
        items.append({"id": obj["id"], "title": (obj.get("title") or "").strip(), "url": HONE_SITE + obj["id"],
                      "public_date": int(obj["public_date"]), "body": body})
    return items


def fetch_hone() -> list[dict]:
    resp = requests.get(HONE_URL, params={"groups": HONE_GROUPS, "title": "x"}, headers=UA, timeout=20)
    resp.raise_for_status()
    items = parse_hone(resp.text)
    print(f"[NEWS] honekrasae: {len(items)} item(s)")
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


def fetch_air4thai() -> list[dict]:
    # ponytail: verify=False — air4thai.pcd.go.th serves a broken cert chain; public read-only data, nothing sent
    resp = requests.get(AIR4THAI_URL, headers=UA, timeout=20, verify=False)
    resp.raise_for_status()
    return resp.json()["stations"]


def pick_pm25(stations: list[dict], today: str) -> list[tuple[str, float, str]]:
    out = []
    for province in PM25_PROVINCES:
        best = None
        for st in stations:
            try:
                if province not in st["areaTH"]:
                    continue
                last = st["AQILast"]
                if last["date"] != today:
                    continue
                value = float(last["PM25"]["value"])
                if value < 0:
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            if best is None or value > best[0]:
                best = (value, last["PM25"]["color_id"])
        if best is not None:
            out.append((province, best[0], best[1]))
    return out


def fetch_gold() -> str:
    resp = requests.get(GOLD_URL, headers=UA, timeout=20)
    resp.raise_for_status()
    return resp.text


def parse_gold(html: str) -> dict:
    fields = {m.group(1): re.sub(r"<[^>]+>", "", m.group(2)).strip() for m in GOLD_RE.finditer(html)}
    for name in ("BLSell", "BLBuy", "OMSell", "OMBuy", "AsTime"):
        if name not in fields:
            raise RuntimeError(f"goldtraders: missing {name}")
    m = GOLD_TIME_RE.match(fields["AsTime"])
    if not m:
        raise RuntimeError("goldtraders: unrecognised announce time")
    day, month, year, time_str, round_no = m.groups()
    def price(name):
        s = fields[name].strip().removesuffix(".00")
        float(s.replace(",", ""))  # numeric check; ValueError propagates on a schema change
        return s
    return {
        "bl_sell": price("BLSell"), "bl_buy": price("BLBuy"),
        "om_sell": price("OMSell"), "om_buy": price("OMBuy"),
        "date": date(int(year) - 543, int(month), int(day)),
        "time": time_str, "round": int(round_no),
    }


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


# ── Text-only lanes: daily โหนกระแส summary ──────────────────────────────────
# Content Monetization pays text posts per qualified view; comments/shares only
# buy distribution. A full rewrite in our own words keeps them clear of the
# unoriginal-content rule, and BAIT_WORDS keeps them clear of the engagement-bait demotion.

BAIT_WORDS = ("พิมพ์ 1", "พิมพ์1", "คอมเมนต์ 1", "กดแชร์", "แชร์ให้", "แชร์ต่อ", "แชร์เลย",
              "กดไลค์", "กดไลก์", "แท็กเพื่อน", "กดติดตาม")

# flash-lite often closes with a Thai question word and no "?"
QUESTION_END = re.compile(r"(\?|ไหม|มั้ย|ยังไง|อย่างไร|หรือไม่|บ้าง|อะไร|ไหน|เปล่า)\s*(คะ|ครับ|นะ|จ๊ะ)?\s*\??\s*$")

MIXED_SCRIPT = re.compile(r"[\u0e00-\u0e7f][A-Za-z]|[A-Za-z][\u0e00-\u0e7f]")

TEXT_SYSTEM = SYSTEM.replace("ความยาวรวมไม่เกิน 500 ตัวอักษร ", "") + (
    " ห้ามแต่งข้อเท็จจริงที่ไม่มีในข่าว "
    "คำถามปิดท้ายต้องเป็นคำถามปลายเปิดถามความเห็นจริง ลงท้ายด้วยเครื่องหมาย ? "
    "ห้ามขอให้พิมพ์ตัวเลข ห้ามชวนแชร์ ห้ามชวนแท็กเพื่อน "
    "ผู้ต้องหาในคดียังไม่ถูกศาลตัดสิน ห้ามสรุปว่าเขาผิด ใช้คำว่า ถูกกล่าวหา หรือ ถูกจับกุม "
    "เรียกผู้อ่านว่า คุณ"
)


def validate_text(text: str, min_chars: int, max_chars: int) -> str:
    """Strip markdown; reject wrong length, URLs, engagement bait, or no closing question."""
    text = re.sub(r"\*\*|__|^#{1,6}\s+|^```.*$", "", text, flags=re.M).strip()
    if not min_chars <= len(text) <= max_chars:
        raise RuntimeError(f"text post length {len(text)} outside {min_chars}-{max_chars}: {text!r}")
    if "http" in text.lower():
        raise RuntimeError(f"text post contains a URL: {text!r}")
    garbled = MIXED_SCRIPT.findall(text) + [c for c in text if c.isalpha() and not c.isascii() and not "\u0e00" <= c <= "\u0e7f"]
    if garbled:  # flash-lite sometimes drops Latin/other-script letters into Thai words ("พาหนocกลับ", "ทำުރร้าย")
        raise RuntimeError(f"text post has garbled letters {garbled}: {text!r}")
    leak = re.findall(r"<[^<>\n]{1,40}>", text)
    if leak:  # template slot copied into the post ("<ผู้ร้อง>ร้องเรียน")
        raise RuntimeError(f"text post has template placeholders {leak}: {text!r}")
    bait = [w for w in BAIT_WORDS if w in text]
    if bait:
        raise RuntimeError(f"text post has engagement bait {bait}: {text!r}")
    body = [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    if not body or not QUESTION_END.search(body[-1]):
        raise RuntimeError(f"text post does not end with a question: {text!r}")
    return text


def _gemini_text(prompt: str, min_chars: int, max_chars: int, max_tokens: int = 4096,
                 model: str | None = None, temperature: float = 0.85, attempts: int = 2) -> str:
    """Gemini call, retried on a rejected draft."""
    from gen_predict import call_gemini
    for attempt in range(1, attempts + 1):
        try:
            return validate_text(call_gemini(prompt, TEXT_SYSTEM, max_tokens, model, temperature), min_chars, max_chars)
        except RuntimeError as exc:
            if attempt == attempts:
                raise
            print(f"[NEWS] draft rejected, retrying: {exc}")


def pick_hone(items: list[dict], today: date) -> dict | None:
    """Today's episode write-up: published 12:00-20:59 Bangkok today and reads like the show; longest wins."""
    def ok(it):
        t = datetime.fromtimestamp(it["public_date"], BANGKOK)
        return t.date() == today and 12 <= t.hour <= 20 and any(k in it["body"] for k in HONE_MARKERS)
    return max((it for it in items if ok(it)), key=lambda it: len(it["body"]), default=None)


def build_hone_prompt(story: dict) -> str:
    # style modelled on a viral page recap of the 2026-10-05 episode (2.5k reactions, 6k shares):
    # drama hook headline (first 2 lines show before "ดูเพิ่มเติม": shock number + the line people quote),
    # story told in 🔵 chapters, every detail kept as ◼️ bullets, quotes kept
    return "\n".join([
        "บทความรายการโหนกระแสวันนี้ (ห้ามลอกคำ ให้เขียนใหม่ทั้งหมดด้วยสำนวนของคุณ):",
        f"หัวข้อ: {story['title']}",
        f"เนื้อหา: {story['body']}",
        "",
        "เล่าใหม่เป็นเรื่องเล่าที่ลุ้นเหมือนดูซีรีส์ ให้คนที่ไม่ได้ดูรายการรู้เรื่องครบเหมือนนั่งดูเอง "
        "ห้ามตัดรายละเอียดใดทิ้ง ต้องครบทุกประเด็น ทุกชื่อ ฉายา อายุ ตัวเลข วันที่ สถานที่ จำนวนเงิน "
        "ลำดับเหตุการณ์ คำถามของพิธีกร โฟนอิน หลักฐาน และคำชี้แจงของทุกฝ่าย "
        "คำพูดเด็ดของบุคคลในเรื่องให้ยกมาในเครื่องหมาย \"...\" คำสำคัญให้ใส่ในเครื่องหมาย \"...\" "
        "ห้ามแต่งเพิ่มสิ่งที่ไม่มีในเนื้อหา ทุกเรื่องที่เป็นคำกล่าวหาให้ใช้คำว่า อ้างว่า "
        "ห้ามตัดสินเองว่าฝ่ายไหนผิด ถ้าไม่มีคำชี้แจงของอีกฝ่ายในเนื้อหา ให้เขียนว่า ยังไม่มีคำชี้แจง "
        "ห้ามเขียนว่าเป็นการสรุปรายการ ห้ามขึ้นต้นชื่อช่วงด้วยคำว่า ช่วงที่ "
        "ในบรรทัด 💬 เท่านั้น ถ้าเป็นคำพูดที่คนอื่นเล่าต่อ ให้ระบุว่าใครอ้าง เช่น — เจ้าของศูนย์ (ตามที่คุณแม่อ้าง) "
        "ส่วนในเนื้อเรื่องให้เขียนว่า ใครอ้างว่า หรือ ใครชี้แจงว่า ตามปกติ ห้ามใส่วงเล็บ (ตามที่...อ้าง) "
        "เขียนภาษาไทยล้วน ห้ามมีตัวอักษรภาษาอื่นปนในคำไทย",
        "รูปแบบผลลัพธ์ที่ต้องส่งกลับ (แทนที่ <...> ด้วยข้อความของคุณ ส่วนที่ไม่ใช่ <...> ให้คงไว้ตามนี้):",
        "",
        "📣 <ฮุกบรรทัดเดียว ไม่เกิน 70 ตัวอักษร ห้ามขึ้นต้นแบบรายงานข่าว เช่น คุณแม่อ้างว่า... "
        "ให้ขึ้นต้นด้วยภาพที่ช็อกที่สุดหรือความย้อนแย้งที่เจ็บที่สุดของเรื่อง (สิ่งที่สร้างมา vs สิ่งที่เจอ, คนใกล้ตัว vs สิ่งที่ทำ) "
        "พร้อมตัวเลขที่ช็อก ใช้คำว่า ร้อง ต่อท้ายผู้ร้องจริงในเรื่อง (เช่น แม่ร้อง หนุ่มร้อง) หรือ ถูกกล่าวหา แทนการยืนยันข้อเท็จจริง ลงท้ายด้วย ! "
        "ตัวอย่างโครงสร้าง (ห้ามลอกคำ): สร้าง X มากับมือ แต่ Y — แม่ร้อง Z>",
        "<บรรทัดที่ 2 ไม่เกิน 120 ตัวอักษร: เกิดอะไรขึ้นโดยย่อ และความคืบหน้าล่าสุด ใช้คำว่า อ้างว่า>",
        "💬 \"<คำพูดที่สะเทือนใจหรือเดือดที่สุดในเรื่อง ยกมาตามเนื้อหา>\" — <ใครพูด และใครเป็นคนอ้าง ถ้าเป็นคำเล่าต่อ>",
        "",
        "🔵 <ชื่อช่วงแรกแบบมีสีสัน เล่าปูมหลังตัวละคร>",
        "◼️ <รายละเอียดทีละประเด็น ประเด็นละ 1–3 ประโยค ครบทุกข้อ>",
        "",
        "🔵 <ชื่อช่วงถัดไปแบบมีสีสัน เช่น จุดเริ่มต้นรอยร้าว / นาทีระทึก / ความจริงที่ช็อก>",
        "◼️ <...>",
        "",
        "(แบ่ง 4–7 ช่วงตามลำดับเรื่อง ช่วงคำชี้แจงหรือโฟนอินของอีกฝ่ายต้องแยกเป็นช่วงของตัวเอง "
        "และช่วงสุดท้ายคือความคืบหน้าล่าสุดหรือสิ่งที่หน่วยงานจะทำต่อ)",
        "",
        "<คำถามปลายเปิด 1 ประโยค ให้ผู้อ่านคิดว่าถ้าเป็นตัวเองหรือคนในบ้านจะทำอย่างไร หรือเห็นด้วยกับฝ่ายไหน ลงท้ายด้วย ?>",
        "#โหนกระแส <แฮชแท็กคำสำคัญของเรื่อง 1–2 คำ ไม่มีเว้นวรรคในแฮชแท็ก>",
    ])


def validate_poster_plan(plan: dict) -> dict:
    """Gemini's poster JSON -> {layout, has_text, sensitive, people, bg_prompt, fx, tag, emoji, tone, lines[3]}; rejects missing or empty lines."""
    lines = [str(plan.get(k) or "").strip() for k in ("l1", "l2", "l3")]
    if not all(lines):
        raise RuntimeError(f"poster plan missing headline lines: {plan!r}")
    layout = plan.get("layout") if plan.get("layout") in ("portrait", "scene") else "scene"
    subject = str(plan.get("bg_subject") or "").strip()
    tag = str(plan.get("tag") or "").strip()
    return {"layout": layout, "has_text": plan.get("has_text") is True,
            "sensitive": plan.get("sensitive") in (True, "true"),
            "people": plan.get("people") is True,  # fail safe: a string "true" still blocks the AI background
            "bg_prompt": f"{subject}, {BG_STYLE}" if subject else "", "lines": lines,
            "fx": plan.get("fx") if plan.get("fx") in poster.FX_TONE else "none",
            "tag": tag if 0 < len(tag) <= 12 else "ข่าวด่วน",
            "emoji": [e.strip() for e in (plan.get("emoji") if isinstance(plan.get("emoji"), list) else [])
                      if isinstance(e, str) and 0 < len(e.strip()) <= 8 and not any(c.isalnum() for c in e)][:3],
            "tone": _tone(plan.get("tone"))}


def _tone(value) -> tuple | None:
    """["#rrggbb" dark, "#rrggbb" light] -> ((r, g, b), (r, g, b)) darker first, or None (bad or grey)."""
    if not (isinstance(value, list) and len(value) == 2
            and all(isinstance(v, str) and HEX_RE.fullmatch(v.strip()) for v in value)):
        return None
    rgb = sorted((tuple(int(v.strip()[i:i + 2], 16) for i in (1, 3, 5)) for v in value), key=sum)
    light = rgb[1]
    if max(light) - min(light) < 80:  # greyish "vivid" colour -> dull poster; use the default tone
        return None
    return tuple(rgb)


def crop_cover(data: bytes, out_path: str) -> str | None:
    """1080x1920 reel cover -> 4:5 photo post (scaled if Facebook serves a smaller copy). None if not 9:16."""
    import io

    from PIL import Image

    im = Image.open(io.BytesIO(data)).convert("RGB")
    if abs(im.height / im.width - 1.25) < 0.02:  # n8n now renders the poster as 4:5 with the whole photo: post as is
        im.save(out_path, "JPEG", quality=92)
        return out_path
    if im.height < im.width * 1.7:
        return None
    k = im.width / 1080
    top, h = round(COVER_CROP[0] * k), round(COVER_CROP[1] * k)
    im.crop((0, top, im.width, top + h)).save(out_path, "JPEG", quality=92)
    return out_path


def reel_cover(story: dict, out_path: str) -> str | None:
    """The reel's own poster (rendered by n8n, no anchor) as the photo post, cropped to 4:5. A local file read:
    no Gemini, no Workers AI, no Facebook call. None -> caller builds the old poster."""
    path = os.path.join(COVER_DIR, f"{story['id']}.jpg")
    try:
        with open(path, "rb") as f:
            out = crop_cover(f.read(), out_path)
        print(f"[NEWS] reel poster: {path} -> {out}")
        return out
    except FileNotFoundError:
        return None
    except Exception as exc:
        print(f"[NEWS] reel poster skipped: {exc}")
        return None


def build_poster(story: dict, out_path: str) -> str | None:
    """Photo poster for a hot story, or None (caller posts text only). Never raises."""
    try:
        import io

        from PIL import Image

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
        # pop-out only for real people: rembg also "finds" people in machines and buildings
        pop = poster.people(photo) if background is None and plan["people"] and not plan["sensitive"] else None
        return poster.render(photo, plan["lines"], f"ภาพ: {credit}", out_path, person, background,
                             sensitive=plan["sensitive"], pop=pop, fx=plan["fx"], tag=plan["tag"],
                             emoji=plan["emoji"], tone=plan["tone"])
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


def build_pm25_message(readings: list[tuple[str, float, str]], today: date) -> str:
    line = " | ".join(f"{p} {round(v)} {PM25_DOT.get(c, '⚪')}" for p, v, c in readings)
    return "\n".join([
        f"🌫️ ค่าฝุ่น PM2.5 เช้านี้ {thai_date(today)}",
        line,
        "เปิดหน้าต่างได้ไหมวันนี้ 😷",
        "#ฝุ่น #PM25",
    ])


def build_gold_message(g: dict) -> str:
    return "\n".join([
        f"🪙 ราคาทองวันนี้ {thai_date(g['date'])} (ประกาศครั้งที่ {g['round']} เวลา {g['time']})",
        f"ทองแท่ง ขายออก {g['bl_sell']} | รับซื้อ {g['bl_buy']}",
        f"รูปพรรณ ขายออก {g['om_sell']} | รับซื้อ {g['om_buy']}",
        "ราคานี้ซื้อหรือขาย?",
        "#ราคาทอง #ทองวันนี้",
    ])


PM25_RGB = {"1": (0, 150, 220), "2": (40, 160, 70), "3": (220, 170, 0), "4": (240, 110, 20), "5": (210, 30, 30)}


def build_card(kind: str, data, today: date, out_path: str, label: str = "") -> str | None:
    """4:5 number card for a scheduled post (photo posts get ~3x the views of text). None -> text post."""
    try:
        d = thai_date(today)
        if kind == "gold":
            return poster.card("ราคาทองวันนี้", f"{d} · ครั้งที่ {data['round']} เวลา {data['time']}",
                               [("ทองแท่ง ขายออก", data["bl_sell"]), ("ทองแท่ง รับซื้อ", data["bl_buy"]),
                                ("รูปพรรณ ขายออก", data["om_sell"]), ("รูปพรรณ รับซื้อ", data["om_buy"])], out_path)
        if kind == "pm25":
            return poster.card("ค่าฝุ่น PM2.5 เช้านี้", f"{d} · µg/m³",
                               [(p, str(round(v)), PM25_RGB.get(c, (20, 20, 20))) for p, v, c in data],
                               out_path, accent=(120, 200, 255))
        if kind == "lotto":
            return poster.card("ผลหวยลาว", d, [("เลข 4 ตัว", data["digit4"]), ("เลข 3 ตัว", data["digit3"]),
                                               ("เลข 2 ตัว", data["digit2"]), ("สัตว์นำโชค", data["animal"]),
                                               ("เลขเสริม", data["dev_lottery"])], out_path, accent=(255, 140, 0))
        if kind == "digest":
            return poster.card(label, d, [("", f"{i + 1}. {s['title'].strip()}") for i, s in enumerate(data[:5])],
                               out_path, accent=(230, 57, 70))
    except Exception as exc:
        print(f"[NEWS] {kind} card skipped: {exc}")
    return None


# ── Affiliate ──────────────────────────────────────────────────────────────────

def unescape_title(t: str) -> str:
    """NocoDB stores the comment's newlines as literal backslash-n; turn them back into real newlines."""
    return t.replace("\\n", "\n").strip()


def affiliate_comment(tag: str) -> str | None:
    """Least-recently-used active NocoDB affiliate row tagged `tag`, as comment text. None = nothing to post."""
    if not (NOCODB_URL and NOCODB_TOKEN and NOCODB_TABLE):
        return None
    try:
        h = {"xc-token": NOCODB_TOKEN}
        url = f"{NOCODB_URL}/api/v2/tables/{NOCODB_TABLE}/records"
        r = requests.get(url, headers=h, timeout=15,
                         params={"where": f"(tag,eq,{tag})~and(active,eq,true)", "limit": 50})
        r.raise_for_status()
        rows = r.json().get("list") or []
        if not rows:
            print(f"[NEWS] affiliate: no active row tagged {tag}")
            return None
        # ponytail: LRU picked client-side over ≤50 rows; move to a server sort if the tag list grows
        row = min(rows, key=lambda r: r.get("last_used_at") or "")
        requests.patch(url, headers=h, timeout=15,
                       json=[{"Id": row["Id"], "last_used_at": datetime.now(timezone.utc).isoformat()}]).raise_for_status()
        return unescape_title(row["title"])
    except Exception as exc:  # an affiliate hiccup never blocks the news post
        print(f"[NEWS] affiliate skipped: {exc}")
        return None


def post_affiliate(post_id: str, tag: str) -> bool:
    """Second comment with a NocoDB affiliate product; never raises — the news post is already live."""
    c = affiliate_comment(tag)
    if not c:
        return False
    try:
        from facebook import post_comment
        post_comment(post_id, c)
        return True
    except Exception as exc:
        print(f"[NEWS] affiliate comment failed: {exc}")
        return False


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
            poster_path = reel_cover(s, f"/tmp/poster_{s['id']}.jpg") or build_poster(s, f"/tmp/poster_{s['id']}.jpg")
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
        image = build_card("digest", stories, datetime.now(BANGKOK).date(), "/tmp/card_digest.jpg", label)
        if dry_run:
            return {"status": "dry_run", "video_ids": ids, "message": message, "card": image}
        comment = "\n".join(
            f"{DIGIT_EMOJI[i]} {s['url']}" for i, s in enumerate(stories)
        )
        post_id = _publish(message, comment, image)
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
        image = build_card("lotto", row, row["date"], "/tmp/card_lotto.jpg")
        if dry_run:
            return {"status": "dry_run", "video_id": key, "message": message, "card": image}
        post_id = _publish(message, f"ดูผลย้อนหลังและวิเคราะห์เลขเด็ด 👉 {SITE_URL}", image)
        mark_posted(key, "lotto", post_id)
        return {"status": "ok", "video_id": key, "post_id": post_id}
    except Exception as exc:
        print(f"[NEWS] lotto error: {exc}")
        return {"status": "error", "error": str(exc)}


def run_pm25(dry_run: bool = False) -> dict:
    try:
        ensure_table()
        today = datetime.now(BANGKOK).date()
        key = today.isoformat()
        if already_posted(key, "pm25"):
            return {"status": "skipped", "reason": "already_posted", "date": key}
        readings = pick_pm25(fetch_air4thai(), key)
        if not readings:
            raise RuntimeError("air4thai: no fresh reading for today")
        message = build_pm25_message(readings, today)
        print(message)
        image = build_card("pm25", readings, today, "/tmp/card_pm25.jpg")
        if dry_run:
            # ponytail: dry-run still rotates last_used_at; fine for a test knob
            return {"status": "dry_run", "video_id": key, "message": message, "card": image,
                    "affiliate": affiliate_comment("pm25")}
        post_id = _publish(message, "ดูค่าฝุ่นทุกสถานี 👉 https://air4thai.pcd.go.th", image)
        mark_posted(key, "pm25", post_id)
        return {"status": "ok", "video_id": key, "post_id": post_id, "affiliate": post_affiliate(post_id, "pm25")}
    except Exception as exc:
        print(f"[NEWS] pm25 error: {exc}")
        return {"status": "error", "error": str(exc)}


def run_gold(dry_run: bool = False) -> dict:
    try:
        ensure_table()
        g = parse_gold(fetch_gold())
        key = f"{g['date'].isoformat()}-{g['round']}"
        if g["date"] != datetime.now(BANGKOK).date():
            print(f"[NEWS] gold: announce date {key} is not today")
            return {"status": "skipped", "reason": "not_today", "date": key}
        if already_posted(key, "gold"):
            return {"status": "skipped", "reason": "already_posted", "date": key}
        message = build_gold_message(g)
        print(message)
        image = build_card("gold", g, g["date"], "/tmp/card_gold.jpg")
        if dry_run:
            # ponytail: dry-run still rotates last_used_at; fine for a test knob
            return {"status": "dry_run", "video_id": key, "message": message, "card": image,
                    "affiliate": affiliate_comment("gold")}
        post_id = _publish(message, "ราคาทองสมาคมค้าทองคำ 👉 https://www.goldtraders.or.th", image)
        mark_posted(key, "gold", post_id)
        return {"status": "ok", "video_id": key, "post_id": post_id, "affiliate": post_affiliate(post_id, "gold")}
    except Exception as exc:
        print(f"[NEWS] gold error: {exc}")
        return {"status": "error", "error": str(exc)}


def run_hone(dry_run: bool = False) -> dict:
    """Daily summary of today's โหนกระแส episode from the honekrasae.com write-up; link in comment."""
    try:
        ensure_table()
        today = datetime.now(BANGKOK).date()
        story = pick_hone(fetch_hone(), today)
        if story is None:
            return {"status": "skipped", "reason": "no_episode"}
        if already_posted(story["id"], "hone"):
            return {"status": "skipped", "reason": "already_posted", "id": story["id"]}
        print(f"[NEWS] hone: {story['id']} {story['title']}")
        # HONE_GEMINI_MODEL switches only this lane (e.g. a stronger model for long full-detail rewrites)
        model = os.getenv("HONE_GEMINI_MODEL") or None
        print(f"[NEWS] hone model: {model or 'default'}")
        # long rewrite at 0.85 garbled a word with Arabic letters in 2 drafts running (2026-10-06): run cooler, 3 tries
        message = _gemini_text(build_hone_prompt(story), 800, 12000, max_tokens=16384, model=model,
                               temperature=0.4, attempts=3)
        print(message)
        if dry_run:
            return {"status": "dry_run", "id": story["id"], "message": message}
        post_id = _publish(message, f"ที่มา: รายการโหนกระแส 👉 {story['url']}")
        mark_posted(story["id"], "hone", post_id)
        return {"status": "ok", "id": story["id"], "post_id": post_id}
    except Exception as exc:
        print(f"[NEWS] hone error: {exc}")
        return {"status": "error", "error": str(exc)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["hot", "digest", "lotto", "pm25", "gold", "hone"], required=True)
    ap.add_argument("--hours", type=int, default=None)
    ap.add_argument("--label", default="สรุปข่าวเช้า")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.kind == "hot":
        res = run_hot(a.hours or 6, a.dry_run)
    elif a.kind == "digest":
        res = run_digest(a.hours or 12, a.label, a.dry_run)
    elif a.kind == "pm25":
        res = run_pm25(a.dry_run)
    elif a.kind == "gold":
        res = run_gold(a.dry_run)
    elif a.kind == "hone":
        res = run_hone(a.dry_run)
    else:
        res = run_lotto(a.dry_run)
    res["ran_at"] = datetime.now().isoformat()
    sys.stdout.flush()
    print(RESULT_SENTINEL + json.dumps(res, ensure_ascii=False))
