"""
Bangkok traffic text post for the Facebook page — traffic.py

TomTom flowSegmentData at fixed points on major roads → median per road →
heavy/slow roads as a short text post. No LLM, no links.

Run as a SHORT-LIVED subprocess (same contract as news_post.py):
    python -m traffic [--dry-run]
Last stdout line is RESULT_SENTINEL + json; everything else is logs.
"""

import argparse
import json
import os
import statistics
import sys
from datetime import datetime

import requests
from dotenv import load_dotenv

from horoscope import BANGKOK
from news_post import _conn, ensure_table, mark_posted
from pipeline import RESULT_SENTINEL

load_dotenv()

URL = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"
MIN_GAP_MIN = int(os.getenv("TRAFFIC_MIN_GAP_MIN", "60"))
DAILY_CAP = int(os.getenv("TRAFFIC_DAILY_CAP", "2300"))
MAX_ROADS = 7
REPEAT_AFTER_MIN = 180  # same congestion signature re-posts after this long

ROADS = {
    "ratchada": "รัชดาฯ", "vibhavadi": "วิภาวดี", "chaeng": "แจ้งวัฒนะ", "latphrao": "ลาดพร้าว",
    "phahon": "พหลโยธิน", "rama9": "พระราม 9", "phetchaburi": "เพชรบุรี", "sukhumvit": "สุขุมวิท",
    "bangna": "บางนา-ตราด", "kanchana": "กาญจนาภิเษก", "rama2": "พระราม 2", "rama3": "พระราม 3",
    "ratchaphruek": "ราชพฤกษ์", "borom": "บรมราชชนนี",
}

# on-carriageway OSM nodes, TomTom-probed 2026-10-01 (FRC1-3 surface road; kanchana_west FRC0 = the road itself).
# ponytail: hand-calibrated; re-probe with --dry-run (segments/snapped) if TomTom's map changes
POINTS = [
    {"name": n, "road": r, "lat": la, "lon": lo} for n, r, la, lo in [
        ("ratchada_huaykwang", "ratchada", 13.7840, 100.5741),
        ("ratchada_rama9", "ratchada", 13.7625, 100.5682),
        ("vibhavadi_ladyao", "vibhavadi", 13.82874, 100.55787),
        ("vibhavadi_laksi", "vibhavadi", 13.87016, 100.57357),
        ("chaeng_govcomplex", "chaeng", 13.89176, 100.56398),
        ("chaeng_pakkret", "chaeng", 13.9025, 100.5350),
        ("latphrao_chokchai", "latphrao", 13.79697, 100.59001),
        ("latphrao_wanghin", "latphrao", 13.78185, 100.61709),
        ("phahon_ari", "phahon", 13.7800, 100.5448),
        ("phahon_kaset", "phahon", 13.84374, 100.57789),
        ("rama9_rca", "rama9", 13.7545, 100.5850),
        ("rama9_east", "rama9", 13.74235, 100.63226),
        ("phetchaburi_chidlom", "phetchaburi", 13.7502, 100.5510),
        ("phetchaburi_thonglo", "phetchaburi", 13.74606, 100.57922),
        ("sukhumvit_phromphong", "sukhumvit", 13.7295, 100.5710),
        ("sukhumvit_bangchak", "sukhumvit", 13.6930, 100.6070),
        ("bangna_central", "bangna", 13.66709, 100.63850),
        ("bangna_mega", "bangna", 13.66125, 100.66110),
        ("kanchana_bangyai", "kanchana", 13.8450, 100.4130),
        ("kanchana_west", "kanchana", 13.76467, 100.40712),
        ("rama2_bangmot", "rama2", 13.67976, 100.46886),
        ("rama2_km11", "rama2", 13.65457, 100.42678),
        ("rama3_bangkholaem", "rama3", 13.68811, 100.51612),
        ("rama3_chongnonsi", "rama3", 13.70063, 100.54548),
        ("ratchaphruek_bangwaek", "ratchaphruek", 13.74774, 100.44766),
        ("ratchaphruek_north", "ratchaphruek", 13.81004, 100.44967),
        ("borom_talingchan", "borom", 13.78061, 100.44525),
        ("borom_thawiwatthana", "borom", 13.78514, 100.38942),
    ]
]

LEVELS = ["heavy", "slow", "moderate", "free"]
EMOJI = {"heavy": "🔴", "slow": "🟠", "moderate": "🟡", "free": "🟢"}
LABEL = {"heavy": "รถติดหนัก", "slow": "ชะลอตัว", "moderate": "หนาแน่น", "free": "รถคล่อง"}
CONGESTED = ("heavy", "slow")


