"""Self-check for news_post.py: digest formatting and the hot-message validator,
both offline. Run: uv run python test_news_post.py"""

from datetime import date

from PIL import Image

import poster
from news_post import (OG_IMAGE_RE, SOURCE_RE, affiliate_comment, build_digest_message, build_gold_message,
                       build_lotto_message, build_pm25_message, crop_cover, parse_gold, parse_hone, pick_flash, pick_hone,
                       pick_pm25, reel_cover, unescape_title, validate_hot, validate_poster_plan,
                       validate_text)

STORIES = [{"id": str(i), "title": f"ข่าวที่ {i}", "body": "x", "category": "viral"} for i in range(1, 8)]


def test_digest():
    msg = build_digest_message(STORIES, "สรุปข่าวเช้า")
    assert "http" not in msg, msg
    numbered = [l for l in msg.splitlines() if l[:1] in "12345" and "️⃣" in l]
    assert len(numbered) == 5, msg
    assert msg.startswith("📰 สรุปข่าวเช้า วัน") and msg.endswith("#สรุปข่าว #ข่าววันนี้"), msg


def test_validate_hot():
    ok = "🔥 หัวข้อ\n" + "บรรทัดข่าวยาวพอสมควรสำหรับผู้อ่าน " * 6 + "\n❓ คิดเห็นอย่างไร\n#ข่าววันนี้ #ไวรัล"
    assert validate_hot("**" + ok + "**") == ok
    for bad in (ok + "\nอ่านต่อ https://x.com", "สั้นไป"):
        try:
            validate_hot(bad)
        except RuntimeError:
            continue
        raise AssertionError(f"validator accepted: {bad!r}")


def test_validate_text():
    ok = "📌 ข้อเท็จจริง\n" + "สรุปข่าวยาวพอสำหรับผู้อ่าน " * 6 + "\n💡 แง่คิด: ระวังไว้\nคุณคิดว่าอย่างไร?\n#แง่คิดจากข่าว #ไวรัล"
    assert validate_text("**" + ok + "**", 150, 800) == ok
    for bad in (ok.replace("ระวังไว้", "อ่านต่อ https://x.com"),        # URL
                ok.replace("ระวังไว้", "ใครเห็นด้วยพิมพ์ 1"),            # comment bait
                ok.replace("ระวังไว้", "กดแชร์ให้เพื่อนรู้"),             # share bait
                ok.replace("คุณคิดว่าอย่างไร?", "จบข่าว"),             # no closing question
                "สั้นไป?"):
        try:
            validate_text(bad, 150, 800)
        except RuntimeError:
            continue
        raise AssertionError(f"validator accepted: {bad!r}")
    for q in ("คุณมองเรื่องนี้ยังไงบ้างคะ", "คุณเห็นด้วยไหม", "แบบไหนดีที่สุดครับ ?"):
        validate_text(ok.replace("คุณคิดว่าอย่างไร?", q), 150, 800)
    assert "แชร์ลูกโซ่" in validate_text(ok.replace("ระวังไว้", "ระวังแชร์ลูกโซ่"), 150, 800)  # news word, not bait


def test_garbled_text():
    ok = "เรื่องนี้ยาว " * 20 + "\nคุณคิดอย่างไร?"
    assert validate_text(ok, 50, 2000) == ok.strip()
    assert validate_text("ใช้ CIB และ สบส. " * 10 + "\nคุณคิดอย่างไร?", 50, 2000)  # spaced Latin is fine
    for bad in ("พาหนocกลับ", "ทำުރร้าย", "ขออนุญาطع่อสร้าง"):
        try:
            validate_text(ok.replace("ยาว", bad, 1), 50, 2000)
            raise AssertionError(bad)
        except RuntimeError as exc:
            assert "garbled" in str(exc), exc


def test_placeholder_leak():
    ok = "เรื่องนี้ยาว " * 20 + "\nคุณคิดอย่างไร?"
    try:
        validate_text("<ผู้ร้อง>ร้องเรียน " + ok, 50, 2000)
        raise AssertionError("placeholder passed")
    except RuntimeError as exc:
        assert "placeholder" in str(exc), exc


