#!/usr/bin/env python3
"""
Lao Lottery Prediction Script — gen_predict.py
Reads lao_lottery.csv, runs statistical analysis, then calls Ollama to generate
a TTS-ready Thai narration (3-5 mins) and optionally produces an MP3 via Gemini TTS.

Usage:
  python gen_predict.py                        # generate script (print only)
  python gen_predict.py --tts                  # generate script + produce MP3
  python gen_predict.py --tts --voice Kore     # use a different female voice
  python gen_predict.py --model qwen2.5:7b     # specify Ollama model
  python gen_predict.py --list-models          # show available Ollama models
  python gen_predict.py --no-llm               # stats only, no Ollama

Female voices (Gemini TTS):  Aoede  Kore  Zephyr  Leda  Callirrhoe  Autonoe
Default voice: Aoede

Recommended Ollama model (best Thai support):
  ollama pull qwen2.5:7b
"""

import io
import os
import re
import sys
from collections import Counter
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

# Force UTF-8 output on Windows terminals
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

GEMINI_MODEL  = "gemini-2.5-flash-lite"
DEFAULT_VOICE = "Aoede"   # bright female — best for Thai narration

# All confirmed female voices in Gemini TTS
FEMALE_VOICES = {"Aoede", "Kore", "Zephyr", "Leda", "Callirrhoe", "Autonoe"}

THAI_MONTHS_REV = {
    1: "มกราคม", 2: "กุมภาพันธ์", 3: "มีนาคม",   4: "เมษายน",
    5: "พฤษภาคม", 6: "มิถุนายน",  7: "กรกฎาคม",  8: "สิงหาคม",
    9: "กันยายน", 10: "ตุลาคม",   11: "พฤศจิกายน", 12: "ธันวาคม",
}


# ── Data Loading ───────────────────────────────────────────────────────────────

def load_data() -> list[dict]:
    dsn = os.environ.get("LOTTO_DB_URL")
    if not dsn:
        raise RuntimeError("LOTTO_DB_URL environment variable not set.")
    with psycopg2.connect(dsn) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM lao_lottery ORDER BY date ASC")
            rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        raise RuntimeError("No data in lao_lottery table. Run fetch_lotto.py first.")
    # Normalize date to string
    for r in rows:
        if hasattr(r["date"], "strftime"):
            r["date"] = r["date"].strftime("%Y-%m-%d")
    return rows


def iso_to_thai(iso_date: str) -> str:
    """Convert YYYY-MM-DD to Thai Buddhist calendar string."""
    try:
        y, m, d = iso_date.split("-")
        month_name = THAI_MONTHS_REV.get(int(m), m)
        return f"{int(d)} {month_name} {int(y) + 543}"
    except Exception:
        return iso_date


# ── Statistical Analysis ───────────────────────────────────────────────────────

def digit_position_freq(numbers: list[str], length: int) -> list[Counter]:
    """Return per-position digit frequency counters for a list of fixed-length strings."""
    counters = [Counter() for _ in range(length)]
    for num in numbers:
        if len(num) == length:
            for i, ch in enumerate(num):
                counters[i][ch] += 1
    return counters


def gap_since_last(values: list[str]) -> dict[str, int]:
    """
    How many draws have passed since each value last appeared.
    Values not seen recently get a large gap.
    """
    seen: dict[str, int] = {}
    for i, v in enumerate(values):
        if v:
            seen[v] = i
    total = len(values)
    return {v: total - 1 - pos for v, pos in seen.items()}


def sum_digits(num: str) -> int:
    return sum(int(c) for c in num if c.isdigit())


def parity_label(num: str) -> str:
    try:
        return "คู่" if int(num) % 2 == 0 else "คี่"
    except ValueError:
        return "?"