# ── TomTom ─────────────────────────────────────────────────────────────────────

def normalize(p: dict, js: dict, collected_at: str) -> dict | None:
    f = js.get("flowSegmentData")
    if not f or f.get("freeFlowSpeed", 0) <= 0 or f.get("confidence", 0) < 0.5:
        return None
    if any(f.get(k) is None for k in ("currentSpeed", "currentTravelTime", "freeFlowTravelTime")):
        return None
    # closed road reads speed 0 → false "heavy"; closure text is out of scope
    if f.get("frc") not in ("FRC0", "FRC1", "FRC2", "FRC3") or f.get("roadClosure"):
        return None
    ratio = min(f["currentSpeed"] / f["freeFlowSpeed"], 1.0)
    fftt = f.get("freeFlowTravelTime") or 0
    coords = (f.get("coordinates") or {}).get("coordinate") or []
    # coords[0] is the segment start (can be km away); nearest point = where the query snapped
    snapped = min(coords, key=lambda c: (c["latitude"] - p["lat"]) ** 2 + (c["longitude"] - p["lon"]) ** 2,
                  default=None)
    return {
        "road": p["road"], "road_name": ROADS[p["road"]], "segment_id": p["name"],
        "latitude": p["lat"], "longitude": p["lon"],
        "current_speed": f["currentSpeed"], "freeflow_speed": f["freeFlowSpeed"],
        "speed_ratio": ratio, "congestion_pct": round((1 - ratio) * 100),
        "travel_time_ratio": f.get("currentTravelTime", 0) / fftt if fftt else 1.0,
        "frc": f["frc"], "confidence": f.get("confidence"), "snapped": snapped, "collected_at": collected_at,
    }


def fetch_point(p: dict, key: str, collected_at: str) -> dict | None:
    # never log the key or the full URL (it carries the key)
    try:
        r = requests.get(URL, params={"key": key, "point": f"{p['lat']},{p['lon']}", "unit": "kmph"},
                         timeout=10)
    except requests.RequestException as exc:
        print(f"[TRAFFIC] {p['name']} failed: {type(exc).__name__}")
        return None
    if r.status_code in (403, 429):
        raise RuntimeError(f"tomtom {r.status_code}")
    if r.status_code != 200:
        print(f"[TRAFFIC] {p['name']} failed: http {r.status_code}")
        return None
    try:
        js = r.json()
    except ValueError:
        print(f"[TRAFFIC] {p['name']} failed: bad json")
        return None
    try:
        seg = normalize(p, js, collected_at)
    except (TypeError, KeyError, AttributeError):  # malformed payload must not abort the other points
        print(f"[TRAFFIC] {p['name']} failed: bad payload")
        return None
    if seg is None:  # log the fields normalize() checks, never the URL/key
        f = js.get("flowSegmentData") or {}
        why = {k: f.get(k) for k in ("frc", "confidence", "roadClosure", "freeFlowSpeed", "currentSpeed",
                                      "currentTravelTime", "freeFlowTravelTime")} if f else "no flowSegmentData"
        print(f"[TRAFFIC] {p['name']} dropped: {why}")
    return seg


# ── Aggregate ──────────────────────────────────────────────────────────────────

def classify(r: float) -> str:
    return "heavy" if r < 0.40 else "slow" if r < 0.60 else "moderate" if r < 0.80 else "free"


def aggregate(segs: list[dict]) -> list[dict]:
    by_road: dict[str, list[dict]] = {}
    for s in segs:
        by_road.setdefault(s["road"], []).append(s)
    roads = []
    for road, ss in by_road.items():
        row = {k: statistics.median(s[k] for s in ss) for k in
               ("current_speed", "freeflow_speed", "speed_ratio", "congestion_pct", "travel_time_ratio")}
        row.update(road=road, road_name=ROADS[road], segment_count=len(ss),
                   traffic_level=classify(row["speed_ratio"]))
        # one odd segment must not make a whole road "heavy"
        if row["traffic_level"] == "heavy" and sum(s["speed_ratio"] < 0.40 for s in ss) < 2:
            row["traffic_level"] = "slow"
        roads.append(row)
    return sorted(roads, key=lambda r: (LEVELS.index(r["traffic_level"]), r["speed_ratio"]))


def select(roads: list[dict]) -> list[dict]:
    picked = [r for r in roads if r["traffic_level"] in CONGESTED][:MAX_ROADS]
    if not picked:
        return []
    return roads[:max(3, len(picked))]  # roads is severity-sorted: pad with next-worst


def signature(roads: list[dict]) -> str:
    return ",".join(sorted(f"{r['road']}:{r['traffic_level']}" for r in roads
                           if r["traffic_level"] in CONGESTED))