def test_parse_hone():
    import json
    detail = "<p>เรื่องราวของโหนกระแสวันนี้ &amp; แม่</p><p>หนุ่ม กรรชัย ถาม</p>"
    items = [{"id": "uq7fMrdLGZj3JmnsjVY8", "title": " แม่ร้องเรียน ", "public_date": 1791193740, "content_detail": "$1a"},
             {"id": "abcdefghijklmnopqrst", "title": "inline", "public_date": 1791193740, "content_detail": "<p>สั้น</p>"},
             {"id": "zzzzzzzzzzzzzzzzzzzz", "title": "card", "public_date": 1}]  # list card without content_detail
    stream = "0:" + json.dumps({"x": items}, ensure_ascii=False, separators=(",", ":")) + f"\n1a:T{len(detail.encode()):x},{detail}2:[]\n"
    chunks = [json.dumps(stream[:50], ensure_ascii=False)[1:-1], json.dumps(stream[50:], ensure_ascii=False)[1:-1]]
    html = "".join(f'<script>self.__next_f.push([1,"{c}"])</script>' for c in chunks)
    got = parse_hone(html)
    assert [g["id"] for g in got] == ["uq7fMrdLGZj3JmnsjVY8", "abcdefghijklmnopqrst"], got
    assert got[0]["title"] == "แม่ร้องเรียน" and got[0]["url"].endswith("/content/uq7fMrdLGZj3JmnsjVY8")
    assert got[0]["body"] == "เรื่องราวของโหนกระแสวันนี้ & แม่\nหนุ่ม กรรชัย ถาม", got[0]["body"]
    assert got[1]["body"] == "สั้น"


def test_pick_hone():
    day = date(2026, 10, 5)
    ep = {"id": "ep", "public_date": 1791193740, "body": "เรื่องราวของโหนกระแสวันนี้ " * 5}  # 10-05 16:49
    items = [
        {"id": "yesterday", "public_date": 1791193740 - 86400, "body": ep["body"] * 2},
        {"id": "morning", "public_date": 1791193740 - 8 * 3600, "body": ep["body"] * 2},  # 08:49
        {"id": "other", "public_date": 1791193740, "body": "น้ำท่วมอยุธยา " * 20},
        ep,
        {"id": "short", "public_date": 1791193740 + 60, "body": "ในรายการ"},
    ]
    assert pick_hone(items, day)["id"] == "ep"
    assert pick_hone(items[:3], day) is None  # no episode today -> skip


def test_pick_flash():
    from datetime import datetime
    from horoscope import BANGKOK
    now = datetime.fromtimestamp(1791286500 + 1800, BANGKOK)  # 30 min after the newest
    items = [
        {"id": "old", "public_date": 1791286500 - 4 * 3600, "body": "ข่าว"},
        {"id": "a", "public_date": 1791286440, "body": "ข่าว"},
        {"id": "b", "public_date": 1791286500, "body": "ข่าว"},
        {"id": "show", "public_date": 1791286500 + 60, "body": "เรื่องราวของโหนกระแสวันนี้"},  # hone lane's
    ]
    assert [it["id"] for it in pick_flash(items, now)] == ["b", "a"]


def test_flash_photo():
    import io, os, tempfile
    import news_post
    buf = io.BytesIO()
    Image.new("RGB", (1200, 630), "blue").save(buf, "PNG")
    resp = type("R", (), {"content": buf.getvalue(), "raise_for_status": lambda self: None})()
    real, news_post.requests.get = news_post.requests.get, lambda *a, **k: resp
    try:
        out = os.path.join(tempfile.mkdtemp(), "f.jpg")
        assert news_post.fetch_flash_photo({"image": "x"}, out) == out
        assert Image.open(out).size == (1200, news_post.HONE_BANNER_TOP)
        news_post.requests.get = lambda *a, **k: 1 / 0
        assert news_post.fetch_flash_photo({"image": "x"}, out) is None  # never raises
    finally:
        news_post.requests.get = real


def test_lotto():
    msg = build_lotto_message({"date": date(2026, 9, 14), "digit4": "1234", "digit3": "234",
                               "digit2": "34", "animal": "ปลา", "dev_lottery": "12 34"})
    assert "1234" in msg and "#หวยลาว #ผลหวยลาว" in msg and "http" not in msg, msg


