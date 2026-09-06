"""
Daily horoscope text post — horoscope.py

Reads Sanook's horoscope RSS, picks the 7 per-birth-day articles for the target
day (items are published at 17:01 UTC the day before = 00:01 Asia/Bangkok),
extracts the structured horoscope from each article, rewrites all 7 in fresh
Thai wording with Gemini and publishes ONE text-only post to the Facebook page.

Run as a SHORT-LIVED subprocess (same contract as pipeline.py):
    python -m horoscope [--date YYYY-MM-DD] [--dry-run]
Last stdout line is RESULT_SENTINEL + json; everything else is logs.
"""

import argparse
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from pipeline import RESULT_SENTINEL

FEED_URL = "https://rssfeeds.sanook.com/rss/feeds/sanook/horoscope.index.xml"
BANGKOK = ZoneInfo("Asia/Bangkok")

# Sanook blocks the default requests UA (same dict as main.FETCH_HEADERS; copied
# so this subprocess doesn't import fastapi/psycopg2 just for a header).
FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

THAI_DAYS = ["อาทิตย์", "จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์"]
DAY_EMOJI = ["☀️", "🌙", "🔥", "💚", "🧡", "💙", "💜"]
THAI_MONTHS_REV = {
    1: "มกราคม", 2: "กุมภาพันธ์", 3: "มีนาคม",   4: "เมษายน",
    5: "พฤษภาคม", 6: "มิถุนายน",  7: "กรกฎาคม",  8: "สิงหาคม",
    9: "กันยายน", 10: "ตุลาคม",   11: "พฤศจิกายน", 12: "ธันวาคม",
}

_BIRTHDAY_RE = re.compile(r"เกิดวัน(" + "|".join(THAI_DAYS) + ")")


# ── Feed ───────────────────────────────────────────────────────────────────────

def parse_feed(xml_text: str, target: date) -> list[dict]:
    """Items whose pubDate (UTC) is the day before `target`, one per birth day,
    ordered Sunday → Saturday."""
    want = target - timedelta(days=1)
    by_day: dict[str, dict] = {}
    for item in ET.fromstring(xml_text).iter("item"):
        pub = item.findtext("pubDate") or ""
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        try:
            if parsedate_to_datetime(pub).date() != want:
                continue
        except (TypeError, ValueError):
            continue
        m = _BIRTHDAY_RE.search(title)
        if m and m.group(1) not in by_day:
            by_day[m.group(1)] = {"birthday": m.group(1), "title": title, "link": link}
    return [by_day[d] for d in THAI_DAYS if d in by_day]


def fetch_feed_items(target: date) -> list[dict]:
    resp = requests.get(FEED_URL, headers=FETCH_HEADERS, timeout=20)
    resp.raise_for_status()
    items = parse_feed(resp.text, target)
    missing = [d for d in THAI_DAYS if d not in {i["birthday"] for i in items}]
    if missing:
        print(f"[HOROSCOPE] missing birth days for {target}: {missing}")
    if not items:
        raise RuntimeError(f"no horoscope items in feed for {target} (pubDate {target - timedelta(days=1)})")
    return items


# ── Article ────────────────────────────────────────────────────────────────────

def _label_value(li) -> tuple[str, str]:
    text = li.get_text(" ", strip=True)
    label, sep, value = text.partition(":")
    return (label.strip(), value.strip()) if sep else ("", text)


