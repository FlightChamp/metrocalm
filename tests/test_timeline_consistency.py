"""
tests/test_timeline_consistency.py
==================================
화면에 표시되는 segment 시간과 카드 총계의 일치 regression test.

dwell 도입 과정에서 두 번 어긋났다.

1. `evaluate()` 에만 정차시간을 더하고 `describe_path()` 의 segment `minutes` 는
   주행시간만 담고 있었다. 경찰병원→동대입구에서 총계 38분 / segment 29분.
2. 이를 고치면서 `sys.modules` 로 scorer 모듈을 찾았는데,
   `load_scorer_class()` 는 `module_from_spec` + `exec_module` 로 모듈을 만들고
   `sys.modules` 에 등록하지 않는다. 조회가 None 을 반환해
   `DEFAULT_DWELL_TIME_MIN` 이 기본값 0.0 으로 떨어졌고, 화면은 그대로 29분이었다.

두 경우 모두 `evaluate()` 반환값만 검사하는 테스트로는 잡히지 않았다.
여기서는 **사용자가 실제로 보는 값**을 검사한다.

실행
----
    pytest tests/test_timeline_consistency.py -v
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


def rec(ctx, a, b):
    rs, disp, _ = ctx
    r = station_routing.find_route_by_station(rs, disp, a, b, "calm")
    assert r["ok"], "%s → %s 경로 실패: %s" % (a, b, r.get("reason"))
    return r["recommended"]


# ---------------------------------------------------------------- 상수 조회
def test_dwell_min_is_readable_from_scorer(ctx):
    """모듈 상수를 실제로 읽어야 한다.

    조용히 0.0 으로 떨어지면 화면 시간이 주행시간만 남는다.
    """
    rs, _, mod = ctx
    assert station_routing._dwell_min(rs) == mod.DEFAULT_DWELL_TIME_MIN
    assert station_routing._dwell_min(rs) > 0


# ---------------------------------------------------------------- 표시값 일치
@pytest.mark.parametrize("a,b", [
    ("경찰병원", "동대입구"),   # 무환승 장거리. 최초로 불일치가 드러난 케이스
    ("신촌", "잠실"),
    ("서울역", "강남"),
    ("광나루", "한양대"),
    ("응암", "연신내"),
    ("방화", "마천"),
    ("신설동", "까치산"),
])
def test_segment_minutes_sum_to_actual(ctx, a, b):
    """timeline 에 찍히는 시간의 합 = 카드 상단 예상 소요시간."""
    ev = rec(ctx, a, b)
    seg_total = sum(sg.get("minutes") or 0 for sg in ev["segments"])
    wait = ev.get("transfer_wait_min") or 0
    assert seg_total + wait == pytest.approx(ev["actual_time_min"], abs=0.2), (
        "segment 합 %.1f + 대기 %.1f != 총계 %.1f"
        % (seg_total, wait, ev["actual_time_min"]))


@pytest.mark.parametrize("a,b", [
    ("경찰병원", "동대입구"),
    ("신촌", "잠실"),
    ("서울역", "강남"),
])
def test_ride_segment_minutes_include_dwell(ctx, a, b):
    """각 승차 segment 의 표시 시간 = 주행 + 정차."""
    ev = rec(ctx, a, b)
    rides = [sg for sg in ev["segments"] if sg["kind"] == "ride"]
    assert rides, "승차 segment 가 있어야 한다"
    for sg in rides:
        assert sg["dwell_stop_count"] == max(sg["n_stops"] - 1, 0)
        assert sg["running_min"] + sg["dwell_min"] == pytest.approx(
            sg["minutes"], abs=0.05)


def test_segment_dwell_sums_to_route_dwell(ctx):
    """segment 별 정차 합 = 경로 전체 정차. 두 계산이 어긋나면 안 된다."""
    ev = rec(ctx, "서울역", "강남")
    seg_dwell = sum(sg.get("dwell_min") or 0
                    for sg in ev["segments"] if sg["kind"] == "ride")
    assert seg_dwell == pytest.approx(ev["dwell_time_min"], abs=0.05)


def test_no_transfer_route_segment_equals_total(ctx):
    """무환승 경로는 segment 하나의 시간이 곧 총계다."""
    ev = rec(ctx, "경찰병원", "동대입구")
    assert ev["transfer_count"] == 0
    rides = [sg for sg in ev["segments"] if sg["kind"] == "ride"]
    assert len(rides) == 1
    assert rides[0]["minutes"] == pytest.approx(ev["actual_time_min"], abs=0.2)
