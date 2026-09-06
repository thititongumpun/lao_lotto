"""Self-check for horoscope.py: article parsing and the pubDate-is-yesterday
feed filter, both offline. Run: python test_horoscope.py"""

from datetime import date

from horoscope import parse_article, parse_feed, thai_date

ARTICLE = '''<div class="EntryReaderInner" id="EntryReader_0"><h2>ดู<a href="https://www.sanook.com/horoscope/" title="ดวง" target="_blank" class="keyword">ดวง</a>รายวันประจำวันอาทิตย์ ที่ 6 กันยายน 2569 สำหรับท่านที่เกิดวันอังคาร</h2>
<ul>
<li><strong>การงาน : </strong>เอกสารล่าช้า</li>
<li><strong>การเงิน : </strong>มีเกณฑ์รับทรัพย์</li>
<li><strong>ความรัก : </strong>ไม่มีเวลาให้กัน</li>
</ul>
<h2>เคล็ดลับเสริม<a href="https://www.sanook.com/horoscope/" title="ดวง" target="_blank" class="keyword">ดวง</a>ประจำวันนี้</h2>
<ul>
<li>ตกแต่งที่อยู่อาศัยด้วยดอกไม้หลากสี</li>
<li><strong>อัญมณีมงคล</strong>: ซันสโตน</li>
<li><strong>สีมงคล</strong>: สีส้ม</li>
<li><strong>เลขนำโชค</strong>: 0, 2, 6, 9</li>
</ul>
<h2>ฤกษ์ดีประจำสัปดาห์มีผลตั้งแต่วันที่ 1 - 7 กันยายน 2569 (ช่วงเวลาที่เหมาะสม)</h2>
<ul>
<li>05:59 - 09:59 : ฤกษ์ดีในการทำบุญขึ้นบ้านใหม่</li>
<li>09:01 - 11:01 : ฤกษ์ดีในการซื้อรถยนต์คันใหม่</li>
<li>09:09 - 15:19 : ฤกษ์ดีในการติดต่อเจรจางานต่าง ๆ</li>
<li>09:19 - 12:19 : ฤกษ์ดีในการเปิดร้านเริ่มธุรกิจใหม่</li>
<li>06:19 - 12:19 : ฤกษ์ดีในการเดินทางทำบุญ</li>
</ul>
<h2><a href="https://www.sanook.com/horoscope/" title="ดูดวง" target="_blank" class="keyword">ดูดวง</a>เพิ่มเติม</h2>
<ul>
<li><a href="https://www.sanook.com/horoscope/myhoro/daily/" target="_blank">ดวงวันนี้ ดูดวงรายวัน ประจำวันเกิด</a></li>
<li><a href="https://www.sanook.com/horoscope/" target="_blank">ดูดวง ดูดวงความรักแม่นๆ ฟรี</a></li>
<li><a href="https://www.sanook.com/horoscope/myhoro/monthly/" target="_blank">ดูดวงรายเดือน ดูดวงตามราศี ดูดวง</a></li>
</ul></div>'''

FEED = '''<rss><channel>
<item><title>ดูดวงรายวันประจำวันอาทิตย์ ที่ 6 กันยายน 2569 สำหรับท่านที่เกิดวันเสาร์</title>
<link>https://www.sanook.com/horoscope/332682/</link><pubDate>Sat, 05 Sep 2026 17:01:00 +0000</pubDate></item>
<item><title>ดูดวงรายวันประจำวันอาทิตย์ ที่ 6 กันยายน 2569 สำหรับท่านที่เกิดวันอังคาร</title>
<link>https://www.sanook.com/horoscope/332666/</link><pubDate>Sat, 05 Sep 2026 17:01:00 +0000</pubDate></item>
<item><title>ดูดวงรายวันประจำวันอาทิตย์ ที่ 6 กันยายน 2569 สำหรับท่านที่เกิดวันอังคาร</title>
<link>https://www.sanook.com/horoscope/dupe/</link><pubDate>Sat, 05 Sep 2026 17:01:00 +0000</pubDate></item>
<item><title>ดูดวงรายวันประจำวันเสาร์ ที่ 5 กันยายน 2569 สำหรับท่านที่เกิดวันอังคาร</title>
<link>https://www.sanook.com/horoscope/332638/</link><pubDate>Fri, 04 Sep 2026 17:01:00 +0000</pubDate></item>
<item><title>ข่าวอื่น</title><link>x</link><pubDate>garbage</pubDate></item>
</channel></rss>'''


def test_parse_article():
    h = parse_article(ARTICLE)
    assert h["work"] == "เอกสารล่าช้า", h
    assert h["money"] == "มีเกณฑ์รับทรัพย์"
    assert h["love"] == "ไม่มีเวลาให้กัน"
    assert h["tip"] == "ตกแต่งที่อยู่อาศัยด้วยดอกไม้หลากสี"
    assert h["gem"] == "ซันสโตน" and h["color"] == "สีส้ม"
    assert h["numbers"] == "0, 2, 6, 9"
    assert h["auspicious_header"].startswith("ฤกษ์ดีประจำสัปดาห์")
    assert len(h["auspicious"]) == 5 and h["auspicious"][0].startswith("05:59 - 09:59")


def test_parse_feed_yesterday_only_ordered_deduped():
    items = parse_feed(FEED, date(2026, 9, 6))
    assert [i["birthday"] for i in items] == ["อังคาร", "เสาร์"], items   # Sun→Sat order
    assert items[0]["link"].endswith("/332666/")                           # first dupe wins
    assert parse_feed(FEED, date(2026, 9, 5))[0]["link"].endswith("/332638/")
    assert parse_feed(FEED, date(2026, 9, 4)) == []


def test_thai_date():
    assert thai_date(date(2026, 9, 6)) == "วันอาทิตย์ที่ 6 กันยายน 2569"


if __name__ == "__main__":
    test_parse_article()
    test_parse_feed_yesterday_only_ordered_deduped()
    test_thai_date()
    print("ok")