def parse_article(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    body = soup.select_one("#EntryReader_0")
    if body is None:
        raise ValueError("article body #EntryReader_0 not found")

    out: dict = {"work": "", "money": "", "love": "", "tip": "", "gem": "",
                 "color": "", "numbers": "", "auspicious_header": "", "auspicious": []}
    keymap = {"การงาน": "work", "การเงิน": "money", "ความรัก": "love",
              "อัญมณีมงคล": "gem", "สีมงคล": "color", "เลขนำโชค": "numbers"}

    for h2 in body.find_all("h2"):
        head = h2.get_text(" ", strip=True)
        ul = h2.find_next_sibling("ul")
        if ul is None or "เพิ่มเติม" in head:
            continue
        if head.startswith("ฤกษ์ดี"):
            out["auspicious_header"] = head
            out["auspicious"] = [li.get_text(" ", strip=True) for li in ul.find_all("li")]
            continue
        for li in ul.find_all("li"):
            label, value = _label_value(li)
            key = keymap.get(label)
            if key:
                out[key] = value
            elif head.startswith("เคล็ดลับ") and not out["tip"]:
                out["tip"] = value
    return out


def fetch_article(url: str) -> dict:
    resp = requests.get(url, headers=FETCH_HEADERS, timeout=20)
    resp.raise_for_status()
    return parse_article(resp.text)


# ── Rewrite ────────────────────────────────────────────────────────────────────

def thai_date(d: date) -> str:
    weekday = THAI_DAYS[(d.weekday() + 1) % 7]
    return f"วัน{weekday}ที่ {d.day} {THAI_MONTHS_REV[d.month]} {d.year + 543}"


SYSTEM = (
    "คุณคือนักเขียนคอนเทนต์ดูดวงภาษาไทยสำหรับเพจ Facebook "
    "หน้าที่ของคุณคือเรียบเรียงคำทำนายที่ได้รับใหม่ทั้งหมดด้วยสำนวนของคุณเอง "
    "ห้ามลอกประโยคต้นฉบับ แต่ต้องคงความหมายเดิมทุกข้อ และคงตัวเลข สี อัญมณี "
    "และช่วงเวลาฤกษ์ไว้ตรงตามต้นฉบับทุกตัวอักษร "
    "ขยายความแต่ละข้อได้เล็กน้อย (1–2 ประโยค) ให้อ่านลื่นและอบอุ่น "
    "ตอบเป็นข้อความธรรมดาเท่านั้น ห้ามใช้ markdown (ไม่มี ** # หรือ -) "
    "ห้ามเพิ่มคำนำ คำลงท้าย หรือคำอธิบายใด ๆ นอกโครงสร้างที่กำหนด"
)


def build_prompt(target: date, days: list[dict]) -> str:
    lines = [f"วันที่ทำนาย: {thai_date(target)}", "", "ข้อมูลต้นฉบับ (ห้ามลอกคำ ให้เขียนใหม่):"]
    for d in days:
        h = d["horoscope"]
        lines += [
            f"[เกิดวัน{d['birthday']}]",
            f"การงาน: {h['work']}", f"การเงิน: {h['money']}", f"ความรัก: {h['love']}",
            f"เคล็ดลับ: {h['tip']}", f"อัญมณี: {h['gem']}", f"สี: {h['color']}", f"เลขนำโชค: {h['numbers']}",
            "",
        ]
    # ponytail: weekly ฤกษ์ block is identical across the 7 articles — take it from the first
    first = days[0]["horoscope"]
    lines += [first["auspicious_header"] or "ฤกษ์ดีประจำสัปดาห์", *first["auspicious"], ""]

    fmt = [f"🔮 ดวงประจำ{thai_date(target)}", ""]
    for i, d in enumerate(days):
        emoji = DAY_EMOJI[THAI_DAYS.index(d["birthday"])]
        fmt += [
            f"{emoji} ชาววัน{d['birthday']}",
            "💼 การงาน: <เขียนใหม่>", "💰 การเงิน: <เขียนใหม่>", "❤️ ความรัก: <เขียนใหม่>",
            "✨ เคล็ดลับ: <เขียนใหม่>", f"💎 อัญมณี: {d['horoscope']['gem']}",
            f"🎨 สีมงคล: {d['horoscope']['color']}", f"🔢 เลขนำโชค: {d['horoscope']['numbers']}",
            "",
        ]
    fmt += ["⏰ <หัวข้อฤกษ์ดีประจำสัปดาห์ตามต้นฉบับ>", "<แต่ละบรรทัดฤกษ์ คงเวลาเดิม เขียนคำอธิบายใหม่>", "",
            "#ดูดวงรายวัน #ดวงวันนี้ #ดวงวันเกิด"]
    lines += ["รูปแบบผลลัพธ์ที่ต้องส่งกลับ (แทนที่ <...> ด้วยข้อความของคุณ ส่วนที่ไม่ใช่ <...> ให้คงไว้ตามนี้):", "", *fmt]
    return "\n".join(lines)


def rewrite(prompt: str) -> str:
    # ponytail: single Gemini call for all 7 days; split per-day if output truncates (max_output_tokens=4096)
    from gen_predict import call_gemini
    text = call_gemini(prompt, SYSTEM)
    text = re.sub(r"\*\*|__|^#{1,6}\s+|^```.*$", "", text, flags=re.M).strip()  # keeps #hashtags
    if len(text) < 200:
        raise RuntimeError(f"rewrite too short ({len(text)} chars): {text!r}")
    return text


# ── Orchestration ──────────────────────────────────────────────────────────────

def run_horoscope(target: date | None = None, dry_run: bool = False) -> dict:
    now = datetime.now().isoformat()
    target = target or datetime.now(BANGKOK).date()
    try:
        items = fetch_feed_items(target)
        print(f"[HOROSCOPE] {target}: {len(items)} article(s)")
        for it in items:
            it["horoscope"] = fetch_article(it["link"])
            print(f"[HOROSCOPE] เกิดวัน{it['birthday']}: {it['horoscope']['work']} / {it['horoscope']['numbers']}")
            time.sleep(1)

        text = rewrite(build_prompt(target, items))
        print(text)
        result = {"status": "ok", "ran_at": now, "date": target.isoformat(),
                  "days": len(items), "text": text}
        if dry_run:
            result["facebook"] = "skipped (dry run)"
        else:
            from facebook import post_text_to_facebook
            result["facebook"] = post_text_to_facebook(text)
    except Exception as exc:
        result = {"status": "error", "error": str(exc), "ran_at": now}
        print(f"[HOROSCOPE] error: {exc}")
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", type=date.fromisoformat, default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    res = run_horoscope(a.date, a.dry_run)
    sys.stdout.flush()
    print(RESULT_SENTINEL + json.dumps(res, ensure_ascii=False))