def build_analysis(rows: list[dict]) -> dict:
    """Build a structured statistics dict from all historical rows."""
    d2 = [r["digit2"] for r in rows if r["digit2"]]
    d3 = [r["digit3"] for r in rows if r["digit3"]]
    d4 = [r["digit4"] for r in rows if r["digit4"]]

    # Frequency
    freq2 = Counter(d2)
    freq3 = Counter(d3)
    freq4 = Counter(d4)

    # Per-position digit frequency
    pos2 = digit_position_freq(d2, 2)
    pos4 = digit_position_freq(d4, 4)

    # Gap since last appearance
    gap2 = gap_since_last(d2)
    gap4 = gap_since_last(d4)

    # Sum trend (last 5)
    recent5 = rows[-5:]
    sum_trend = [
        {"draw": r["date"], "digit2": r["digit2"], "sum": sum_digits(r["digit2"]),
         "parity": parity_label(r["digit2"])}
        for r in recent5 if r["digit2"]
    ]

    # Streak / consecutive patterns in last digits
    last_digits = [r["digit2"][-1] for r in rows if r["digit2"]]
    last_digit_freq = Counter(last_digits)

    # High/low split for 2-digit (00-49 low, 50-99 high)
    lows  = sum(1 for v in d2 if v and int(v) < 50)
    highs = sum(1 for v in d2 if v and int(v) >= 50)

    # Dev lottery number frequency
    dev_all: list[str] = []
    for r in rows:
        if r["dev_lottery"]:
            dev_all.extend(r["dev_lottery"].split())
    dev_freq = Counter(dev_all)

    # Predicted candidates using composite score:
    # score = frequency + (1 / (gap+1)) normalized heuristic
    all_2digit = [f"{i:02d}" for i in range(100)]
    scored2: list[tuple[str, float]] = []
    for num in all_2digit:
        f = freq2.get(num, 0)
        g = gap2.get(num, len(d2))  # unseen = max gap
        # hot = seen often; cold recovery = not seen for long
        score = f * 2.0 + (g / max(len(d2), 1)) * 1.5
        scored2.append((num, round(score, 3)))
    scored2.sort(key=lambda x: -x[1])

    return {
        "total_draws": len(rows),
        "date_range": f"{rows[0]['date']} - {rows[-1]['date']}",
        "latest": rows[-1],
        "freq2":  freq2.most_common(10),
        "freq3":  freq3.most_common(8),
        "freq4":  freq4.most_common(5),
        "pos2_freq": [[c.most_common(5) for c in pos2]],
        "pos4_freq": [[c.most_common(5) for c in pos4]],
        "gap2":  sorted(gap2.items(), key=lambda x: -x[1])[:10],
        "sum_trend": sum_trend,
        "last_digit_freq": last_digit_freq.most_common(5),
        "high_low": {"high": highs, "low": lows},
        "dev_freq": dev_freq.most_common(10),
        "top_candidates_2": scored2[:10],
        "bottom_candidates_2": scored2[-5:],  # extreme cold
    }


def format_analysis_for_prompt(a: dict, rows: list[dict]) -> str:
    """Render the analysis dict as a readable Thai/English context block for the LLM."""
    lines = [
        f"=== ข้อมูลสถิติหวยลาว ({a['total_draws']} งวด) ===",
        f"ช่วงเวลา: {a['date_range'].replace(chr(8211), '-')}",
        "",
        "-- งวดล่าสุด --",
    ]
    lat = a["latest"]
    lines += [
        f"  วันที่ : {iso_to_thai(lat['date'])}",
        f"  4 ตัว : {lat['digit4']}",
        f"  3 ตัว : {lat['digit3']}",
        f"  2 ตัว : {lat['digit2']}",
        f"  สัตว์ : {lat['animal']}",
        f"  พัฒนา: {lat['dev_lottery']}",
        "",
        "-- ประวัติ 5 งวดย้อนหลัง (ใหม่→เก่า) --",
    ]
    for r in reversed(rows[-5:]):
        lines.append(
            f"  {iso_to_thai(r['date']):<28} | 4ตัว:{r['digit4']} | "
            f"3ตัว:{r['digit3']} | 2ตัว:{r['digit2']} | พัฒนา:{r['dev_lottery']}"
        )

    lines += [
        "",
        "-- ความถี่ 2 ตัว (ออกบ่อยสุด) --",
        "  " + "  ".join(f"{n}({c}ครั้ง)" for n, c in a["freq2"][:8]),
        "",
        "-- ความถี่ 4 ตัว --",
        "  " + "  ".join(f"{n}({c})" for n, c in a["freq4"]),
        "",
        "-- ตัวเลขที่ไม่ออกนานที่สุด (2 ตัว) --",
        "  " + "  ".join(f"{n}({g}งวด)" for n, g in a["gap2"][:8]),
        "",
        "-- แนวโน้มผลรวมเลข 2 ตัว (5 งวดล่าสุด) --",
    ]
    for s in a["sum_trend"]:
        lines.append(f"  {iso_to_thai(s['draw'])}: {s['digit2']} ผลรวม={s['sum']} ({s['parity']})")

    lines += [
        "",
        f"-- สัดส่วนสูง(50-99)/ต่ำ(00-49): {a['high_low']['high']}/{a['high_low']['low']} --",
        "",
        "-- ตัวเลขหลักสุดท้ายที่ออกบ่อย --",
        "  " + "  ".join(f"ลงท้าย{d}({c}ครั้ง)" for d, c in a["last_digit_freq"]),
        "",
        "-- ความถี่หวยพัฒนา (top 10) --",
        "  " + "  ".join(f"{n}({c})" for n, c in a["dev_freq"]),
        "",
        "-- ผู้ท้าชิง 2 ตัว (คะแนนสถิติสูง) --",
        "  " + "  ".join(f"{n}" for n, _ in a["top_candidates_2"][:8]),
        "",
        "-- ตัวเย็น (ไม่ออกนาน + ออกน้อย) --",
        "  " + "  ".join(f"{n}" for n, _ in a["bottom_candidates_2"]),
    ]
    return "\n".join(lines)