def test_source_and_og():
    page = '<a href="https://www.khaosod.co.th/breaking-news/news_10411999">ต้นฉบับ</a>'
    m = SOURCE_RE.search(page)
    assert m and m.group(1) == "khaosod.co.th" and m.group(0).endswith("news_10411999"), m
    for html in ('<meta property="og:image" content="https://x/a.jpg"/>',
                 '<meta content="https://x/a.jpg" property="og:image">'):
        og = OG_IMAGE_RE.search(html)
        assert (og.group(1) or og.group(2)) == "https://x/a.jpg", html


def test_poster_plan():
    plan = validate_poster_plan({"layout": "weird", "bg_subject": " court ", "l1": "a", "l2": "b", "l3": "c"})
    assert plan["layout"] == "scene" and plan["has_text"] is False and plan["lines"] == ["a", "b", "c"], plan
    assert plan["bg_prompt"].startswith("court, ") and "no text" in plan["bg_prompt"], plan
    assert plan["sensitive"] is False, plan
    assert plan["fx"] == "none" and plan["tag"] == "ข่าวด่วน" and plan["people"] is False, plan  # missing -> defaults
    assert validate_poster_plan({"people": "yes", "l1": "a", "l2": "b", "l3": "c"})["people"] is False
    assert validate_poster_plan({"people": True, "l1": "a", "l2": "b", "l3": "c"})["people"] is True
    odd = validate_poster_plan({"fx": "lava", "tag": "ยาวมากเกินไปสำหรับตราประทับ", "l1": "a", "l2": "b", "l3": "c"})
    assert odd["fx"] == "none" and odd["tag"] == "ข่าวด่วน", odd
    ok = validate_poster_plan({"fx": "rain", "tag": " เตือนภัย! ", "l1": "a", "l2": "b", "l3": "c"})
    assert ok["fx"] == "rain" and ok["tag"] == "เตือนภัย!", ok
    em = validate_poster_plan({"fx": "none", "emoji": ["🐘", "abc", 5, "🛕", "📜", "⚖️"],
                               "tone": ["#E0FF40", "#001010"], "l1": "a", "l2": "b", "l3": "c"})
    assert em["emoji"] == ["🐘", "🛕", "📜"], em  # letters/non-strings dropped, max 3
    assert em["tone"] == ((0, 16, 16), (224, 255, 64)), em  # darker colour first
    assert validate_poster_plan({"tone": ["red", "#000000"], "l1": "a", "l2": "b", "l3": "c"})["tone"] is None
    assert validate_poster_plan({"tone": ["#000000", "#808080"], "l1": "a", "l2": "b", "l3": "c"})["tone"] is None
    assert validate_poster_plan({"has_text": "yes", "l1": "a", "l2": "b", "l3": "c"})["has_text"] is False
    assert validate_poster_plan({"sensitive": "yes", "l1": "a", "l2": "b", "l3": "c"})["sensitive"] is False
    assert validate_poster_plan({"sensitive": True, "l1": "a", "l2": "b", "l3": "c"})["sensitive"] is True
    assert validate_poster_plan({"sensitive": "true", "l1": "a", "l2": "b", "l3": "c"})["sensitive"] is True
    try:
        validate_poster_plan({"layout": "portrait", "l1": "a", "l2": "", "l3": "c"})
    except RuntimeError:
        return
    raise AssertionError("plan without l2 accepted")


