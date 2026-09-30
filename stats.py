"""
Page post stats — stats.py

Daily snapshot of every Page post (last 7 days) into fb_post_stats, plus the Page's
daily Content Monetization earnings into fb_page_earnings, so /content/stats can
show which lane (fb_text_posts.kind) / media type / hour earns views and engagement.
Read-only on Facebook.

    uv run python -m stats [--days 7]

Last stdout line is RESULT_SENTINEL + json; everything else is logs.
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

import requests

from facebook import GRAPH_BASE, PAGE_ID, VERSION_MANAGE, _get_access_token
from news_post import _conn
from pipeline import RESULT_SENTINEL

POST_FIELDS = ("id,created_time,attachments{media_type},shares,"
               "reactions.summary(total_count).limit(0),comments.summary(total_count).limit(0),"
               "insights.metric(post_media_view)")


def ensure_tables() -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS fb_post_stats ("
            "post_id text primary key, media_type text, created_at timestamptz, "
            "views int, reactions int, comments int, shares int, updated_at timestamptz default now())"
        )
        cur.execute("CREATE TABLE IF NOT EXISTS fb_page_earnings (day date primary key, usd numeric)")


def parse_post(p: dict) -> tuple:
    """Graph /posts item -> (post_id, media_type, created_at, views, reactions, comments, shares)."""
    att = (p.get("attachments") or {}).get("data") or [{}]
    views = None
    for m in (p.get("insights") or {}).get("data") or []:
        if m.get("name") == "post_media_view":
            views = m["values"][0]["value"]
    return (p["id"], att[0].get("media_type") or "text", p["created_time"], views,
            p["reactions"]["summary"]["total_count"], p["comments"]["summary"]["total_count"],
            (p.get("shares") or {}).get("count", 0))


def fetch_posts(days: int) -> list[dict]:
    since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    url = f"{GRAPH_BASE}/{VERSION_MANAGE}/{PAGE_ID}/posts"
    params = {"since": since, "limit": 50, "fields": POST_FIELDS, "access_token": _get_access_token()}
    posts = []
    while url:
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        d = r.json()
        posts += d.get("data") or []
        url, params = (d.get("paging") or {}).get("next"), None  # next URL already carries the params
    return posts


def fetch_earnings(days: int) -> list[tuple]:
    """[(day, usd)] from the Page insight; end_time 07:00 UTC = the Bangkok day that just ended."""
    r = requests.get(f"{GRAPH_BASE}/{VERSION_MANAGE}/{PAGE_ID}/insights", timeout=30, params={
        "metric": "monetization_approximate_earnings", "period": "day",
        "since": int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp()),
        "access_token": _get_access_token()})
    r.raise_for_status()
    return [((datetime.fromisoformat(v["end_time"]) - timedelta(days=1)).date(), v["value"])
            for m in r.json().get("data") or [] for v in m["values"]]


def run(days: int = 7) -> dict:
    try:
        ensure_tables()
        rows = [parse_post(p) for p in fetch_posts(days)]
        earnings = fetch_earnings(days)
        with _conn() as conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO fb_post_stats (post_id, media_type, created_at, views, reactions, comments, shares) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (post_id) DO UPDATE SET "
                "views=EXCLUDED.views, reactions=EXCLUDED.reactions, comments=EXCLUDED.comments, "
                "shares=EXCLUDED.shares, updated_at=now()", rows)
            cur.executemany("INSERT INTO fb_page_earnings VALUES (%s,%s) "
                            "ON CONFLICT (day) DO UPDATE SET usd=EXCLUDED.usd", earnings)
        print(f"[STATS] {len(rows)} posts, {len(earnings)} earning days")
        return {"status": "ok", "posts": len(rows), "earning_days": len(earnings)}
    except Exception as exc:
        print(f"[STATS] error: {exc}")
        return {"status": "error", "error": str(exc)}


# kind: fb_text_posts labels our lanes (digest has 5 rows per post -> DISTINCT); the rest = reels/manual.
REPORT_SQL = """
WITH s AS (
  SELECT s.*, COALESCE(k.kind, s.media_type) AS kind,
         extract(hour FROM s.created_at AT TIME ZONE 'Asia/Bangkok')::int AS hour
  FROM fb_post_stats s
  LEFT JOIN (SELECT DISTINCT post_id, kind FROM fb_text_posts) k USING (post_id)
  WHERE s.created_at > now() - make_interval(days => %s)
    AND s.created_at < now() - interval '1 day'  -- still-growing posts skew the averages
)
SELECT {key}, count(*), round(avg(views)), round(avg(reactions), 1),
       round(avg(comments), 1), round(avg(shares), 1)
FROM s {where} GROUP BY {key} ORDER BY avg(views) DESC NULLS LAST
"""
COLS = ("posts", "avg_views", "avg_reactions", "avg_comments", "avg_shares")


def report(days: int = 30) -> dict:
    """{by_kind, by_hour (photo/text only), earnings} for /content/stats."""
    out = {}
    with _conn() as conn, conn.cursor() as cur:
        # by_hour: photo/text only — reels are ~75% of posts and would decide the hour ranking
        for name, key, where in (("by_kind", "kind, media_type", ""),
                                 ("by_hour", "hour", "WHERE media_type <> 'video'")):
            cur.execute(REPORT_SQL.format(key=key, where=where), (days,))
            names = key.replace(" ", "").split(",")
            out[name] = [{**dict(zip(names, r)),
                          **{c: (v if v is None or isinstance(v, int) else float(v)) for c, v in zip(COLS, r[len(names):])}}
                         for r in cur.fetchall()]
        cur.execute("SELECT day, usd FROM fb_page_earnings WHERE day > current_date - %s ORDER BY day", (days,))
        out["earnings"] = [{"day": d.isoformat(), "usd": float(u)} for d, u in cur.fetchall()]
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    res = run(ap.parse_args().days)
    res["ran_at"] = datetime.now().isoformat()
    sys.stdout.flush()
    print(RESULT_SENTINEL + json.dumps(res, ensure_ascii=False))
