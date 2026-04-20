#!/usr/bin/env python3
"""
Lao lottery fetcher — FastAPI + APScheduler.

Runs the fetch job every midnight (server local time).
Environment variables (or .env file):
  LOTTO_DB_URL  — PostgreSQL DSN, e.g. postgresql://user:pass@localhost:5432/mydb
"""

import os
import re
import sys
from contextlib import asynccontextmanager
from datetime import datetime

import psycopg2
import psycopg2.extras
import requests
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

load_dotenv()

URL = "https://www.sanook.com/news/laolotto/"

FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

THAI_MONTHS = {
    "มกราคม": 1,  "กุมภาพันธ์": 2,  "มีนาคม": 3,   "เมษายน": 4,
    "พฤษภาคม": 5, "มิถุนายน": 6,    "กรกฎาคม": 7,  "สิงหาคม": 8,
    "กันยายน": 9, "ตุลาคม": 10,     "พฤศจิกายน": 11, "ธันวาคม": 12,
}

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS lao_lottery (
    date        DATE         PRIMARY KEY,
    digit4      CHAR(4)      NOT NULL,
    digit3      CHAR(3)      NOT NULL,
    digit2      CHAR(2)      NOT NULL,
    animal      VARCHAR(20)  NOT NULL,
    dev_lottery VARCHAR(20)  NOT NULL
);
"""

INSERT_SQL = """
INSERT INTO lao_lottery (date, digit4, digit3, digit2, animal, dev_lottery)
VALUES (%(date)s, %(digit4)s, %(digit3)s, %(digit2)s, %(animal)s, %(dev_lottery)s)
ON CONFLICT (date) DO NOTHING;
"""


# ── Helpers ────────────────────────────────────────────────────────────────────

def parse_thai_date(text: str) -> str | None:
    match = re.search(r"(\d{1,2})\s+([^\s\d]+)\s+(\d{4})", text.strip())
    if not match:
        return None
    day, month_th, year_be = match.group(1), match.group(2).strip(), int(match.group(3))
    month = THAI_MONTHS.get(month_th)
    if not month:
        return None
    return f"{year_be - 543:04d}-{month:02d}-{int(day):02d}"


def _parse_latest(soup: BeautifulSoup) -> dict:
    result = {"digit4": "", "digit3": "", "digit2": "", "animal": "", "dev_lottery": ""}

    strong_4 = soup.select_one("strong.textBold")
    if strong_4:
        result["digit4"] = strong_4.get_text(strip=True)

    head_wrap = soup.find("div", class_="headWrap")
    if head_wrap:
        strong_a = head_wrap.find("strong")
        if strong_a:
            result["animal"] = strong_a.get_text(strip=True)

    tdHalf = soup.find("div", class_="tdHalf")
    if tdHalf:
        for td in tdHalf.find_all("div", recursive=False):
            h3 = td.find("h3")
            strong = td.find("strong")
            if not h3 or not strong:
                continue
            label = h3.get_text(strip=True)
            val = strong.get_text(strip=True)
            if "3 ตัว" in label:
                result["digit3"] = val
            elif "2 ตัว" in label:
                result["digit2"] = val

    td_full = soup.find("div", class_="tdFull")
    if td_full:
        p = td_full.find("p")
        if p:
            nums = [s.get_text(strip=True) for s in p.find_all("strong") if s.get_text(strip=True)]
            result["dev_lottery"] = " ".join(nums)

    return result


def _parse_archive(soup: BeautifulSoup) -> list[dict]:
    results = []
    for block in soup.find_all("div", class_="LaoLottoArchiveTable"):
        h2 = block.find("h2")
        if not h2:
            continue
        date_str = parse_thai_date(h2.get_text(" ", strip=True))
        if not date_str:
            continue

        entry: dict = {
            "date": date_str, "digit4": "", "digit3": "",
            "digit2": "", "animal": "", "dev_lottery": "",
        }
        valid = False

        for type_div in block.find_all("div", class_="type"):
            span = type_div.find("span")
            label = span.get_text(strip=True) if span else ""
            raw_text = " ".join(
                s.strip() for s in type_div.strings
                if s.strip() and s.strip() not in label
            )
            digits = re.findall(r"\b\d+\b", raw_text)

            if "4 ตัว" in label:
                nums = [d for d in digits if len(d) == 4]
                if nums:
                    entry["digit4"] = nums[0]
                    valid = True
            elif "3 ตัว" in label:
                nums = [d for d in digits if len(d) == 3]
                if nums:
                    entry["digit3"] = nums[0]
            elif "2 ตัว" in label:
                nums = [d for d in digits if len(d) == 2]
                if nums:
                    entry["digit2"] = nums[0]

        other = block.find("div", class_="otherNum")
        if other:
            dev_nums = [
                s.get_text(strip=True)
                for s in other.find_all("span")
                if re.fullmatch(r"\d{2}", s.get_text(strip=True))
            ]
            entry["dev_lottery"] = " ".join(dev_nums)

        if valid:
            results.append(entry)

    return results


# ── Fetch ──────────────────────────────────────────────────────────────────────

def fetch_page() -> BeautifulSoup | None:
    try:
        resp = requests.get(URL, headers=FETCH_HEADERS, timeout=15)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException as e:
        print(f"[ERROR] Network error: {e}", file=sys.stderr)
        return None


def fetch_latest(soup: BeautifulSoup) -> dict | None:
    date_str = None
    for h2 in soup.find_all("h2"):
        txt = h2.get_text(" ", strip=True)
        if "หวยลาว" in txt and "งวดประจำวันที่" not in txt and "ย้อนหลัง" not in txt:
            date_str = parse_thai_date(txt)
            if date_str:
                break

    if not date_str:
        print("[WARN] Date not found — using today.", file=sys.stderr)
        date_str = datetime.today().strftime("%Y-%m-%d")

    result = _parse_latest(soup)
    if not result["digit4"] and not result["digit2"]:
        print("[WARN] No lottery numbers found — page structure may have changed.", file=sys.stderr)
        return None

    result["date"] = date_str
    return result


# ── Database ───────────────────────────────────────────────────────────────────

def get_conn():
    dsn = os.environ.get("LOTTO_DB_URL")
    if not dsn:
        raise RuntimeError("LOTTO_DB_URL environment variable not set.")
    return psycopg2.connect(dsn)


def save(rows: list[dict]) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
            inserted = 0
            for row in rows:
                cur.execute(INSERT_SQL, row)
                if cur.rowcount:
                    inserted += 1
                    print(
                        f"[SAVED] {row['date']} — "
                        f"4-digit: {row['digit4']}  3-digit: {row['digit3']}  "
                        f"2-digit: {row['digit2']}  animal: {row['animal']}  "
                        f"dev: {row['dev_lottery']}"
                    )
                else:
                    print(f"[SKIP]  {row['date']} already exists.")
        conn.commit()
    return inserted


# ── Scheduled job ──────────────────────────────────────────────────────────────

def run_fetch_job(backfill: bool = False) -> dict:
    soup = fetch_page()
    if not soup:
        return {"status": "error", "message": "Failed to fetch page."}

    rows: list[dict] = []
    latest = fetch_latest(soup)
    if latest:
        rows.append(latest)

    if backfill:
        rows.extend(_parse_archive(soup))

    if not rows:
        return {"status": "error", "message": "No data found on page."}

    seen: dict[str, dict] = {}
    for r in rows:
        seen[r["date"]] = r

    try:
        inserted = save(list(seen.values()))
    except Exception as e:
        return {"status": "error", "message": str(e)}

    return {"status": "ok", "inserted": inserted, "total": len(seen)}


def scheduled_job() -> None:
    print(f"[SCHEDULER] Running fetch job at {datetime.now().isoformat()}")
    result = run_fetch_job()
    print(f"[SCHEDULER] Done: {result}")


# ── FastAPI app ────────────────────────────────────────────────────────────────

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(scheduled_job, CronTrigger(hour=0, minute=0), id="midnight_fetch", replace_existing=True)
    scheduler.start()
    print("[APP] Scheduler started — job fires every midnight.")
    yield
    scheduler.shutdown()
    print("[APP] Scheduler stopped.")


app = FastAPI(title="Lao Lottery Fetcher", lifespan=lifespan)


@app.get("/health")
def health():
    job = scheduler.get_job("midnight_fetch")
    next_run = job.next_run_time.isoformat() if job and job.next_run_time else None
    return {"status": "ok", "next_run": next_run}


@app.post("/run")
def trigger_run(backfill: bool = False):
    """Manually trigger a fetch. Pass ?backfill=true to also import archive."""
    result = run_fetch_job(backfill=backfill)
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    return result


@app.get("/results")
def get_results(limit: int = 20):
    """Return latest lottery results from the database."""
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM lao_lottery ORDER BY date DESC LIMIT %s", (limit,)
                )
                rows = cur.fetchall()
        return JSONResponse([{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()} for r in rows])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
