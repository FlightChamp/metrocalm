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


# ---------------------------------------------------------------- 6호선 응암순환
#  구산에서 온 열차는 응암에 도착한 뒤 새절 방향으로 빠져나간다.
#  따라서 역촌으로 가려면 응암에서 내려 다음 열차를 기다려야 한다.
#
#  과거에 두 번 깨졌던 지점이다.
#   (1) 응암 노드를 하나로 두어 '구산 -> 응암 -> 역촌' 이 환승 없이 이어졌다.
#   (2) 노드를 나눈 뒤에는 '구산 -> 응암(순환끝) -> 새절 -> 응암(본선) -> 역촌' 이라는
#       U턴이 생겼다. 새절에서 반대 방향 열차로 갈아타는 것인데 비용이 없어서,
#       배차가 긴 낮 시간대에만 재승차 환승보다 싸게 나왔다.
#  그래서 시간대를 바꿔가며 확인한다.

@pytest.fixture(scope="module")
def ctx_at():
    """시각을 바꿔가며 스코어러를 만드는 팩토리."""
    if not (ROOT / "data" / "master" / "station_display_master.csv").exists():
        pytest.skip("station_display_master.csv 없음")
    mod = station_routing.load_scorer_class(ROOT)
    disp = station_routing.load_display_master(ROOT)

    def make(time_str="08:30", day_type="weekday"):
        return mod.RouteScorer(ROOT, day_type, time_str, None), disp
    return make


@pytest.mark.parametrize("time_str", ["08:30", "11:30", "13:30", "19:00", "22:00"])
def test_gusan_to_yeokchon_needs_eungam_reboard(ctx_at, time_str):
    """구산 -> 역촌 은 어느 시간대에도 응암 재승차가 있어야 한다."""
    rs, disp = ctx_at(time_str)
    r = station_routing.find_route_by_station(rs, disp, "구산", "역촌", "calm")
    assert r["ok"], r.get("reason")
    rec = r["recommended"]
    assert rec["transfer_count"] == 1, (
        "%s: 환승 %d회. 구산->역촌 은 응암 재승차가 필요하다"
        % (time_str, rec["transfer_count"]))
    tr = [x for x in rec["segments"] if x["kind"] == "transfer"]
    assert tr and tr[0]["at"] == "응암", "환승역이 응암이어야 한다"


@pytest.mark.parametrize("time_str", ["08:30", "13:30", "22:00"])
@pytest.mark.parametrize("od", [("구산", "역촌"), ("연신내", "새절"),
                                ("응암", "연신내"), ("상일동", "강남"),
                                ("천호", "마천"), ("서울역", "강남")])
def test_no_station_revisit(ctx_at, time_str, od):
    """추천 경로가 같은 역을 떨어진 위치에서 다시 지나면 안 된다.

    분기역 계통 환승(강동->강동)은 연속이므로 허용된다.
    """
    rs, disp = ctx_at(time_str)
    r = station_routing.find_route_by_station(rs, disp, od[0], od[1], "calm")
    assert r["ok"], r.get("reason")
    names = [station_routing.station_of(n) for n in r["recommended"]["path"]]
    seen, prev = set(), None
    for nm in names:
        if nm != prev:
            assert nm not in seen, "%s %s->%s 경로가 %s 를 다시 지난다: %s" % (
                time_str, od[0], od[1], nm, " -> ".join(names))
            seen.add(nm)
        prev = nm


def test_eungam_loop_end_node_not_exposed(ctx_at):
    """내부 노드명(@eungam_loop 등)이 사용자 문구에 노출되면 안 된다."""
    rs, disp = ctx_at("13:30")
    r = station_routing.find_route_by_station(rs, disp, "구산", "역촌", "calm")
    for line in station_routing.segments_to_text(r["recommended"]["segments"]):
        assert "@" not in line and "_" not in line, line


def test_transfer_wait_only_after_transfer(ctx_at):
    """환승 대기시간은 환승이 있을 때만 붙는다(최초 승차 전 대기는 제외)."""
    rs, disp = ctx_at("08:30")
    direct = station_routing.find_route_by_station(rs, disp, "강남", "잠실", "calm")
    assert direct["recommended"]["transfer_count"] == 0
    assert (direct["recommended"].get("transfer_wait_min") or 0) == 0

    with_tr = station_routing.find_route_by_station(rs, disp, "상일동", "강남", "calm")
    rec = with_tr["recommended"]
    assert rec["transfer_count"] >= 1
    assert (rec.get("transfer_wait_min") or 0) > 0
    # 예상 소요시간 = 승차·도보 + 환승 대기
    assert abs(rec["actual_time_min"]
               - (rec["ride_time_min"] + rec["transfer_wait_min"])) < 0.2


def test_transfer_tip_respects_direction(ctx_at):
    """환승 호차/문은 진행 방향에 맞게 골라야 한다.

    잠실 8->2 는 방면에 따라 하차 위치가 6-4 / 1-1 로 정반대다.
    상일동에서 오면 석촌 방면이므로 1호차여야 한다.
    """
    import importlib
    app_dir = ROOT / "app" / "streamlit"
    if not (app_dir / "metrocalm_app.py").exists():
        pytest.skip("앱 파일 없음")
    tip_mart = None
    for ext in (".parquet", ".csv.gz", ".csv"):
        p = ROOT / "data" / "marts" / ("transfer_tip_mart" + ext)
        if p.exists():
            import pandas as pd
            tip_mart = (pd.read_parquet(p) if ext == ".parquet" else pd.read_csv(p))
            break
    if tip_mart is None:
        pytest.skip("transfer_tip_mart 없음")
    sub = tip_mart[(tip_mart["station_name"] == "잠실")
                   & (tip_mart["from_line"].astype(str) == "8")
                   & (tip_mart["to_line"].astype(str) == "2")]
    if sub.empty:
        pytest.skip("잠실 8->2 패턴 없음")
    # 방면별로 하차 호차가 서로 다르다는 사실 자체를 고정한다
    cars = sub.groupby("arrive_toward")["alight_car"].first()
    assert cars.nunique() > 1, "방면별 하차 위치가 같다면 방향 매칭이 무의미하다"