# ── Gemini Integration ─────────────────────────────────────────────────────────

def call_gemini(prompt: str, system: str) -> str:
    """Call Gemini 2.5 Flash Lite and return the generated text."""
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("[ERROR] GEMINI_API_KEY environment variable not set.", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    sys.stdout.write("\n[Generating script")
    sys.stdout.flush()

    parts = []
    for chunk in client.models.generate_content_stream(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.85,
            top_p=0.9,
            max_output_tokens=4096,
        ),
    ):
        token = chunk.text or ""
        parts.append(token)
        sys.stdout.write(".")
        sys.stdout.flush()

    sys.stdout.write("]\n\n")
    return "".join(parts)


# ── TTS Text Cleaner ──────────────────────────────────────────────────────────

def clean_for_tts(text: str) -> str:
    """
    Strip everything that sounds wrong when read aloud:
    - Section headers like [INTRO], [STATS], [PREDICTION ...], [OUTRO]
    - Timing hints like (หมายเหตุ: ~30 วินาที) or (~45 seconds)
    - Markdown bold/italic (**text**, *text*, __text__)
    - Remaining bracket pairs [] and ()
    - ครับ/ค่ะ → ค่ะ  (enforce female speech)
    - Collapse extra blank lines to one
    """
    # Remove section headers in square brackets
    text = re.sub(r"\[.*?\]", "", text)
    # Remove timing annotations  (หมายเหตุ: ...) or (~xx วินาที) etc.
    text = re.sub(r"\(หมายเหตุ[^)]*\)", "", text)
    text = re.sub(r"\(~[^)]*\)", "", text)
    text = re.sub(r"\(\s*\d+\s*:\s*\d+\s*[-–]\s*\d+\s*:\s*\d+\s*\)", "", text)
    # Strip markdown emphasis
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", text)
    # Strip leftover bare brackets/parens that are now empty
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"\[\s*\]", "", text)
    # ครับ/ค่ะ → ค่ะ  (female speech)
    text = re.sub(r"ครับ\s*/\s*ค่ะ", "ค่ะ", text)
    text = re.sub(r"ค่ะ\s*/\s*ครับ", "ค่ะ", text)
    text = re.sub(r"\bครับ\b", "ค่ะ", text)
    # Collapse 3+ blank lines → 1 blank line
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ── Script Generator ───────────────────────────────────────────────────────────

