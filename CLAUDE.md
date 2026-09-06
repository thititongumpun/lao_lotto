# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the app locally
uv run uvicorn main:app --reload

# Run fetch job manually (no server)
uv run python -c "from main import run_fetch_job; print(run_fetch_job())"

# Run with backfill
uv run python -c "from main import run_fetch_job; print(run_fetch_job(backfill=True))"

# Horoscope post: dry run (no Facebook), optional --date YYYY-MM-DD
uv run python -m horoscope --dry-run

# Offline self-checks
uv run python test_pipeline.py && uv run python test_horoscope.py

# Docker
docker compose up --build
docker compose up -d
```

## Architecture

Single-file FastAPI app (`main.py`) with three layers:

1. **Scraper** — `fetch_page()` fetches `sanook.com/news/laolotto/`, `fetch_latest()` parses the current draw, `_parse_archive()` parses historical draws from the same page.

2. **Database** — psycopg2 connects via `LOTTO_DB_URL`. Table `lao_lottery` is created on first save. `save()` upserts with `ON CONFLICT (date) DO NOTHING`.

3. **Scheduler** — `AsyncIOScheduler` (APScheduler) starts in the FastAPI lifespan and fires `scheduled_job()` via `CronTrigger(hour=22, minute=0)` daily `Asia/Bangkok` time (set via `TZ` env var). `content_router.register_jobs()` adds two more cron jobs.

**Content subprocesses** (`content_router._run_subprocess` spawns `python -m <module>`, reads the last `RESULT_SENTINEL` JSON line):

- `pipeline.py` — lottery predict → metadata → video → Facebook Reels → YouTube. Cron 22:30 mon-fri.
- `horoscope.py` — Sanook horoscope RSS → 7 per-birth-day articles for today (pubDate = yesterday 17:01 UTC) → `parse_article()` → one Gemini rewrite (`gen_predict.call_gemini`) → `facebook.post_text_to_facebook()` text-only page post. Cron 00:30 daily.

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | App status + next scheduled run time |
| POST | `/run?backfill=false` | Manual fetch trigger |
| GET | `/results?limit=20` | Query saved rows from DB |
| GET | `/content/health` | Last content pipeline + horoscope results |
| POST | `/content/pipeline/run` | Full lottery video pipeline |
| POST | `/content/horoscope/run?date=&dry_run=false` | Horoscope text post (dry_run skips Facebook) |

All `/content/*` routes use HTTP Basic `admin`/`admin`; other paths 404 via `block_scanners` middleware.

## Environment Variables

| Variable | Description |
|---|---|
| `LOTTO_DB_URL` | PostgreSQL DSN — required |
| `TZ` | Timezone for scheduler — set to `Asia/Bangkok` in compose |
| `GEMINI_API_KEY` | Gemini (narration, TTS, horoscope rewrite) |
| `CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_API_TOKEN` | Workers AI (metadata, images) |
| `FACEBOOK_ACCESS_TOKEN` | Page token for Reels + text posts (via `.env`) |
