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

# Docker
docker compose up --build
docker compose up -d
```

## Architecture

Single-file FastAPI app (`main.py`) with three layers:

1. **Scraper** — `fetch_page()` fetches `sanook.com/news/laolotto/`, `fetch_latest()` parses the current draw, `_parse_archive()` parses historical draws from the same page.

2. **Database** — psycopg2 connects via `LOTTO_DB_URL`. Table `lao_lottery` is created on first save. `save()` upserts with `ON CONFLICT (date) DO NOTHING`.

3. **Scheduler** — `AsyncIOScheduler` (APScheduler) starts in the FastAPI lifespan and fires `scheduled_job()` via `CronTrigger(hour=0, minute=0)` every midnight `Asia/Bangkok` time (set via `TZ` env var).

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | App status + next scheduled run time |
| POST | `/run?backfill=false` | Manual fetch trigger |
| GET | `/results?limit=20` | Query saved rows from DB |

## Environment Variables

| Variable | Description |
|---|---|
| `LOTTO_DB_URL` | PostgreSQL DSN — required |
| `TZ` | Timezone for scheduler — set to `Asia/Bangkok` in compose |