def build_prompt(stats_context: str, next_draw_date_hint: str) -> tuple[str, str]:
    system = (
        "You are a Thai female YouTube host for Lao lottery analysis. "
        "YOU MUST WRITE EVERYTHING IN THAI LANGUAGE ONLY. "
        "NEVER use Chinese, English, or any other language. "
        "NEVER switch language mid-response. If you start writing Thai, finish in Thai.\n\n"
        "คุณคือพิธีกรหญิงช่อง YouTube หวยลาว พูดภาษาไทยเท่านั้น ใช้คำลงท้าย ค่ะ นะคะ เท่านั้น\n\n"
        "ห้ามเด็ดขาด:\n"
        "- ห้ามใช้ภาษาจีน ภาษาอังกฤษ หรือภาษาอื่นใดทั้งสิ้น\n"
        "- ห้ามใส่ [หัวข้อ], (เวลา), **ตัวหนา**, หรือสัญลักษณ์ Markdown ใดๆ\n"
        "- ห้ามใช้คำว่า ครับ — ใช้ ค่ะ / นะคะ เท่านั้น\n"
        "- ห้ามบอกว่าทำนายไม่ได้ ต้องระบุตัวเลขเสมอ\n\n"
        "กฎที่ต้องทำ:\n"
        "1. เขียนเป็นข้อความพูดล้วนๆ ภาษาไทยตลอด\n"
        "2. อ่านตัวเลขออกเป็นคำพูด เช่น 79 → เจ็ดเก้า, 5079 → ห้าศูนย์เจ็ดเก้า\n"
        "3. ใช้สถิติเป็นเหตุผลสนับสนุนเสมอ\n"
        "4. ปิดท้ายด้วยประโยคกันชนว่าเป็นการวิเคราะห์เพื่อความบันเทิงเท่านั้น\n"
        "5. เนื้อหาไหลลื่น ฟังเป็นธรรมชาติ เหมือนคนพูดจริงๆ"
    )

    prompt = (
        "สำคัญมาก: เขียนเป็นภาษาไทยเท่านั้น ห้ามใช้ภาษาจีนหรือภาษาอื่นแม้แต่คำเดียว\n\n"
        f"ข้อมูลสถิติหวยลาว:\n\n{stats_context}\n\n"
        f"งวดถัดไปที่คาดว่าจะออก: {next_draw_date_hint}\n\n"
        "เขียนสคริปต์พูดภาษาไทยสำหรับ TTS ความยาว 3-5 นาที ประกอบด้วย:\n\n"
        "ส่วนที่ 1 — เปิดตัว: ทักทายผู้ชม บอกชื่อช่องและงวดที่จะวิเคราะห์ ดึงดูดให้ฟังต่อ\n"
        "ส่วนที่ 2 — ทบทวนงวดที่แล้ว: สรุปผลออกงวดล่าสุด บอกว่าเลขไหนมา เลขไหนพลาด\n"
        "ส่วนที่ 3 — วิเคราะห์สถิติ: อธิบาย pattern อย่างน้อย 3 ประเด็น "
        "(ตัวร้อน ตัวเย็น แนวโน้มผลรวม สัดส่วนคู่/คี่ ลำดับตัวเลข)\n"
        "ส่วนที่ 4 — เลขเด็ด 2 ตัว: ประกาศ 3 ชุด อ่านแต่ละตัวออกเสียง พร้อมเหตุผลสั้นๆ\n"
        "ส่วนที่ 5 — เลขเด็ด 4 ตัว: ประกาศ 2 ชุด อ่านออกเสียงทีละหลัก พร้อมเหตุผล\n"
        "ส่วนที่ 6 — หวยพัฒนา: ประกาศ 5 เลข อ่านออกเสียง พร้อมเหตุผล\n"
        "ส่วนที่ 7 — ปิดท้าย: ขอบคุณ กระตุ้น like/subscribe/กดกระดิ่ง "
        "และประโยคกันชนทางกฎหมาย\n\n"
        "สำคัญ:\n"
        "- ข้อความทั้งหมดต้องเป็นประโยคพูดล้วนๆ ไม่มีสัญลักษณ์ใดๆ\n"
        "- ห้ามใช้คำว่า ครับ โดยเด็ดขาด — ใช้เฉพาะ ค่ะ คะ หรือ นะคะ เท่านั้น\n"
        "- คั่นระหว่างแต่ละส่วนด้วยบรรทัดว่าง 1 บรรทัด (blank line) เพื่อให้แบ่งฉากได้"
    )
    return system, prompt


def estimate_next_draw(target_weekday: int | None = None) -> str:
    """
    Return the next draw date as a Thai string.
    target_weekday: 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri (Python weekday).
    If None, picks the nearest next Mon–Fri from today.
    """
    from datetime import date, timedelta
    _draw_days = {0, 1, 2, 3, 4}
    today = date.today()
    for offset in range(1, 8):
        candidate = today + timedelta(days=offset)
        if target_weekday is not None:
            if candidate.weekday() == target_weekday:
                break
        else:
            if candidate.weekday() in _draw_days:
                break
    month_th = THAI_MONTHS_REV.get(candidate.month, str(candidate.month))
    return f"{candidate.day} {month_th} {candidate.year + 543}"


# ── TTS Integration ────────────────────────────────────────────────────────────

def run_tts(text_file: Path, voice: str) -> Path:
    """
    Call generate_tts.py (Gemini) with the given text file and voice.
    Output MP3 lands next to the text file as <stem>.mp3.
    """
    import subprocess

    out_dir    = text_file.parent
    tts_script = Path(__file__).parent / "generate_tts.py"
    python     = Path(sys.executable)

    print(f"\n[TTS] Voice: {voice}  |  File: {text_file.name}")
    result = subprocess.run(
        [str(python), str(tts_script), str(text_file), str(out_dir), voice],
        capture_output=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"generate_tts.py exited with code {result.returncode}")

    mp3_path = out_dir / "narration.mp3"
    final_mp3 = text_file.with_suffix(".mp3")
    if mp3_path.exists():
        mp3_path.rename(final_mp3)
    return final_mp3


