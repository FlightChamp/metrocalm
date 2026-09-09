"""
tests/test_dwell_time.py
========================
중간역 정차시간(dwell) 보정 regression test.

원본 `역간거리 및 소요시간` 의 소요시간은 순수 주행시간이며 정차시간이 없다.
`열차운행현황` 의 공표 소요시간·표정속도와 교차검증한 결과 중간역당 약 30초가
빠져 있어 DEFAULT_DWELL_TIME_MIN = 0.5 를 더한다.

정차시간을 더하는 곳은 **같은 열차로 지나가는 중간역뿐**이다.
출발역·도착역·환승 하차역·환승 승차역은 모두 제외한다.

실행
----
    pytest tests/test_dwell_time.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app" / "streamlit"))
sys.path.insert(0, str(ROOT / "scripts"))

station_routing = pytest.importorskip("station_routing")


@pytest.fixture(scope="module")
def ctx():
    if not (ROOT / "data" / "master" / "station_display_master.csv").exists():
        pytest.skip("station_display_master.csv 없음. 12 스크립트를 먼저 실행하세요.")
    mod = station_routing.load_scorer_class(ROOT)
    rs = mod.RouteScorer(ROOT, "weekday", "08:30", None)
    disp = station_routing.load_display_master(ROOT)
    return rs, disp, mod


def route(ctx, a, b, mode="calm"):
    rs, disp, _ = ctx
    return station_routing.find_route_by_station(rs, disp, a, b, mode)


def rec(ctx, a, b):
    r = route(ctx, a, b)
    assert r["ok"], "%s → %s 경로를 찾지 못했습니다: %s" % (a, b, r.get("reason"))
    return r["recommended"]


# ---------------------------------------------------------------- 상수
def test_dwell_constant_is_half_minute(ctx):
    """교차검증에서 유도한 30초. 값이 바뀌면 아래 기대값들도 함께 바뀌어야 한다."""
    _, _, mod = ctx
    assert mod.DEFAULT_DWELL_TIME_MIN == 0.5


# ---------------------------------------------------------------- 정차역 개수
# 2호선 신촌 - 이대 - 아현 - 충정로 는 연속한 인접역이며 환승이 없다.
@pytest.mark.parametrize("a,b,expected", [
    ("신촌", "이대", 0),      # 인접역: 중간역 없음
    ("신촌", "아현", 1),      # 3개 역: 이대 1개
    ("신촌", "충정로", 2),    # 4개 역: 이대·아현 2개
])
def test_dwell_stop_count_on_direct_ride(ctx, a, b, expected):
    ev = rec(ctx, a, b)
    assert ev["transfer_count"] == 0, "이 케이스는 무환승이어야 한다"
    assert ev["dwell_stop_count"] == expected
    assert ev["dwell_time_min"] == pytest.approx(expected * 0.5)


def test_adjacent_stations_have_no_dwell(ctx):
    """인접역은 출발·도착뿐이라 정차시간이 0이어야 한다."""
    ev = rec(ctx, "신촌", "이대")
    assert ev["dwell_time_min"] == 0.0


# ---------------------------------------------------------------- 환승 경로
@pytest.mark.parametrize("a,b", [
    ("서울역", "강남"),
    ("광나루", "한양대"),
    ("군자", "여의나루"),
    ("방화", "마천"),
])
def test_dwell_counts_per_ride_segment(ctx, a, b):
    """segment 별로 max(k-1, 0) 을 합한 값과 같아야 한다.

    환승역은 앞 segment 의 도착역이자 뒤 segment 의 출발역이므로
    이 정의에서 양쪽 모두 제외된다.
    """
    ev = rec(ctx, a, b)
    expected = sum(max((sg.get("n_stops") or 0) - 1, 0)
                   for sg in ev["segments"] if sg["kind"] == "ride")
    assert ev["dwell_stop_count"] == expected


def test_transfer_station_counted_neither_way(ctx):
    """환승 1회 경로에서 정차역 수 = 전체 승차 엣지 수 - 2 여야 한다.

    (segment 2개이므로 각 segment 에서 1개씩, 총 2개가 빠진다)
    """
    ev = rec(ctx, "서울역", "강남")
    if ev["transfer_count"] != 1:
        pytest.skip("이 시간대의 추천 경로가 환승 1회가 아님")
    ride_edges = sum(sg.get("n_stops") or 0
                     for sg in ev["segments"] if sg["kind"] == "ride")
    assert ev["dwell_stop_count"] == ride_edges - 2


# ---------------------------------------------------------------- 시간 합산
@pytest.mark.parametrize("a,b", [
    ("신촌", "잠실"),
    ("서울역", "강남"),
    ("응암", "연신내"),
    ("신설동", "까치산"),
])
def test_actual_time_is_sum_of_parts(ctx, a, b):
    """actual = 승차 + 정차 + 환승도보 + 환승대기.

    앱의 time_breakdown() 이 화면에 뿌리는 4개 항목이 총 예상 소요시간과
    일치해야 한다는 뜻이기도 하다.
    """
    ev = rec(ctx, a, b)
    walk = sum(sg.get("minutes") or 0
               for sg in ev["segments"] if sg["kind"] == "transfer")
    wait = ev.get("transfer_wait_min") or 0
    dwell = ev["dwell_time_min"]
    run = (ev["ride_time_min"] or 0) - dwell - walk
    assert run + dwell + walk + wait == pytest.approx(ev["actual_time_min"], abs=0.2)
    assert run > 0, "승차시간이 0 이하면 분해가 잘못된 것이다"


# ---------------------------------------------------------------- 이중 보정 방지
@pytest.mark.parametrize("a,b", [
    ("신촌", "잠실"),
    ("서울역", "강남"),
])
def test_dwell_added_once_and_uncorrected(ctx, a, b):
    """dwell 은 actual·perceived 에 각각 정확히 1회, 혼잡도 보정 없이 더해진다.

    DEFAULT_DWELL_TIME_MIN 을 0 으로 바꿔 같은 경로를 다시 평가한 뒤 차이를 본다.
    dwell 에 κ 가 곱해졌다면 perceived 증가분이 dwell 보다 커져 여기서 잡힌다.
    """
    rs, _, mod = ctx
    ev = rec(ctx, a, b)
    path = list(ev["path"])
    dwell = ev["dwell_time_min"]
    assert dwell > 0, "이 OD 는 중간역이 있어야 한다"

    before = mod.DEFAULT_DWELL_TIME_MIN
    try:
        mod.DEFAULT_DWELL_TIME_MIN = 0.0
        base = rs.evaluate(path)
    finally:
        mod.DEFAULT_DWELL_TIME_MIN = before
    full = rs.evaluate(path)

    assert base["dwell_time_min"] == 0.0
    assert full["actual_time_min"] - base["actual_time_min"] == pytest.approx(dwell, abs=0.1)
    assert full["perceived_time_min"] - base["perceived_time_min"] == pytest.approx(dwell, abs=0.1)


def test_running_time_excludes_dwell_and_walk(ctx):
    """running_time_min 은 순수 주행시간이어야 한다."""
    ev = rec(ctx, "신촌", "잠실")
    assert ev["transfer_count"] == 0
    assert ev["transfer_walk_min"] == 0.0
    assert ev["running_time_min"] + ev["dwell_time_min"] == pytest.approx(
        ev["actual_time_min"], abs=0.1)


# ---------------------------------------------------------------- 최초 승차 전 대기
def test_no_wait_before_first_boarding(ctx):
    """무환승 경로에는 대기시간이 붙지 않는다. dwell 도입 후에도 유지되어야 한다."""
    ev = rec(ctx, "신촌", "잠실")
    assert ev["transfer_count"] == 0
    assert (ev.get("transfer_wait_min") or 0) == 0.0
