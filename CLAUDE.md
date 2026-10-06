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

# Traffic post: dry run (no Facebook)
uv run python -m traffic --dry-run

# Offline self-checks
uv run python test_pipeline.py && uv run python test_horoscope.py && uv run python test_traffic.py

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
- `news_post.py --kind hone` — text-only, daily 17:40 + 20:10 fallback: honekrasae.com `/group?groups=โหนกระแส,ข่าวกำลังโหน` (Next.js SSR; items parsed out of the `self.__next_f.push` RSC chunks by `parse_hone`) → `pick_hone`: published today 12:00–20:59 Bangkok and the body reads like the show (`HONE_MARKERS`; group tags are inconsistent), longest wins → one Gemini rewrite (`build_hone_prompt`: เกิดอะไรขึ้น / ฝ่ายผู้ร้อง / อีกฝ่ายชี้แจง / ต่อจากนี้ + question, claims kept as อ้างว่า) through `validate_text` → text post, source link in comment, dedupe on content id.
- `stats.py` — read-only: every Page post of the last 7 days (`/posts` + `insights.metric(post_media_view)`) → `fb_post_stats` upsert, Page `monetization_approximate_earnings` → `fb_page_earnings`. Lane label = `fb_text_posts.kind` (horoscope marks its post there too), else media type. Cron 03:10 daily, not behind `NEWS_POSTS_ENABLED`.
- `traffic.py` — TomTom flowSegmentData at 28 fixed points on 14 roads (road comes from the point config; API returns no road name); drops FRC4+, confidence<0.5, closures; median per road; heavy <0.40 / slow <0.60 / moderate <0.80, but a road is heavy only if ≥2 of its segments are <0.40 (else capped at slow); 4:5 card (`poster.card` over stock `assets/traffic_heavy.jpg`/`traffic_slow.jpg`, picked by the worst road; generated once with flux-2-klein-4b, no per-post AI) as a `/photos` post, card failure → text post; posts only when the heavy/slow signature changes and ≥`TRAFFIC_MIN_GAP_MIN` since the last post, same signature re-posted after 180 min; post row is marked before the Facebook call so a timeout cannot double-post; daily TomTom call count in `traffic_api_usage`, run skipped over `TRAFFIC_DAILY_CAP`. Cron `*/15 6-21` (06:00–21:45) behind `NEWS_POSTS_ENABLED`. `--dry-run` prints per-point frc/snapped coords.
- pm25 / gold / lotto / digest post a 4:5 number card (`news_post.build_card` → `poster.card`) as a `/photos` post with the text as caption; card render failure → plain text post. `--dry-run` leaves `/tmp/card_<kind>.jpg`.
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
| POST | `/content/news/hone?dry_run=false` | Daily โหนกระแส episode summary text post (honekrasae.com) |
| POST | `/content/traffic/run?dry_run=false` | Bangkok traffic text post (TomTom) |
| GET | `/content/traffic/usage?days=7` | Daily TomTom request counts |
| GET | `/content/stats?days=30` | Avg views/reactions/comments/shares by lane and posting hour (posts older than 1 day) + daily Page earnings |

All `/content/*` routes use HTTP Basic `admin`/`admin`; other paths 404 via `block_scanners` middleware.

## Environment Variables

| Variable | Description |
|---|---|
| `LOTTO_DB_URL` | PostgreSQL DSN — required |
| `TZ` | Timezone for scheduler — set to `Asia/Bangkok` in compose |
| `NEWS_POSTS_ENABLED` | `1` registers the news text-post jobs (hot/digest/lotto). Unset = jobs off; the `/content/news/*` endpoints still work for dry runs |
| `TOMTOM_API_KEY` | TomTom traffic API key (traffic lane) |
| `TRAFFIC_INTERVAL_MIN` / `TRAFFIC_HOURS` / `TRAFFIC_MIN_GAP_MIN` / `TRAFFIC_DAILY_CAP` | Traffic lane tuning; defaults 15, `6-21`, 60, 2300 (max 7 roads listed, fixed) |
| `GEMINI_API_KEY` | Gemini (narration, TTS, horoscope rewrite) |
| `HONE_GEMINI_MODEL` | Gemini model for the `hone` lane only (via `.env`); unset = `gen_predict.GEMINI_MODEL`. Output price includes thinking tokens |
| `CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_API_TOKEN` | Workers AI (metadata, images) |
| `FACEBOOK_PAGE_ID` | Facebook Page to post to; default `598514650638901` |
| `FACEBOOK_ACCESS_TOKEN` | Page token for Reels + text posts (via `.env`) |
| `NOCODB_BASE_URL` / `NOCODB_API_TOKEN` / `NOCODB_TABLE_NAME` | NocoDB `affiliate` table (v2 API, table id) for the affiliate comment on pm25/gold posts. Unset = no affiliate comment |