def test_render_both_layouts():
    photo = Image.new("RGB", (1200, 675), (40, 90, 140))
    person = Image.new("RGBA", (400, 600), (230, 200, 180, 255))
    lines = ["ศาลฎีกาตัดสิทธิ์ 10 ปี", "“สัจจพงษ์”", "พ่อ สส.ภูมิใจไทย ปมฮั้ว สว. ข้อความยาวมากเกินบรรทัด"]
    for out, kw in (("/tmp/t_scene.jpg", {}), ("/tmp/t_portrait.jpg", {"person": person, "background": photo})):
        poster.render(photo, lines, "ภาพ: ข่าวสด", out, **kw)
        assert Image.open(out).size == (1080, 1080), out

    poster.render(photo, lines, "ภาพ: ข่าวสด", "/tmp/t_scene_muted.jpg", sensitive=True)
    assert Image.open("/tmp/t_scene_muted.jpg").size == (1080, 1080)
    normal = Image.open("/tmp/t_scene.jpg").tobytes()
    muted = Image.open("/tmp/t_scene_muted.jpg").tobytes()
    assert normal != muted, "sensitive style should differ from the normal scene poster"

    pop = Image.new("RGBA", photo.size)  # a fake person mask in the middle: pops out above the print
    pop.paste((255, 255, 255, 255), (450, 120, 750, 675))
    for fx in poster.FX_TONE:
        out = f"/tmp/t_scene_{fx}.jpg"
        poster.render(photo, lines, "ภาพ: ข่าวสด", out, pop=pop, fx=fx, tag="เตือนภัย!")
        assert Image.open(out).size == (1080, 1080), out
    poster.render(photo, lines, "ภาพ: ข่าวสด", "/tmp/t_scene_emoji.jpg", fx="none", emoji=["🐘", "⚖️"],
                  tone=((0, 20, 10), (60, 200, 120)))
    plain = Image.open("/tmp/t_scene_none.jpg").tobytes()
    assert Image.open("/tmp/t_scene_emoji.jpg").tobytes() != plain, "emoji/tone should change the poster"


def test_reel_cover():
    import io
    buf = io.BytesIO(); Image.new("RGB", (1080, 1920), "red").save(buf, "JPEG")
    assert crop_cover(buf.getvalue(), "/tmp/_cover_test.jpg") == "/tmp/_cover_test.jpg"
    assert Image.open("/tmp/_cover_test.jpg").size == (1080, 1350)
    small = io.BytesIO(); Image.new("RGB", (720, 1280)).save(small, "JPEG")  # Facebook may serve a smaller copy
    crop_cover(small.getvalue(), "/tmp/_cover_test.jpg"); assert Image.open("/tmp/_cover_test.jpg").size == (720, 900)
    wide = io.BytesIO(); Image.new("RGB", (1080, 1080)).save(wide, "JPEG")  # a square frame is not our cover
    assert crop_cover(wide.getvalue(), "/tmp/_cover_test.jpg") is None
    # poster file from n8n -> cropped photo post; no file -> None (old generated poster)
    import news_post, tempfile, os
    d = tempfile.mkdtemp(); news_post.COVER_DIR = d
    with open(os.path.join(d, "123.jpg"), "wb") as f: f.write(buf.getvalue())
    assert reel_cover({"id": "123"}, "/tmp/_rc.jpg") == "/tmp/_rc.jpg" and Image.open("/tmp/_rc.jpg").size == (1080, 1350)
    assert reel_cover({"id": "456"}, "/tmp/_rc.jpg") is None


def test_pm25():
    stations = [
        {"areaTH": "เขตธนบุรี, กรุงเทพฯ", "AQILast": {"date": "2026-09-28", "PM25": {"color_id": "1", "value": "7.9"}}},
        {"areaTH": "เขตดินแดง, กรุงเทพฯ", "AQILast": {"date": "2026-09-27", "PM25": {"color_id": "3", "value": "30"}}},
        {"areaTH": "เขตบางนา, กรุงเทพฯ", "AQILast": {"date": "2026-09-28", "PM25": {"color_id": "2", "value": "12.4"}}},
        {"areaTH": "อ.เมือง, เชียงใหม่", "AQILast": {"date": "2026-09-28", "PM25": {"color_id": "4", "value": "45.2"}}},
        {"areaTH": "อ.เมือง, ภูเก็ต", "AQILast": {"date": "2026-09-28", "PM25": {"color_id": "0", "value": "-1"}}},
        {"areaTH": "x"},
    ]
    r = pick_pm25(stations, "2026-09-28")
    assert r == [("กรุงเทพ", 12.4, "2"), ("เชียงใหม่", 45.2, "4")], r
    msg = build_pm25_message(r, date(2026, 9, 28))
    assert "กรุงเทพ 12 🟢 | เชียงใหม่ 45 🟠" in msg, msg
    assert "http" not in msg and len(msg.splitlines()) == 4 and msg.endswith("#ฝุ่น #PM25"), msg


