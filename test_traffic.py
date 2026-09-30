"""Self-check for traffic.py: normalize, classify, aggregate, select, signature, build_message.
Run: uv run python test_traffic.py"""

from datetime import datetime

import traffic
from horoscope import BANGKOK
from traffic import (
    aggregate,
    build_message,
    classify,
    normalize,
    select,
    signature,
)


def test_normalize():
    """Test normalize with canned JSON and edge cases."""
    point = {"name": "ratchada_huaykwang", "road": "ratchada", "lat": 13.7840, "lon": 100.5741}
    canned = {
        "flowSegmentData": {
            "frc": "FRC1",
            "currentSpeed": 20,
            "freeFlowSpeed": 50,
            "currentTravelTime": 300,
            "freeFlowTravelTime": 120,
            "confidence": 0.9,
            "roadClosure": False,
            "coordinates": {"coordinate": [{"latitude": 13.78, "longitude": 100.57}]},
        }
    }
    seg = normalize(point, canned, "2026-09-30T18:05:00+07:00")
    assert seg is not None
    assert seg["speed_ratio"] == 0.4, f"expected 0.4, got {seg['speed_ratio']}"
    assert seg["congestion_pct"] == 60, f"expected 60, got {seg['congestion_pct']}"
    assert seg["travel_time_ratio"] == 2.5, f"expected 2.5, got {seg['travel_time_ratio']}"
    assert seg["road_name"] == "รัชดาฯ", f"expected รัชดาฯ, got {seg['road_name']}"

    # Test None returns for invalid inputs
    assert normalize(point, {}, "2026-09-30T18:05:00+07:00") is None
    assert normalize(point, {"flowSegmentData": {"freeFlowSpeed": 0}}, "2026-09-30T18:05:00+07:00") is None
    assert normalize(point, {"flowSegmentData": {"freeFlowSpeed": 50, "confidence": 0.4}}, "2026-09-30T18:05:00+07:00") is None
    assert normalize(point, {"flowSegmentData": {"freeFlowSpeed": 50, "confidence": 0.9, "frc": "FRC5"}}, "2026-09-30T18:05:00+07:00") is None
    assert normalize(point, {"flowSegmentData": {"freeFlowSpeed": 50, "confidence": 0.9, "frc": "FRC1", "roadClosure": True}}, "2026-09-30T18:05:00+07:00") is None

    no_speed = {"flowSegmentData": {**canned["flowSegmentData"]}}
    del no_speed["flowSegmentData"]["currentSpeed"]
    assert normalize(point, no_speed, "x") is None

    # Test ratio capped at 1.0 when currentSpeed > freeFlowSpeed
    over_canned = {
        "flowSegmentData": {
            "frc": "FRC1",
            "currentSpeed": 60,
            "freeFlowSpeed": 50,
            "currentTravelTime": 100,
            "freeFlowTravelTime": 120,
            "confidence": 0.9,
            "roadClosure": False,
            "coordinates": {"coordinate": [{"latitude": 13.78, "longitude": 100.57}]},
        }
    }
    seg_over = normalize(point, over_canned, "2026-09-30T18:05:00+07:00")
    assert seg_over["speed_ratio"] == 1.0, f"expected 1.0 (capped), got {seg_over['speed_ratio']}"


def test_classify():
    """Test classify boundaries."""
    assert classify(0.3999) == "heavy"
    assert classify(0.40) == "slow"
    assert classify(0.5999) == "slow"
    assert classify(0.60) == "moderate"
    assert classify(0.7999) == "moderate"
    assert classify(0.80) == "free"


def test_aggregate():
    """Test aggregate with multi-segment and multi-road sorting."""
    # Build 3 segments for one road with ratios 0.30, 0.35, 0.95
    segs = [
        {
            "road": "ratchada",
            "road_name": "รัชดาฯ",
            "segment_id": "seg1",
            "current_speed": 15,
            "freeflow_speed": 50,
            "speed_ratio": 0.30,
            "congestion_pct": 70,
            "travel_time_ratio": 3.0,
        },
        {
            "road": "ratchada",
            "road_name": "รัชดาฯ",
            "segment_id": "seg2",
            "current_speed": 17.5,
            "freeflow_speed": 50,
            "speed_ratio": 0.35,
            "congestion_pct": 65,
            "travel_time_ratio": 2.8,
        },
        {
            "road": "ratchada",
            "road_name": "รัชดาฯ",
            "segment_id": "seg3",
            "current_speed": 47.5,
            "freeflow_speed": 50,
            "speed_ratio": 0.95,
            "congestion_pct": 5,
            "travel_time_ratio": 1.05,
        },
    ]
    roads = aggregate(segs)
    assert len(roads) == 1
    assert roads[0]["segment_count"] == 3
    assert roads[0]["traffic_level"] == "heavy", f"expected heavy (median 0.35), got {roads[0]['traffic_level']}"

    def lvl(*ratios):
        return aggregate([{"road": "ratchada", "speed_ratio": r, "congestion_pct": 0, "current_speed": 1,
                           "freeflow_speed": 1, "travel_time_ratio": 1} for r in ratios])[0]["traffic_level"]
    assert lvl(0.1) == "slow"
    assert lvl(0.1, 0.5) == "slow"
    assert lvl(0.1, 0.3) == "heavy"


