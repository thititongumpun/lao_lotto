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
- `news_post.py --kind hot` — /api/hot story → Gemini rewrite → **photo poster** (`build_poster`: 1minhotspot article page → source link → og:image; one Gemini vision call picks `portrait`/`scene`, a cartoon `fx` (rain/storm/fire/money/party/justice/police/sport/alert, or none + 1-3 story `emoji` stickers and a backdrop `tone`), a stamp `tag` + 3 headline lines; `poster.py` renders 1080² — portrait = rembg cutout over a flux-2-klein-4b background, scene = tilted photo print with the people (rembg) popping out above it over an fx-toned backdrop (sensitive stories: plain graded photo, no fx); source photo with baked-in headline → no poster) → `/photos` post, or the old text post if any poster step fails. `--dry-run` leaves `/tmp/poster_<id>.jpg`.
- `horoscope.py` — Sanook horoscope RSS → 7 per-birth-day articles for today (pubDate = yesterday 17:01 UTC) → `parse_article()` → one Gemini rewrite (`gen_predict.call_gemini`) → `facebook.post_text_to_facebook()` text-only page post. Cron 00:30 daily.
- `news_post.py --kind pm25` — Air4Thai `getNewAQI_JSON.php` (TLS verify off: the site's cert chain is broken) → worst PM2.5 station per province (กรุงเทพ เชียงใหม่ ขอนแก่น ภูเก็ต) → template text post, no LLM. Cron 07:05 daily.
- `news_post.py --kind gold` — `classic.goldtraders.or.th` HTML (`lblBLSell/lblBLBuy/lblOMSell/lblOMBuy/lblAsTime` spans) → template text post; skips if the latest announcement is not today, dedupes on date+announcement number. Cron 09:30 and 15:00 daily.
- Both lanes then post a second comment from NocoDB table `affiliate` (`where tag = <kind> and active`, least-recently-used row, `title` is the whole comment incl. Shopee link); no matching row or NocoDB unset → no affiliate comment.

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | App status + next scheduled run time |
| POST | `/run?backfill=false` | Manual fetch trigger |
| GET | `/results?limit=20` | Query saved rows from DB |
| GET | `/content/health` | Last content pipeline + horoscope results |
| POST | `/content/pipeline/run` | Full lottery video pipeline |
| POST | `/content/horoscope/run?date=&dry_run=false` | Horoscope text post (dry_run skips Facebook) |
| POST | `/content/news/pm25?dry_run=false` | PM2.5 text post (Air4Thai) |
| POST | `/content/news/gold?dry_run=false` | Gold price text post (goldtraders) |

All `/content/*` routes use HTTP Basic `admin`/`admin`; other paths 404 via `block_scanners` middleware.

## Environment Variables

| Variable | Description |
|---|---|
| `LOTTO_DB_URL` | PostgreSQL DSN — required |
| `TZ` | Timezone for scheduler — set to `Asia/Bangkok` in compose |
| `NEWS_POSTS_ENABLED` | `1` registers the news text-post jobs (hot/digest/lotto). Unset = jobs off; the `/content/news/*` endpoints still work for dry runs |
| `GEMINI_API_KEY` | Gemini (narration, TTS, horoscope rewrite) |
| `CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_API_TOKEN` | Workers AI (metadata, images) |
| `FACEBOOK_ACCESS_TOKEN` | Page token for Reels + text posts (via `.env`) |
| `NOCODB_BASE_URL` / `NOCODB_API_TOKEN` / `NOCODB_TABLE_NAME` | NocoDB `affiliate` table (v2 API, table id) for the affiliate comment on pm25/gold posts. Unset = no affiliate comment |