def test_gold():
    html = (
        '<span id="DetailPlace_uc_goldprices1_lblAsTime">27/09/2569 เวลา 09:01 น. (ครั้งที่ 1)</span>'
        '<span id="DetailPlace_uc_goldprices1_lblBLSell"><b><font color="Black">67,850.00</font></b></span>'
        '<span id="DetailPlace_uc_goldprices1_lblBLBuy"><b><font color="Black">\n67,650.00</font></b></span>'
        '<span id="DetailPlace_uc_goldprices1_lblOMSell"><b><font color="Black">68,650.00</font></b></span>'
        '<span id="DetailPlace_uc_goldprices1_lblOMBuy"><b><font color="Black">66,294.68</font></b></span>'
    )
    g = parse_gold(html)
    assert g["date"] == date(2026, 9, 27) and g["round"] == 1 and g["time"] == "09:01", g
    assert g["bl_sell"] == "67,850" and g["om_buy"] == "66,294.68", g
    msg = build_gold_message(g)
    assert "67,850" in msg and "66,294.68" in msg and "http" not in msg and len(msg.splitlines()) == 5, msg
    for broken in (html.replace("lblOMBuy", "lblOMBuyX"),
                   html.replace("27/09/2569 เวลา 09:01 น. (ครั้งที่ 1)", "ไม่มีข้อมูล")):
        try:
            parse_gold(broken)
        except RuntimeError:
            continue
        raise AssertionError(f"parse_gold accepted broken html: {broken!r}")


def test_affiliate():
    raw = "ผงซักฟอก \\nพิกัดสินค้า 👇\\nhttps://s.shopee.co.th/AAEj "
    assert unescape_title(raw) == "ผงซักฟอก \nพิกัดสินค้า 👇\nhttps://s.shopee.co.th/AAEj"
    import news_post
    news_post.NOCODB_URL = ""
    assert affiliate_comment("pm25") is None


def test_stats_parse():
    from stats import parse_post
    p = {"id": "1_2", "created_time": "2026-09-30T15:00:04+0000",
         "attachments": {"data": [{"media_type": "photo"}]}, "shares": {"count": 3},
         "reactions": {"summary": {"total_count": 5}}, "comments": {"summary": {"total_count": 2}},
         "insights": {"data": [{"name": "post_media_view", "values": [{"value": 812}]}]}}
    assert parse_post(p) == ("1_2", "photo", "2026-09-30T15:00:04+0000", 812, 5, 2, 3)
    bare = {k: p[k] for k in ("id", "created_time", "reactions", "comments")}  # text post, no shares/insights
    assert parse_post(bare) == ("1_2", "text", "2026-09-30T15:00:04+0000", None, 5, 2, 0)


def test_cards():
    from news_post import build_card
    today = date(2026, 9, 30)
    gold = {"bl_sell": "67,850", "bl_buy": "67,650", "om_sell": "68,650", "om_buy": "66,315.40", "round": 3, "time": "09:38"}
    lotto = {"digit4": "1234", "digit3": "234", "digit2": "34", "animal": "ช้าง", "dev_lottery": "56"}
    for kind, data in (("gold", gold), ("pm25", [("กรุงเทพ", 82.4, "5"), ("ภูเก็ต", 9.0, "1")]),
                       ("lotto", lotto), ("digest", STORIES)):
        out = build_card(kind, data, today, f"/tmp/_card_{kind}.jpg", "สรุปข่าวเช้า")
        assert out and Image.open(out).size == (1080, 1350), kind
    assert build_card("gold", {}, today, "/tmp/_card_bad.jpg") is None  # broken data -> text post


if __name__ == "__main__":
    test_digest()
    test_validate_hot()
    test_validate_text()
    test_garbled_text()
    test_placeholder_leak()
    test_parse_hone()
    test_pick_hone()
    test_pick_flash()
    test_flash_photo()
    test_lotto()
    test_source_and_og()
    test_poster_plan()
    test_render_both_layouts()
    test_reel_cover()
    test_pm25()
    test_gold()
    test_affiliate()
    test_stats_parse()
    test_cards()
    print("ok")
