"""Self-check for news_post.py: digest formatting and the hot-message validator,
both offline. Run: uv run python test_news_post.py"""

from datetime import date

from PIL import Image

import poster
from news_post import (OG_IMAGE_RE, SOURCE_RE, build_digest_message, build_lotto_message, crop_cover,
                       reel_cover, validate_hot, validate_poster_plan)

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


if __name__ == "__main__":
    test_digest()
    test_validate_hot()
    test_lotto()
    test_source_and_og()
    test_poster_plan()
    test_render_both_layouts()
    test_reel_cover()
    print("ok")