# ── Callable entry point ───────────────────────────────────────────────────────

def run_predict(
    tts: bool = True,
    voice: str = DEFAULT_VOICE,
    day: int | None = None,
    output: str | None = None,
) -> dict:
    """
    Run the full prediction pipeline programmatically (no sys.exit).
    Returns {"txt": str, "mp3": str | None, "preview": str}.
    Raises RuntimeError on failure.
    """
    from datetime import datetime as dt

    rows      = load_data()
    analysis  = build_analysis(rows)
    stats_ctx = format_analysis_for_prompt(analysis, rows)
    next_date = estimate_next_draw(day)

    system, prompt = build_prompt(stats_ctx, next_date)
    raw_script = call_gemini(prompt, system)
    if not raw_script:
        raise RuntimeError("Empty response from Gemini.")

    clean_script = clean_for_tts(raw_script)

    stamp    = dt.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(output) if output else Path(__file__).parent / f"lottery_tts_{stamp}.txt"
    out_path.write_text(clean_script, encoding="utf-8")
    print(f"\n[SAVED] TTS script → {out_path}")

    mp3_path: Path | None = None
    if tts:
        mp3_path = run_tts(out_path, voice)
        print(f"[DONE]  MP3 audio  → {mp3_path}")

    return {
        "txt":     str(out_path),
        "mp3":     str(mp3_path) if mp3_path else None,
        "preview": clean_script[:300],
    }


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Lao Lottery TTS Script Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--no-llm",      action="store_true",   help="Print stats only, skip LLM generation")
    parser.add_argument("--output",      default=None,          help="Save script text to this file")
    parser.add_argument("--tts",         action="store_true",   help="Generate MP3 via Gemini TTS after scripting")
    parser.add_argument(
        "--voice", default=DEFAULT_VOICE,
        help=f"Gemini TTS female voice (default: {DEFAULT_VOICE}). "
             f"Options: {', '.join(sorted(FEMALE_VOICES))}",
    )
    parser.add_argument(
        "--day", type=int, default=None, metavar="0-4",
        help="Target draw weekday: 0=Mon 1=Tue 2=Wed 3=Thu 4=Fri (default: next available)",
    )
    args = parser.parse_args()

    # Validate voice
    if args.voice not in FEMALE_VOICES:
        print(
            f"[WARN] '{args.voice}' is not in the confirmed female voices list.\n"
            f"  Female voices: {', '.join(sorted(FEMALE_VOICES))}\n"
            f"  Proceeding anyway — check Gemini docs if audio sounds wrong.",
            file=sys.stderr,
        )

    # ── Load & analyse ──────────────────────────────────────────────────────────
    rows = load_data()
    print(f"[INFO] Loaded {len(rows)} draws from PostgreSQL (lao_lottery)")

    if args.day is not None and not (0 <= args.day <= 4):
        print("[ERROR] --day must be 0–4 (0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri)", file=sys.stderr)
        sys.exit(1)

    analysis  = build_analysis(rows)
    stats_ctx = format_analysis_for_prompt(analysis, rows)
    next_date = estimate_next_draw(args.day)

    print("\n" + stats_ctx)

    if args.no_llm:
        return

    # ── Gemini ──────────────────────────────────────────────────────────────────
    print(f"\n[INFO] Using model  : {GEMINI_MODEL}")
    print(f"[INFO] Next draw est: {next_date}")
    if args.tts:
        print(f"[INFO] TTS voice    : {args.voice}")

    system, prompt = build_prompt(stats_ctx, next_date)
    raw_script = call_gemini(prompt, system)

    if not raw_script:
        print("[ERROR] Empty response from Ollama.", file=sys.stderr)
        sys.exit(1)

    # ── Clean for TTS ───────────────────────────────────────────────────────────
    clean_script = clean_for_tts(raw_script)

    print(clean_script)

    # ── Save text file ──────────────────────────────────────────────────────────
    from datetime import datetime as dt
    stamp = dt.now().strftime("%Y%m%d_%H%M%S")

    if args.output:
        out_path = Path(args.output)
    else:
        out_path = Path(__file__).parent / f"lottery_tts_{stamp}.txt"

    out_path.write_text(clean_script, encoding="utf-8")
    print(f"\n[SAVED] TTS script → {out_path}")

    # ── TTS ─────────────────────────────────────────────────────────────────────
    if args.tts:
        mp3_path = run_tts(out_path, args.voice)
        print(f"[DONE]  MP3 audio  → {mp3_path}")


if __name__ == "__main__":
    main()
