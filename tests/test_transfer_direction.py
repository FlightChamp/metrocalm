"""
tests/test_transfer_direction.py
================================
환승 방면 표기 regression test.

원본 `수도권 도시철도 환승 데이터` 의 강동 5→5 두 행은 `하차 열차 방면` 이
'강동 방면' 으로, 즉 환승역 자기 자신으로 기재돼 있다.
"강동역에서 강동 방면으로 하차" 는 성립하지 않는다.

원본을 고치지 않고, 화면에서 해당 방면 문구만 생략한다.
호차/문 안내는 그대로 유지한다.

판단은 substring 이 아니라 정규화 후 완전 일치로 한다.
'동대문' 이 '동대문역사문화공원' 에 포함된다는 이유로 무효 처리하면 안 된다.

실행
----
    pytest tests/test_transfer_direction.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app" / "streamlit"))

station_routing = pytest.importorskip("station_routing")

is_valid_toward = station_routing.is_valid_toward
basis = station_routing.transfer_basis_text
norm = station_routing._norm_station_token


# ---------------------------------------------------------------- 정규화
@pytest.mark.parametrize("raw,expected", [
    ("강동 방면", "강동"),
    ("강동", "강동"),
    ("길동 방면", "길동"),
    ("서울역", "서울"),
    ("서울역 방면", "서울"),
    ("동대문역사문화공원 방면", "동대문역사문화공원"),  # 중간의 '역' 은 보존
    ("동대문", "동대문"),
    ("역촌", "역촌"),                                   # 끝이 '역' 이 아니면 그대로
])
def test_normalization(raw, expected):
    assert norm(raw) == expected


# ---------------------------------------------------------------- 유효성 판정
def test_arrive_toward_same_as_station_is_invalid():
    """강동역의 '강동 방면' 은 자기 자신이므로 무효."""
    assert is_valid_toward("강동", "강동 방면") is False


def test_depart_toward_different_station_is_valid():
    """강동역의 '길동 방면' 은 실제 인접역이므로 유효."""
    assert is_valid_toward("강동", "길동 방면") is True


@pytest.mark.parametrize("station,toward", [
    ("잠실", "몽촌토성 방면"),
    ("잠실", "석촌 방면"),
    ("잠실", "잠실나루 방면"),      # 부분 문자열이지만 다른 역
    ("잠실", "잠실새내 방면"),      # 부분 문자열이지만 다른 역
    ("왕십리", "상왕십리 방면"),    # 부분 문자열이지만 다른 역
    ("동대문", "동대문역사문화공원 방면"),
    ("동대문역사문화공원", "동대문 방면"),
])
def test_substring_is_not_treated_as_invalid(station, toward):
    """단순 포함 관계로 무효 처리하면 안 된다."""
    assert is_valid_toward(station, toward) is True


@pytest.mark.parametrize("bad", [None, "", "   ", "방면"])
def test_empty_toward_is_invalid(bad):
    assert is_valid_toward("강동", bad) is False


# ---------------------------------------------------------------- 기준 문구
def test_basis_hides_only_invalid_side():
    """강동 5→5: 하차 방면만 생략하고 승차 방면은 남긴다."""
    assert basis("강동", "강동 방면", "길동 방면") == "길동 방면 승차 기준"


def test_basis_keeps_both_when_valid():
    """잠실 8→2 처럼 양쪽이 정상이면 기존 문구를 그대로 유지한다."""
    assert basis("잠실", "몽촌토성 방면", "석촌 방면") == \
        "몽촌토성 방면 하차 · 석촌 방면 승차 기준"


def test_basis_when_both_invalid():
    assert basis("강동", "강동 방면", "강동 방면") == "환승 위치 기준"


def test_basis_hides_arrive_only():
    assert basis("강동", "길동 방면", "강동 방면") == "길동 방면 하차 기준"


def test_basis_never_returns_empty():
    """방면을 모두 숨겨도 문구 자체는 남아야 한다(호차/문 안내는 유지)."""
    for a, d in [("강동 방면", "강동 방면"), (None, None), ("", "")]:
        assert basis("강동", a, d).strip() != ""


# ---------------------------------------------------------------- 마트 실측
def test_only_known_stations_have_invalid_direction():
    """무효 방면이 강동 5→5 두 행 외에 새로 생기면 알아채야 한다."""
    pd = pytest.importorskip("pandas")
    p = ROOT / "data" / "marts" / "transfer_tip_mart.parquet"
    if not p.exists():
        pytest.skip("transfer_tip_mart 없음. 06 스크립트를 먼저 실행하세요.")
    t = pd.read_parquet(p)
    bad = t[[not is_valid_toward(r.station_name, r.arrive_toward)
             or not is_valid_toward(r.station_name, r.depart_toward)
             for r in t.itertuples()]]
    assert set(bad["station_name"]) <= {"강동"}, \
        "예상 밖의 역에 무효 방면이 있습니다:\n%s" % bad[
            ["station_name", "from_line", "to_line",
             "arrive_toward", "depart_toward"]].to_string(index=False)
    assert len(bad) == 2, "강동 5→5 는 2행이어야 합니다 (실제 %d행)" % len(bad)
