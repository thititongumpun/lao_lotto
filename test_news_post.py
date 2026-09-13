"""Self-check for news_post.py: digest formatting and the hot-message validator,
both offline. Run: uv run python test_news_post.py"""

from datetime import date

from news_post import build_digest_message, build_lotto_message, validate_hot

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


if __name__ == "__main__":
    test_digest()
    test_validate_hot()
    test_lotto()
    print("ok")