def build_message(roads: list[dict], now: datetime) -> str:
    lines = ["🚗 รายงานการจราจรกรุงเทพฯ", ""]
    for r in roads:
        l = r["traffic_level"]
        lines.append(f"{EMOJI[l]} {r['road_name']} {LABEL[l]} ความเร็วราว {round(r['current_speed'])} กม./ชม.")
    lines += ["", f"อัปเดตล่าสุด {now:%H:%M} น.", "ข้อมูล: TomTom Traffic"]
    return "\n".join(lines)


# ── DB ─────────────────────────────────────────────────────────────────────────

def ensure_usage() -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("CREATE TABLE IF NOT EXISTS traffic_api_usage ("
                    "day date primary key, requests int not null default 0)")


def used_today(day) -> int:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT requests FROM traffic_api_usage WHERE day=%s", (day,))
        row = cur.fetchone()
        return row[0] if row else 0


def add_usage(day, n: int) -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO traffic_api_usage (day, requests) VALUES (%s, %s) "
                    "ON CONFLICT (day) DO UPDATE SET requests = traffic_api_usage.requests + EXCLUDED.requests",
                    (day, n))


def usage(days: int = 7) -> list[dict]:
    ensure_usage()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT day, requests FROM traffic_api_usage ORDER BY day DESC LIMIT %s", (days,))
        return [{"day": d.isoformat(), "requests": n} for d, n in cur.fetchall()]


def last_post() -> tuple[str | None, float | None]:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT video_id, extract(epoch from now()-posted_at)/60 FROM fb_text_posts "
                    "WHERE kind='traffic' ORDER BY posted_at DESC LIMIT 1")
        row = cur.fetchone()
    if not row:
        return None, None
    return row[0].split("|", 1)[1], float(row[1])


# ── Run ────────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> dict:
    try:
        key = os.getenv("TOMTOM_API_KEY")
        if not key:
            raise RuntimeError("TOMTOM_API_KEY not set")
        ensure_table()
        ensure_usage()
        now = datetime.now(BANGKOK)
        day = now.date()
        used = used_today(day)
        if used + len(POINTS) > DAILY_CAP:
            return {"status": "skipped", "reason": "budget_exhausted", "used": used}
        last_sig, ago = last_post()
        if not dry_run and ago is not None and ago < MIN_GAP_MIN:
            return {"status": "skipped", "reason": "too_soon", "minutes_ago": round(ago)}
        collected_at = now.isoformat()
        segs, calls = [], 0
        try:
            for p in POINTS:
                calls += 1
                s = fetch_point(p, key, collected_at)
                if s:
                    segs.append(s)
        finally:
            add_usage(day, calls)
        if not segs:
            raise RuntimeError("no valid segments")
        roads = aggregate(segs)
        picked = select(roads)
        sig = signature(roads)
        if not picked:
            res = {"status": "skipped", "reason": "no_congestion", "calls": calls}
            if dry_run:  # diagnosable even when nothing is congested
                res.update(roads=roads, segments=segs)
            return res
        if not dry_run and sig == last_sig and ago < REPEAT_AFTER_MIN:
            return {"status": "skipped", "reason": "unchanged", "signature": sig, "calls": calls}
        message = build_message(picked, now)
        print(message)
        if dry_run:
            return {"status": "dry_run", "message": message, "signature": sig, "roads": roads,
                    "segments": segs, "calls": calls}
        from facebook import post_text_to_facebook
        key = f"{now:%Y-%m-%dT%H:%M}|{sig}"
        # row first: if FB accepts but we time out/crash, the next tick waits a gap instead of reposting
        mark_posted(key, "traffic", "pending")
        try:
            post_id = post_text_to_facebook(message)["id"]
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code < 500:  # FB clearly rejected: free the slot
                with _conn() as conn, conn.cursor() as cur:
                    cur.execute("DELETE FROM fb_text_posts WHERE video_id=%s AND kind='traffic'", (key,))
            raise  # timeouts/conn errors/5xx keep the pending row (FB may have accepted the post)
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("UPDATE fb_text_posts SET post_id=%s WHERE video_id=%s AND kind='traffic'",
                        (post_id, key))
        return {"status": "ok", "post_id": post_id, "signature": sig, "calls": calls}
    except Exception as exc:
        print(f"[TRAFFIC] error: {exc}")
        return {"status": "error", "error": str(exc)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    res = run(a.dry_run)
    res["ran_at"] = datetime.now().isoformat()
    sys.stdout.flush()
    print(RESULT_SENTINEL + json.dumps(res, ensure_ascii=False))
