"""
tests/test_station_routing.py
=============================
역 단위 라우팅 regression test.

가장 중요한 케이스는 **서울역 → 강남역** 이다.
사용자는 "서울역"만 입력하는데, 시스템이 1호선 서울역으로 출발을 고정하고
"서울역에서 1호선 → 4호선 환승" 으로 시작하면 잘못이다.

실행
----
    pytest tests/ -v
    pytest tests/ -v --root .
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
    return rs, disp


def route(ctx, a, b, mode="calm"):
    rs, disp = ctx
    return station_routing.find_route_by_station(rs, disp, a, b, mode)


# ---------------------------------------------------------------- 서울역 -> 강남
def test_seoul_to_gangnam_opens_both_origin_lines(ctx):
    """서울역의 1호선/4호선 노드를 모두 출발 후보로 열어야 한다."""
    r = route(ctx, "서울역", "강남")
    assert r["ok"]
    assert r["n_origin_nodes"] == 2, "서울역은 1호선·4호선 두 노드가 출발 후보여야 한다"


def test_seoul_to_gangnam_does_not_start_with_transfer(ctx):
    """시작하자마자 '서울역 1호선 → 4호선 환승' 이 나오면 안 된다."""
    r = route(ctx, "서울역", "강남")
    segs = r["recommended"]["segments"]
    assert segs[0]["kind"] == "ride", "첫 구간은 승차여야 한다"
    first_transfer = next((s for s in segs if s["kind"] == "transfer"), None)
    if first_transfer is not None:
        assert first_transfer["at"] != "서울역", "출발역에서 곧바로 환승하면 안 된다"


def test_origin_access_not_counted_as_transfer(ctx):
    """origin_access / destination_exit 은 환승 횟수에 포함하지 않는다."""
    r = route(ctx, "서울역", "강남")
    rec = r["recommended"]
    n_transfer_segments = sum(1 for s in rec["segments"] if s["kind"] == "transfer")
    assert rec["transfer_count"] == n_transfer_segments


def test_no_station_uid_exposed(ctx):
    """사용자 화면 문구에 내부 station_uid 가 노출되면 안 된다."""
    r = route(ctx, "서울역", "강남")
    for line in station_routing.segments_to_text(r["recommended"]["segments"]):
        assert "_" not in line, "station_uid 형식이 문구에 노출됐다: %s" % line


def test_boarding_line_is_stated(ctx):
    """추천 경로 설명은 '서울역에서 N호선 승차' 형태여야 한다."""
    r = route(ctx, "서울역", "강남")
    text = station_routing.segments_to_text(r["recommended"]["segments"])[0]
    assert text.startswith("서울역에서")
    assert "승차" in text


# ---------------------------------------------------------------- 일반 케이스
@pytest.mark.parametrize("a,b", [
    ("신촌", "잠실"), ("군자", "여의나루"), ("혜화", "사당"),
    ("신설동", "까치산"), ("연신내", "응암"), ("응암", "연신내"),
])
def test_routes_are_found(ctx, a, b):
    r = route(ctx, a, b)
    assert r["ok"], "%s -> %s 경로를 찾지 못했다: %s" % (a, b, r.get("reason"))
    assert r["recommended"]["actual_time_min"] > 0


def test_eungam_loop_is_asymmetric(ctx):
    """6호선 응암순환은 단방향이라 왕복 소요시간이 달라야 한다."""
    a = route(ctx, "응암", "연신내")["recommended"]["actual_time_min"]
    b = route(ctx, "연신내", "응암")["recommended"]["actual_time_min"]
    assert abs(a - b) > 1.0, "응암순환이 대칭이면 단방향 처리가 깨진 것이다"


def test_same_station_rejected(ctx):
    r = route(ctx, "서울역", "서울역")
    assert not r["ok"]


def test_out_of_scope_line_not_in_path(ctx):
    """범위 밖 노선(9호선 등)이 경로에 포함되면 안 된다."""
    r = route(ctx, "신촌", "잠실")
    for node in r["recommended"]["path"]:
        assert node.split("_", 1)[0] in {"1", "2", "3", "4", "5", "6", "7", "8"}


def test_transfer_station_has_multiple_candidates(ctx):
    """종로3가는 1·3·5호선 세 노드를 후보로 열어야 한다."""
    _, disp = ctx
    cands = station_routing.candidates_of(disp, "종로3가")
    assert len(cands) == 3
