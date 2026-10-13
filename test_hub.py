import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import hub


def test_end_maps_status_and_trims_fields(monkeypatch):
    sent = []
    monkeypatch.setattr(hub, "emit", lambda *a: sent.append(a))
    hub.end("news_post:hot", "r1", {"status": "error", "error": "quota", "script": "x" * 500, "items": [1, 2]})
    hub.end("traffic", "r2", {"status": "skipped"})
    job, run, stage, status, f = sent[0]
    assert (job, run, stage, status) == ("news_post:hot", "r1", "job", "fail")
    assert f["reason"] == "quota" and len(f["script"]) == 200 and "items" not in f
    assert sent[1][3] == "skip"


def test_emit_posts_to_hub(monkeypatch):
    got: queue.Queue = queue.Queue()

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            got.put((self.headers["X-Hub-Key"], json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(202)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("HUB_URL", f"http://127.0.0.1:{srv.server_port}/ingest")
    monkeypatch.setenv("HUB_KEY", "k")
    monkeypatch.setattr(hub, "_started", False)
    hub.emit("pipeline", "r1", "job", "start")
    key, ev = got.get(timeout=3)
    srv.shutdown()
    assert key == "k"
    assert ev["app"] == "laolotto" and ev["job"] == "pipeline" and ev["status"] == "start"


def test_emit_noop_without_url(monkeypatch):
    monkeypatch.delenv("HUB_URL", raising=False)
    hub.emit("pipeline", "r1", "job", "start")  # must not raise or block


def test_earnings_summary_feeds_hub_end(monkeypatch):
    from datetime import date
    import stats
    rows = [(date(2026, 10, d), v) for d, v in [(3, 1), (12, 2.5), (5, 1), (6, 1), (7, 1), (8, 1), (9, 1), (10, 1)]]
    out = stats.earnings_summary(rows)
    assert out == {"usd_day": "2026-10-12", "usd": 2.5, "usd_7d": 8.5}  # oldest (Oct 3) dropped
    assert stats.earnings_summary([]) == {}
    sent = []
    monkeypatch.setattr(hub, "emit", lambda *a: sent.append(a))
    hub.end("stats", "r", {"status": "ok", **out})
    assert sent[0][4]["usd"] == 2.5