def test_aggregate_multi_road_sort():
    """Test that aggregate sorts by level then speed_ratio."""
    segs = [
        {"road": "sukhumvit", "speed_ratio": 0.75, "congestion_pct": 25, "current_speed": 37.5, "freeflow_speed": 50, "travel_time_ratio": 1.3},
        {"road": "ratchada", "speed_ratio": 0.30, "congestion_pct": 70, "current_speed": 15, "freeflow_speed": 50, "travel_time_ratio": 3.0},
        {"road": "ratchada", "speed_ratio": 0.30, "congestion_pct": 70, "current_speed": 15, "freeflow_speed": 50, "travel_time_ratio": 3.0},
        {"road": "vibhavadi", "speed_ratio": 0.55, "congestion_pct": 45, "current_speed": 27.5, "freeflow_speed": 50, "travel_time_ratio": 1.8},
    ]
    roads = aggregate(segs)
    # Should be sorted: heavy, slow, moderate/free
    assert roads[0]["traffic_level"] == "heavy"
    assert roads[1]["traffic_level"] == "slow"
    assert roads[2]["traffic_level"] == "moderate"


def test_select():
    """Test select with various scenarios."""
    # All free/moderate → []
    free_roads = [
        {"traffic_level": "free", "speed_ratio": 0.85, "road": "ratchada", "road_name": "รัชดาฯ"},
        {"traffic_level": "moderate", "speed_ratio": 0.70, "road": "sukhumvit", "road_name": "สุขุมวิท"},
    ]
    assert select(free_roads) == []

    # 1 heavy + moderate + free (severity-sorted) → 3: heavy, moderate, then next in order
    mixed = [
        {"traffic_level": "heavy", "speed_ratio": 0.30, "road": "ratchada", "road_name": "รัชดาฯ"},
        {"traffic_level": "moderate", "speed_ratio": 0.70, "road": "phahon", "road_name": "พหลโยธิน"},
        {"traffic_level": "free", "speed_ratio": 0.85, "road": "sukhumvit", "road_name": "สุขุมวิท"},
        {"traffic_level": "free", "speed_ratio": 0.80, "road": "vibhavadi", "road_name": "วิภาวดี"},
        {"traffic_level": "free", "speed_ratio": 0.75, "road": "rama9", "road_name": "พระราม 9"},
    ]
    picked = select(mixed)
    assert [r["road"] for r in picked] == ["ratchada", "phahon", "sukhumvit"], picked

    # 10 heavy → len == MAX_ROADS
    heavy = [{"traffic_level": "heavy", "speed_ratio": 0.30 - i * 0.01, "road": f"road{i}", "road_name": f"ถนน{i}"} for i in range(10)]
    picked = select(heavy)
    assert len(picked) == traffic.MAX_ROADS


def test_signature():
    """Test signature unchanged/changed logic."""
    roads1 = [
        {"road": "ratchada", "traffic_level": "heavy"},
        {"road": "sukhumvit", "traffic_level": "slow"},
    ]
    sig1 = signature(roads1)

    # Same levels, different speed_ratio → signature unchanged
    roads2 = [
        {"road": "ratchada", "traffic_level": "heavy", "speed_ratio": 0.25},  # changed speed but still heavy
        {"road": "sukhumvit", "traffic_level": "slow", "speed_ratio": 0.50},
    ]
    sig2 = signature(roads2)
    assert sig1 == sig2, f"expected unchanged, sig1={sig1}, sig2={sig2}"

    # One road changes level → signature changes
    roads3 = [
        {"road": "ratchada", "traffic_level": "slow"},  # changed from heavy to slow
        {"road": "sukhumvit", "traffic_level": "slow"},
    ]
    sig3 = signature(roads3)
    assert sig1 != sig3, f"expected changed signature, sig1={sig1}, sig3={sig3}"


def test_build_message():
    """Test build_message formatting."""
    now = datetime(2026, 9, 30, 18, 5, tzinfo=BANGKOK)
    roads = [
        {"traffic_level": "heavy", "road_name": "รัชดาฯ", "current_speed": 15, "road": "ratchada"},
        {"traffic_level": "slow", "road_name": "สุขุมวิท", "current_speed": 27, "road": "sukhumvit"},
    ]
    msg = build_message(roads, now)
    assert msg.startswith("🚗 รายงานการจราจรกรุงเทพฯ"), msg
    assert "🔴 " in msg, "missing heavy emoji"
    assert "ความเร็วราว" in msg, "missing speed label"
    assert "อัปเดตล่าสุด 18:05 น." in msg, "missing timestamp"
    assert "http" not in msg, "should not contain URLs"


def test_fetch_point_bad_payload():
    class R:
        status_code = 200

        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    orig = traffic.requests.get
    try:
        for body in ({"flowSegmentData": {"freeFlowSpeed": None}}, []):
            traffic.requests.get = lambda *a, _b=body, **k: R(_b)
            p = {"name": "x", "lat": 1, "lon": 2, "road": "ratchada"}
            assert traffic.fetch_point(p, "k", "t") is None
    finally:
        traffic.requests.get = orig


if __name__ == "__main__":
    test_fetch_point_bad_payload()
    test_normalize()
    test_classify()
    test_aggregate()
    test_aggregate_multi_road_sort()
    test_select()
    test_signature()
    test_build_message()
    print("ok")
