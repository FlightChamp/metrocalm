"""
bootstrap_yeoyuro_seoul.py
======================
여유로 서울 (Yeoyuro Seoul, 서울 지하철 혼잡도 기반 쾌적 경로 추천 시스템) 프로젝트 부트스트랩 스크립트.

이 스크립트가 하는 일
---------------------
1) Phase 1~5 전체를 담을 repository 폴더 트리를 생성한다.
2) 원본 데이터 없이도 "명세로부터 확정 가능한" master 테이블을 생성한다.
   - line_master.csv               : 프로젝트 유효 노선/구간(지선 포함)
   - transfer_station_master.csv   : 프로젝트 내 유효 환승역 35개
   - branch_node_master.csv        : 운행계통 분기점 3개
   - event_station_master.csv      : 이벤트 민감 역
   - event_calendar_master.csv     : 정규화된 이벤트 캘린더(official / actual impact window 분리)
   - university_event_master.csv   : 논술·편입 필기 이벤트(long format)
   - station_alias_master.csv      : 역명 alias seed (원본 스캔으로 확장 필요)
   - station_master.csv            : 스키마(헤더)만 생성. 실제 값은 원본 데이터에서 자동 생성.
3) config/project_scope.yaml, requirements.txt, .gitignore, README.md 스켈레톤을 생성한다.
4) 생성 결과를 자체 검증하고 콘솔 리포트 + reports/bootstrap_report.txt 로 남긴다.

사용법
------
    python bootstrap_yeoyuro_seoul.py --root .
    python bootstrap_yeoyuro_seoul.py --root C:\\projects\\metro_calm_project --force

주의
----
- 기존 파일은 기본적으로 덮어쓰지 않는다(--force 로만 덮어씀).
- station_master.csv 의 '값'은 의도적으로 생성하지 않는다.
  270여 개 역명을 하드코딩하면 개명역/신설역/오타 리스크가 있으므로,
  Phase 1 에서 원본 승하차 데이터로부터 자동 생성하는 것이 유일하게 방어 가능한 방식이다.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ENCODING = "utf-8-sig"  # Excel 에서 바로 열어도 한글이 깨지지 않도록


# =====================================================================
# 0. 역명 정규화 유틸
# =====================================================================
# 원본 데이터마다 '신촌' / '신촌역' / '총신대입구(이수)' / '이수' 처럼 표기가 다르다.
# canonical 규칙: 접미사 '역'을 제거한다. 단 '서울역'처럼 '역'이 고유명사에
# 포함된 경우는 예외로 둔다.
SUFFIX_EXEMPT_STATIONS = {"서울역"}

# 별칭 → canonical 강제 매핑(정규화만으로 해결되지 않는 케이스)
FORCED_CANONICAL = {
    "총신대입구": "이수",
    "총신대입구(이수)": "이수",
    "이수/총신대입구(이수)": "이수",
    "동대문운동장": "동대문역사문화공원",
}


def normalize_station(name: str) -> str:
    """역명을 canonical 표기로 정규화한다."""
    s = str(name).strip()
    if not s:
        return s
    if s in FORCED_CANONICAL:
        return FORCED_CANONICAL[s]
    if s in SUFFIX_EXEMPT_STATIONS:
        return s
    if s.endswith("역") and len(s) >= 3:
        s = s[:-1]
    return FORCED_CANONICAL.get(s, s)


def normalize_station_list(raw: str, sep: str = ";") -> str:
    """'고려대역;안암역' -> '고려대;안암'"""
    parts = [normalize_station(p) for p in str(raw).split(sep) if str(p).strip()]
    return sep.join(parts)


# =====================================================================
# 1. 폴더 트리
# =====================================================================
DIRECTORIES = [
    # --- data lake ---
    "data/raw/ridership_monthly",
    "data/raw/congestion_quarterly",
    "data/raw/transfer_volume",
    "data/raw/transfer_detail",
    "data/raw/route_distance_time",
    "data/raw/train_operation",
    "data/raw/event_calendar",
    "data/staging",
    "data/master",
    "data/marts",
    "data/interim",
    # --- source ---
    "src/yeoyuro_seoul/config",
    "src/yeoyuro_seoul/ingestion",
    "src/yeoyuro_seoul/validation",
    "src/yeoyuro_seoul/masters",
    "src/yeoyuro_seoul/features",
    "src/yeoyuro_seoul/models",
    "src/yeoyuro_seoul/graph",
    "src/yeoyuro_seoul/scoring",
    "src/yeoyuro_seoul/service",
    "src/yeoyuro_seoul/utils",
    # --- 실행 엔트리포인트 ---
    "scripts",
    # --- 산출물 ---
    "reports/figures",
    "reports/data_quality",
    "reports/model",
    "docs",
    "notebooks",
    "app/streamlit",
    "app/api",
    "models",
    "tests",
    "config",
]

PACKAGE_INIT_DIRS = [d for d in DIRECTORIES if d.startswith("src/yeoyuro_seoul")]


# =====================================================================
# 2. Master 테이블 정의 (명세 §4, §5, §6, §8~§10 을 그대로 정규화)
# =====================================================================

LINE_MASTER_ROWS = [
    # line_id, line_name, branch_code, branch_name, start_station, end_station, in_scope, note
    ("1", "1호선", "main", "본선", "서울역", "청량리", 1, "프로젝트 구간: 서울역-청량리"),
    ("2", "2호선", "main", "본선(순환)", "시청", "시청", 1, "순환선 전구간"),
    ("2", "2호선", "seongsu_branch", "성수지선", "성수", "신설동", 1, "분기점 성수"),
    ("2", "2호선", "sinjeong_branch", "신정지선", "신도림", "까치산", 1, "분기점 신도림"),
    ("3", "3호선", "main", "본선", "지축", "오금", 1, ""),
    ("4", "4호선", "main", "본선", "불암산", "남태령", 1, ""),
    ("5", "5호선", "main", "본선", "방화", "하남검단산", 1, ""),
    ("5", "5호선", "macheon_branch", "마천지선", "강동", "마천", 1, "분기점 강동"),
    ("6", "6호선", "main", "본선", "응암", "신내", 1, "응암순환 구간 주의"),
    ("7", "7호선", "main", "본선", "장암", "온수", 1, "온수 이서(인천구간)는 범위 밖"),
    ("8", "8호선", "main", "본선", "암사역사공원", "모란", 1, ""),
]

OUT_OF_SCOPE_LINES = [
    "9호선", "신분당선", "공항철도", "경의중앙선", "수인분당선",
    "경춘선", "우이신설선", "신림선", "김포골드라인", "GTX-A",
    "1호선(코레일 구간)", "4호선(코레일 구간)", "7호선(인천 구간)",
]

# 명세 §5 — 프로젝트 내 유효 환승역 35개
TRANSFER_STATION_ROWS = [
    ("서울역", "1,4"),
    ("시청", "1,2"),
    ("종로3가", "1,3,5"),
    ("동대문", "1,4"),
    ("동묘앞", "1,6"),
    ("신설동", "1,2(성수지선)"),
    ("을지로3가", "2,3"),
    ("을지로4가", "2,5"),
    ("동대문역사문화공원", "2,4,5"),
    ("신당", "2,6"),
    ("왕십리", "2,5"),
    ("건대입구", "2,7"),
    ("잠실", "2,8"),
    ("교대", "2,3"),
    ("사당", "2,4"),
    ("대림", "2,7"),
    ("영등포구청", "2,5"),
    ("까치산", "2(신정지선),5"),
    ("합정", "2,6"),
    ("충정로", "2,5"),
    ("연신내", "3,6"),
    ("불광", "3,6"),
    ("충무로", "3,4"),
    ("약수", "3,6"),
    ("고속터미널", "3,7"),
    ("가락시장", "3,8"),
    ("오금", "3,5"),
    ("노원", "4,7"),
    ("삼각지", "4,6"),
    ("이수/총신대입구(이수)", "4,7"),
    ("공덕", "5,6"),
    ("청구", "5,6"),
    ("군자", "5,7"),
    ("천호", "5,8"),
    ("태릉입구", "6,7"),
]

# 명세 §5 — 운행계통 분기점 3개
BRANCH_NODE_ROWS = [
    ("성수", "2", "seongsu_branch", "2호선 본선과 성수지선 분기"),
    ("신도림", "2", "sinjeong_branch", "2호선 본선과 신정지선 분기"),
    ("강동", "5", "macheon_branch", "5호선 하남검단산 방면과 마천 방면 분기"),
    ("응암", "6", "eungam_loop", "6호선 응암순환 진입점 (단방향 순환)"),
]

# 명세 §6 — 이벤트 민감 역
EVENT_STATION_ROWS = [
    # station, category, sub_category, impact_level, note
    ("종합운동장", "stadium", "large", "strong", "대형 경기장/공연"),
    ("월드컵경기장", "stadium", "large", "strong", "대형 경기장/공연"),
    ("올림픽공원", "stadium", "large", "strong", "대형 공연장(체조경기장 등)"),
    ("한강진", "stadium", "medium_small", "conditional", "블루스퀘어 등 중소형 공연"),
    ("광나루", "stadium", "medium_small", "conditional", "중소형 공연/행사"),
    ("종각", "assembly", "gwanghwamun_area", "strong", "광화문권역 집회/행사"),
    ("시청", "assembly", "gwanghwamun_area", "strong", "광화문권역 집회/행사"),
    ("광화문", "assembly", "gwanghwamun_area", "strong", "광화문권역 집회/행사"),
    ("경복궁", "assembly", "gwanghwamun_area", "conditional", "광화문권역 집회/행사"),
    ("종각", "new_year_bell", "anchor", "night_strong", "제야의 종 — 야간 시간대만 강함"),
    ("여의나루", "festival", "cherry_blossom", "very_strong", "여의도 봄꽃축제"),
    ("여의나루", "festival", "fireworks", "very_strong", "서울세계불꽃축제"),
    ("이태원", "commercial", "halloween_proxy", "conditional", "상권 혼잡 proxy"),
    ("홍대입구", "commercial", "halloween_proxy", "conditional", "상권 혼잡 proxy"),
    ("삼성", "mice", "exhibition", "strong", "코엑스 전시/MICE"),
    ("학여울", "mice", "exhibition", "conditional", "SETEC 전시"),
    ("신촌", "university", "연세대", "strong", ""),
    ("고려대", "university", "고려대", "conditional", "high_noise"),
    ("안암", "university", "고려대", "conditional", "high_noise"),
    ("혜화", "university", "성균관대", "strong", ""),
    ("한양대", "university", "한양대", "very_strong", "단일 접근역 대표 사례"),
    ("이대", "university", "이화여대", "strong", ""),
    ("건대입구", "university", "건국대", "conditional", "high_noise(환승역)"),
    ("동대입구", "university", "동국대", "strong", ""),
    ("홍대입구", "university", "홍익대", "conditional", "high_noise"),
    ("상수", "university", "홍익대", "conditional", "high_noise"),
    ("숙대입구", "university", "숙명여대", "strong", ""),
]

# 명세 §8 — 이벤트 캘린더 (official 기간과 actual crowd impact window 를 분리)
EVENT_CALENDAR_ROWS = [
    # event_id, event_type, event_name,
    # official_start, official_end, impact_start, impact_end, peak_date,
    # peak_hour_bins, anchor_stations, impact_stations, confidence, strength_prior, note
    ("NYB_2022", "new_year_bell", "2022 제야의 종",
     "2022-12-31", "2023-01-01", "2022-12-31", "2023-01-01", "2022-12-31",
     "20;21;22;23;00;01", "종각", "시청;종로3가;을지로입구;광화문", "A", "night_strong",
     "일일 총량은 오히려 감소. 야간 시간대 피처로만 사용"),
    ("NYB_2023", "new_year_bell", "2023 제야의 종·새해맞이 카운트다운",
     "2023-12-31", "2024-01-01", "2023-12-31", "2024-01-01", "2023-12-31",
     "20;21;22;23;00;01", "종각", "시청;종로3가;을지로입구;광화문", "A", "night_strong",
     "§11-2 검증: 총량 감소, 21시 이후 피크 강함"),
    ("NYB_2024", "new_year_bell", "2024 제야의 종",
     "2024-12-31", "2025-01-01", "2024-12-31", "2025-01-01", "2024-12-31",
     "20;21;22;23;00;01", "종각", "시청;종로3가;을지로입구;광화문", "A", "night_strong", ""),
    ("NYB_2025", "new_year_bell", "2025 제야의 종",
     "2025-12-31", "2026-01-01", "2025-12-31", "2026-01-01", "2025-12-31",
     "20;21;22;23;00;01", "종각", "시청;종로3가;을지로입구;광화문", "A", "night_strong", ""),

    ("CB_2023", "cherry_blossom", "2023 영등포 여의도 봄꽃축제",
     "2023-04-04", "2023-04-09", "2023-04-01", "2023-04-09", "2023-04-02",
     "", "여의나루", "여의도;마포;공덕", "A", "strong",
     "§11-1 검증: 공식기간보다 직전 주말(04-01~04-03) 급증이 더 강함"),
    ("CB_2024", "cherry_blossom", "2024 영등포 여의도 봄꽃축제",
     "2024-03-29", "2024-04-02", "2024-03-29", "2024-04-02", "",
     "", "여의나루", "여의도;마포;공덕", "A", "strong", ""),
    ("CB_2025", "cherry_blossom", "2025 영등포 여의도 봄꽃축제",
     "2025-04-08", "2025-04-12", "2025-04-08", "2025-04-12", "",
     "", "여의나루", "여의도;마포;공덕", "A", "strong", ""),
    ("CB_2026", "cherry_blossom", "2026 영등포 여의도 봄꽃축제",
     "2026-04-03", "2026-04-07", "2026-04-03", "2026-04-07", "",
     "", "여의나루", "여의도;마포;공덕", "A", "strong", "미래 이벤트 — 학습 제외, 추론용"),

    ("FW_2022", "fireworks", "서울세계불꽃축제 2022",
     "2022-10-08", "2022-10-08", "2022-10-08", "2022-10-08", "2022-10-08",
     "15;16;17;18;19;20;21;22", "여의나루", "여의도;마포;공덕", "A-", "very_strong",
     "§11-1 검증"),
    ("FW_2023", "fireworks", "서울세계불꽃축제 2023",
     "2023-10-07", "2023-10-07", "2023-10-07", "2023-10-07", "2023-10-07",
     "15;16;17;18;19;20;21;22", "여의나루", "여의도;마포;공덕", "A", "very_strong",
     "§11-2 검증: 여의나루 +289.7%, 여의도 +223.0%, 마포 +195.9%, 공덕 +39.2%"),
    ("FW_2024", "fireworks", "서울세계불꽃축제 2024",
     "2024-10-05", "2024-10-05", "2024-10-05", "2024-10-05", "2024-10-05",
     "15;16;17;18;19;20;21;22", "여의나루", "여의도;마포;공덕", "A", "very_strong", ""),
    ("FW_2025", "fireworks", "서울세계불꽃축제 2025",
     "2025-09-27", "2025-09-27", "2025-09-27", "2025-09-27", "2025-09-27",
     "15;16;17;18;19;20;21;22", "여의나루", "여의도;마포;공덕", "A", "very_strong", ""),

    ("HW_2022", "halloween_proxy", "Halloween commercial crowd period 2022",
     "2022-10-28", "2022-10-31", "2022-10-28", "2022-10-31", "2022-10-29",
     "18;19;20;21;22;23", "이태원;홍대입구", "상수;합정;신촌", "B", "medium",
     "§11-1: 이태원 peak 확인. 공식 축제 아님(proxy)"),
    ("HW_2023", "halloween_proxy", "Halloween commercial crowd period 2023",
     "2023-10-27", "2023-10-31", "2023-10-27", "2023-10-31", "2023-10-28",
     "18;19;20;21;22;23", "이태원;홍대입구", "상수;합정;신촌", "B", "weak",
     "§11-2: 전체 증가율 약함 → 낮은 가중치 proxy"),
    ("HW_2024", "halloween_proxy", "Halloween commercial crowd period 2024",
     "2024-10-25", "2024-10-31", "2024-10-25", "2024-10-31", "2024-10-26",
     "18;19;20;21;22;23", "이태원;홍대입구", "상수;합정;신촌", "B", "not_verified", ""),
    ("HW_2025", "halloween_proxy", "Halloween commercial crowd period 2025",
     "2025-10-24", "2025-11-02", "2025-10-31", "2025-11-02", "2025-11-01",
     "18;19;20;21;22;23", "이태원;홍대입구", "상수;합정;신촌", "B", "not_verified",
     "high impact window 2025-10-31~11-02, peak 후보 11-01 별도 관리"),
]

# 명세 §9, §10 — 대학 논술/편입 (wide -> long 정규화)
# (university, impact_stations, {academic_year: date_or_range}, confidence, note)
UNIV_ESSAY_RAW = [
    ("연세대", "신촌역", {2023: "2022-10-01", 2024: "2023-09-23",
                          2025: "2024-10-12", 2026: "2025-09-27"}, "A-/B", ""),
    ("고려대", "고려대역;안암역", {2023: None, 2024: None,
                                   2025: "2024-11-16~2024-11-17", 2026: "2025-11-15~2025-11-16"}, "B", ""),
    ("성균관대", "혜화역", {2023: "2022-11-19~2022-11-20", 2024: "2023-11-18~2023-11-19",
                            2025: "2024-11-16~2024-11-17", 2026: "2025-11-15~2025-11-16"}, "B", ""),
    ("한양대", "한양대역", {2023: "2022-11-26~2022-11-27", 2024: "2023-11-25~2023-11-26",
                            2025: "2024-11-23~2024-11-24", 2026: "2025-11-22~2025-11-23"}, "B", ""),
    ("이화여대", "이대역", {2023: "2022-11-26~2022-11-27", 2024: "2023-11-25~2023-11-26",
                            2025: "2024-11-23~2024-11-24", 2026: "2025-11-22~2025-11-23"}, "B", ""),
    ("건국대", "건대입구역", {2023: "2022-11-19", 2024: "2023-11-18",
                              2025: "2024-11-16", 2026: "2025-11-15"}, "B", ""),
    ("동국대", "동대입구역", {2023: "2022-11-20", 2024: "2023-11-19",
                              2025: "2024-11-17", 2026: "2025-11-16"}, "B", ""),
    ("홍익대", "홍대입구역;상수역", {2023: "2022-10-08~2022-10-09", 2024: "2023-10-07~2023-10-08",
                                     2025: "2024-10-05~2024-10-06", 2026: "2025-10-18~2025-10-19"}, "B",
     "2024·2025학년도는 불꽃축제와 날짜 충돌 → 교란요인 반드시 분리"),
    ("숙명여대", "숙대입구역", {2023: "2022-11-19~2022-11-20", 2024: "2023-11-18~2023-11-19",
                                2025: "2024-11-16~2024-11-17", 2026: "2025-11-15~2025-11-16"}, "B", ""),
]

UNIV_TRANSFER_RAW = [
    ("연세대", "신촌역", {2023: "2022-12-18", 2024: "2023-12-23",
                          2025: "2024-12-21", 2026: "2025-12-20"}, "B",
     "전 모집단위 대체로 동일 시간대; 음악대학 등 예외 제외"),
    ("고려대", "고려대역;안암역", {2023: "2022-12-17", 2024: "2023-12-16",
                                   2025: "2024-12-14", 2026: "2025-12-13"}, "B",
     "2023·2024는 오전 자연/오후 인문; 2025·2026은 오전 인문/오후 자연"),
    ("성균관대", "혜화역", {2023: "2023-01-07", 2024: "2024-01-06",
                            2025: "2025-01-04", 2026: "2026-01-03"}, "B",
     "인문/자연 같은 날; 오전/점심 시간대 분리"),
    ("한양대", "한양대역", {2023: "2023-01-07", 2024: "2024-01-13",
                            2025: "2025-01-11", 2026: "2026-01-10"}, "B",
     "2026은 자연 14:00; 인문·간호 야간 17:00"),
    ("이화여대", "이대역", {2023: "2022-12-18", 2024: "2023-12-17",
                            2025: "2024-12-15", 2026: "2025-12-14"}, "B",
     "2026은 인문계열 오전; 자연계열 오후"),
    ("건국대", "건대입구역", {2023: "2022-12-23", 2024: "2023-12-28",
                              2025: "2024-12-27", 2026: "2025-12-24"}, "B",
     "2026은 인문/예체능 오전; 자연 오후"),
    ("동국대", "동대입구역", {2023: "2023-01-08", 2024: "2024-01-07",
                              2025: "2025-01-05", 2026: "2026-01-04"}, "B",
     "인문/자연 같은 날로 관리"),
    ("홍익대", "홍대입구역;상수역", {2023: "2023-01-06", 2024: "2024-01-05",
                                     2025: "2025-01-03", 2026: "2026-01-02"}, "B",
     "서울캠퍼스 인문/자연 지원자 대상"),
    ("숙명여대", "숙대입구역", {2023: "2022-12-17", 2024: "2023-12-16",
                                2025: "2024-12-14", 2026: "2025-12-13"}, "B",
     "인문/자연 같은 날로 관리"),
]

# §11 에서 이미 검증된 효과 (event_strength 사전값)
VERIFIED_EFFECT = {
    ("essay", "한양대", 2023): "confirmed_strong",
    ("essay", "한양대", 2024): "confirmed_strong",
    ("essay", "동국대", 2023): "confirmed",
    ("essay", "동국대", 2024): "confirmed",
    ("essay", "숙명여대", 2023): "confirmed",
    ("essay", "이화여대", 2023): "confirmed",
    ("essay", "건국대", 2023): "confirmed",
    ("essay", "성균관대", 2023): "confirmed",
    ("essay", "연세대", 2023): "confirmed",
    ("transfer_exam", "고려대", 2023): "confirmed",
    ("transfer_exam", "고려대", 2024): "confirmed",
    ("transfer_exam", "동국대", 2023): "confirmed",
    ("transfer_exam", "동국대", 2024): "confirmed",
    ("transfer_exam", "숙명여대", 2024): "confirmed",
    ("transfer_exam", "한양대", 2023): "negative_observed",
    ("transfer_exam", "한양대", 2024): "negative_observed",
    ("transfer_exam", "연세대", 2024): "weak",
    ("transfer_exam", "건국대", 2024): "weak",
    ("transfer_exam", "성균관대", 2024): "weak",
    ("transfer_exam", "홍익대", 2024): "weak",
}

# 대학별 역 영향 강도 (§6-3)
UNIV_STATION_IMPACT = {
    "한양대": "very_strong",
    "연세대": "strong",
    "성균관대": "strong",
    "이화여대": "strong",
    "동국대": "strong",
    "숙명여대": "strong",
    "고려대": "conditional",
    "건국대": "conditional",
    "홍익대": "conditional",
}

# direction_master — 원본 방향 표기의 물리적 의미 (혼잡도 데이터 실측으로 확정)
# 판정 근거:
#   본선  : 오전 피크 부하 증감 방향이 실제 출근 동선과 일치 (사당->강남 = 외선, 신림->신도림 = 내선)
#   지선  : 종점역(신설동/까치산)에서 한쪽 방향 값이 0 -> 그 방향은 존재하지 않는 방향
DIRECTION_MASTER_ROWS = [
    # line_id, branch_code, direction_raw, direction, toward, rule, note
    ("1", "main", "상선", "up", "소요산 방면", "코레일 표준", ""),
    ("1", "main", "하선", "down", "인천/신창 방면", "코레일 표준", ""),
    ("2", "main", "내선", "inner", "역번호 증가", "실측 확정", "시청->을지로입구 방향"),
    ("2", "main", "외선", "outer", "역번호 감소", "실측 확정", "사당->강남 방향"),
    ("2", "seongsu_branch", "내선", "inner", "성수(본선) 방면", "실측 확정",
     "신설동 외선=0. 오전 피크 내선 우세(용답 53.3 vs 11.5)"),
    ("2", "seongsu_branch", "외선", "outer", "신설동 방면", "실측 확정",
     "오후 피크 외선 우세(성수 9002 외선 73)"),
    ("2", "sinjeong_branch", "외선", "outer", "신도림(본선) 방면", "실측 확정",
     "까치산 내선=0. 오전 피크 외선 우세(도림천 86.0 vs 22.8)"),
    ("2", "sinjeong_branch", "내선", "inner", "까치산 방면", "실측 확정",
     "오후 피크 내선 우세(도림천 73.2)"),
    ("3", "main", "상선", "up", "대화 방면", "코레일 표준", ""),
    ("3", "main", "하선", "down", "오금 방면", "코레일 표준", ""),
    ("4", "main", "상선", "up", "불암산 방면", "코레일 표준", ""),
    ("4", "main", "하선", "down", "남태령/오이도 방면", "코레일 표준", ""),
    ("5", "main", "상선", "up", "방화 방면", "코레일 표준", ""),
    ("5", "main", "하선", "down", "하남검단산 방면", "코레일 표준", ""),
    ("5", "macheon_branch", "하선", "down", "마천 방면", "실측 확정",
     "강동(마천) 9005 는 하선만 존재 -> 단방향 edge"),
    ("6", "main", "상선", "up", "응암 방면", "코레일 표준", ""),
    ("6", "main", "하선", "down", "신내 방면", "코레일 표준", ""),
    ("6", "eungam_loop", "하선", "down", "응암순환(단방향)", "실측 확정",
     "역촌·불광·독바위·연신내·구산·응암S 는 하선만 존재"),
    ("7", "main", "상선", "up", "장암 방면", "코레일 표준", ""),
    ("7", "main", "하선", "down", "온수 방면", "코레일 표준", ""),
    ("8", "main", "상선", "up", "암사역사공원 방면", "코레일 표준", ""),
    ("8", "main", "하선", "down", "모란 방면", "코레일 표준", ""),
]

# 성수역 코드 사용 규칙 — 본선 외선이 3개 코드로 분산되어 있다.
# 인접역(뚝섬 33.5 / 건대입구 33.6) 수준과 일치하는 코드는 9001 뿐이다.
STATION_CODE_USAGE_ROWS = [
    # line_id, station_code, station_name, direction, role, mean_congestion, use_for_graph, note
    ("2", 211, "성수", "내선", "본선 내선", 32.4, 1, "인접역 수준. 그대로 사용"),
    ("2", 211, "성수", "외선", "미상(부분 계통)", 16.8, 0, "인접역의 절반. 본선 외선으로 쓰면 과소평가"),
    ("2", 9001, "성수E", "외선", "본선 외선", 32.5, 1, "인접역 수준. 본선 외선 노드로 사용"),
    ("2", 9002, "성수", "외선", "성수지선 진입", 18.1, 0, "저녁 급증(73). 지선 방면 계통"),
    ("2", 9003, "신도림", "내선", "신정지선 분기", None, 1, "지선 계통"),
    ("5", 9005, "강동(마천)", "하선", "마천 방면 분기", None, 1, "단방향"),
    ("6", 9006, "응암S", "하선", "응암순환 진입", None, 1, "단방향"),
]

# station_alias_master seed — 원본 스캔 전에도 확실한 케이스만 수록
ALIAS_SEED_ROWS = [
    # alias_name, canonical_name, line_id, alias_source, note
    ("총신대입구(이수)", "이수", "4", "official_name", "4호선 공식명. 7호선은 '이수'"),
    ("총신대입구", "이수", "4", "abbrev", ""),
    ("이수(총신대입구)", "이수", "4", "variant", ""),
    ("동대문운동장", "동대문역사문화공원", "2,4,5", "renamed_2009", "구 역명"),
    ("올림픽공원(한국체대)", "올림픽공원", "5", "parenthesis", ""),
    ("천호(풍납토성)", "천호", "5,8", "parenthesis", ""),
    ("군자(능동)", "군자", "5,7", "parenthesis", ""),
    ("몽촌토성(평화의문)", "몽촌토성", "8", "parenthesis", ""),
    ("광나루(장신대)", "광나루", "5", "parenthesis", ""),
    ("굽은다리(강동구민회관앞)", "굽은다리", "5", "parenthesis", ""),
    ("오목교(목동운동장앞)", "오목교", "5", "parenthesis", ""),
    ("남한산성입구(성남법원·검찰청)", "남한산성입구", "8", "parenthesis", ""),
    ("상월곡(한국과학기술연구원)", "상월곡", "6", "parenthesis", ""),
    ("월곡(동덕여대)", "월곡", "6", "parenthesis", ""),
    ("화랑대(서울여대입구)", "화랑대", "6", "parenthesis", ""),
    ("공릉(서울과학기술대)", "공릉", "7", "parenthesis", ""),
    ("어린이대공원(세종대)", "어린이대공원", "7", "parenthesis", ""),
    ("종합운동장(잠실)", "종합운동장", "2", "parenthesis", ""),
    ("이촌(국립중앙박물관)", "이촌", "4", "parenthesis", ""),
    ("숙대입구(갈월)", "숙대입구", "4", "parenthesis", ""),
    ("신용산", "신용산", "4", "identity", "정규화 무변경 확인용 샘플"),
    ("서울역", "서울역", "1,4", "suffix_exempt", "접미사 '역' 제거 예외"),
    ("신촌", "신촌", "2", "line_scoped", "경의중앙선 신촌과 동명이역 — line_id로 구분"),
    ("양평", "양평", "5", "line_scoped", "경의중앙선 양평과 동명이역 — 범위 밖 제외"),
    ("아산", "아산", "", "out_of_scope", "1호선 코레일 구간 — 제외 대상 예시"),
]

STATION_MASTER_HEADER = [
    "station_uid", "station_name", "station_name_raw", "line_id", "branch_code",
    "station_code_ridership", "station_code_congestion", "order_in_line",
    "is_transfer_station", "is_branch_node", "is_terminal", "in_project_scope",
    "lat", "lon", "first_seen_month", "last_seen_month", "source_files",
]


# =====================================================================
# 3. 파일 생성기
# =====================================================================
@dataclass
class BootstrapResult:
    created_dirs: list = field(default_factory=list)
    created_files: list = field(default_factory=list)
    skipped_files: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


class YeoyuroSeoulBootstrapper:
    """폴더 트리 + master 테이블 + config 파일을 생성한다."""

    def __init__(self, root: Path, force: bool = False):
        self.root = root
        self.force = force
        self.result = BootstrapResult()

    # ---------- 저수준 IO ----------
    def _write(self, rel_path: str, content: str) -> None:
        path = self.root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not self.force:
            self.result.skipped_files.append(rel_path)
            return
        path.write_text(content, encoding=ENCODING)
        self.result.created_files.append(rel_path)

    def _write_csv(self, rel_path: str, header: list, rows: list) -> None:
        path = self.root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not self.force:
            self.result.skipped_files.append(rel_path)
            return
        with path.open("w", encoding=ENCODING, newline="") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(rows)
        self.result.created_files.append(rel_path)

    # ---------- 1) 폴더 ----------
    def make_directories(self) -> None:
        for d in DIRECTORIES:
            path = self.root / d
            if not path.exists():
                path.mkdir(parents=True, exist_ok=True)
                self.result.created_dirs.append(d)
            # 빈 폴더도 git 에 올라가도록
            keep = path / ".gitkeep"
            if not any(path.iterdir()) and not keep.exists():
                keep.write_text("", encoding="utf-8")
        for d in PACKAGE_INIT_DIRS:
            init = self.root / d / "__init__.py"
            if not init.exists():
                init.write_text("", encoding="utf-8")

    # ---------- 2) master 테이블 ----------
    def build_line_master(self) -> None:
        header = ["line_id", "line_name", "branch_code", "branch_name",
                  "start_station", "end_station", "in_scope", "note"]
        rows = [
            [lid, lname, bcode, bname,
             normalize_station(start), normalize_station(end), scope, note]
            for lid, lname, bcode, bname, start, end, scope, note in LINE_MASTER_ROWS
        ]
        self._write_csv("data/master/line_master.csv", header, rows)

    def build_transfer_station_master(self) -> None:
        header = ["transfer_id", "station_name", "valid_project_lines",
                  "line_count", "has_branch_line", "note"]
        rows = []
        for idx, (name, lines) in enumerate(TRANSFER_STATION_ROWS, start=1):
            canonical = normalize_station(name)
            # "1,2(성수지선)" 처럼 지선이 명시된 경우 플래그
            has_branch = 1 if "(" in lines else 0
            line_count = len([x for x in lines.split(",") if x.strip()])
            rows.append([f"TR_{idx:03d}", canonical, lines, line_count, has_branch, ""])
        self._write_csv("data/master/transfer_station_master.csv", header, rows)

    def build_branch_node_master(self) -> None:
        header = ["branch_id", "station_name", "line_id", "branch_type", "description"]
        rows = [
            [f"BR_{i:02d}", normalize_station(name), line, btype, desc]
            for i, (name, line, btype, desc) in enumerate(BRANCH_NODE_ROWS, start=1)
        ]
        self._write_csv("data/master/branch_node_master.csv", header, rows)

    def build_event_station_master(self) -> None:
        header = ["event_station_id", "station_name", "category",
                  "sub_category", "impact_level", "note"]
        rows = [
            [f"ES_{i:03d}", normalize_station(st), cat, sub, level, note]
            for i, (st, cat, sub, level, note) in enumerate(EVENT_STATION_ROWS, start=1)
        ]
        self._write_csv("data/master/event_station_master.csv", header, rows)

    def build_event_calendar_master(self) -> None:
        header = ["event_id", "event_type", "event_name",
                  "official_start_date", "official_end_date",
                  "impact_start_date", "impact_end_date", "peak_date",
                  "peak_hour_bins", "anchor_stations", "impact_stations",
                  "confidence", "strength_prior", "note"]
        rows = []
        for r in EVENT_CALENDAR_ROWS:
            r = list(r)
            r[9] = normalize_station_list(r[9])    # anchor_stations
            r[10] = normalize_station_list(r[10])  # impact_stations
            rows.append(r)
        self._write_csv("data/master/event_calendar_master.csv", header, rows)

    @staticmethod
    def _split_range(value: str):
        """'2024-11-16~2024-11-17' -> ('2024-11-16', '2024-11-17')"""
        if "~" in value:
            a, b = value.split("~", 1)
            return a.strip(), b.strip()
        return value.strip(), value.strip()

    def build_university_event_master(self) -> None:
        header = ["event_id", "event_type", "university", "academic_year",
                  "exam_start_date", "exam_end_date", "n_days",
                  "impact_stations", "station_impact_level",
                  "confidence", "verified_effect", "is_supplementary", "note"]
        rows = []

        def add(kind: str, prefix: str, raw_rows):
            for univ, stations, dates, conf, note in raw_rows:
                for ay in sorted(dates):
                    value = dates[ay]
                    if not value:  # '없음'
                        continue
                    start, end = self._split_range(value)
                    n_days = (datetime.strptime(end, "%Y-%m-%d")
                              - datetime.strptime(start, "%Y-%m-%d")).days + 1
                    rows.append([
                        f"{prefix}_{univ}_{ay}",
                        kind, univ, ay, start, end, n_days,
                        normalize_station_list(stations),
                        UNIV_STATION_IMPACT.get(univ, "conditional"),
                        conf,
                        VERIFIED_EFFECT.get((kind, univ, ay), "not_verified"),
                        0, note,
                    ])

        add("essay", "ESSAY", UNIV_ESSAY_RAW)
        add("transfer_exam", "TRF", UNIV_TRANSFER_RAW)

        # 명세 §9 주석: 연세대 2025학년도 자연계열 추가시험
        rows.append([
            "ESSAY_연세대_2025_SUP", "essay", "연세대", 2025,
            "2024-12-08", "2024-12-08", 1, "신촌",
            UNIV_STATION_IMPACT["연세대"], "B", "not_verified", 1,
            "자연계열 추가시험 — 보조 row",
        ])
        rows.sort(key=lambda r: (r[1], r[4]))
        self._write_csv("data/master/university_event_master.csv", header, rows)

    def build_direction_master(self) -> None:
        header = ["line_id", "branch_code", "direction_raw", "direction",
                  "toward", "rule", "note"]
        self._write_csv("data/master/direction_master.csv", header,
                        [list(r) for r in DIRECTION_MASTER_ROWS])

    def build_station_code_usage(self) -> None:
        header = ["line_id", "station_code", "station_name", "direction", "role",
                  "mean_congestion", "use_for_graph", "note"]
        rows = [["" if v is None else v for v in r] for r in STATION_CODE_USAGE_ROWS]
        self._write_csv("data/master/station_code_usage.csv", header, rows)

    def build_station_alias_master(self) -> None:
        header = ["alias_name", "canonical_name", "line_id", "alias_source", "note"]
        rows = [list(r) for r in ALIAS_SEED_ROWS]
        self._write_csv("data/master/station_alias_master.csv", header, rows)

    def build_station_master_stub(self) -> None:
        """값은 비우고 스키마만 생성 — Phase 1 파이프라인이 채운다.

        이미 01_build_ridership_mart.py 가 채워 넣었다면 --force 여도 절대 덮어쓰지 않는다.
        """
        path = self.root / "data/master/station_master.csv"
        if path.exists():
            try:
                n = sum(1 for _ in path.open(encoding=ENCODING)) - 1
            except OSError:
                n = 0
            if n > 0:
                self.result.skipped_files.append(
                    f"data/master/station_master.csv (실데이터 {n}행 — 보호됨)")
                return
        self._write_csv("data/master/station_master.csv", STATION_MASTER_HEADER, [])
        self.result.warnings.append(
            "station_master.csv 는 헤더만 생성했습니다. "
            "역 목록은 원본 승하차 데이터 스캔으로 자동 생성해야 정확합니다(수기 입력 금지)."
        )

    # ---------- 3) config / repo 파일 ----------
    def build_config(self) -> None:
        lines_yaml = "\n".join(
            f"  - line_id: \"{lid}\"\n"
            f"    branch_code: {bcode}\n"
            f"    start_station: {normalize_station(start)}\n"
            f"    end_station: {normalize_station(end)}"
            for lid, _, bcode, _, start, end, _, _ in LINE_MASTER_ROWS
        )
        out_yaml = "\n".join(f"  - {x}" for x in OUT_OF_SCOPE_LINES)
        content = f"""# 여유로 서울 (Yeoyuro Seoul) 프로젝트 범위 정의 (single source of truth)
# 모든 파이프라인은 이 파일을 읽어 in-scope 필터를 적용한다.
project:
  name: 여유로 서울 (Yeoyuro Seoul)
  description: 서울 지하철 1~8호선 혼잡도 기반 쾌적 경로 추천 시스템
  operator: 서울교통공사

scope:
  in_scope_lines:
{lines_yaml}
  out_of_scope_lines:
{out_yaml}

masters:
  station_master: data/master/station_master.csv
  station_alias_master: data/master/station_alias_master.csv
  line_master: data/master/line_master.csv
  transfer_station_master: data/master/transfer_station_master.csv
  branch_node_master: data/master/branch_node_master.csv
  event_station_master: data/master/event_station_master.csv
  event_calendar_master: data/master/event_calendar_master.csv
  university_event_master: data/master/university_event_master.csv

expected_counts:
  transfer_stations: {len(TRANSFER_STATION_ROWS)}
  branch_nodes: {len(BRANCH_NODE_ROWS)}

naming:
  station_suffix_exempt:
    - 서울역
  forced_canonical:
{chr(10).join(f'    "{k}": {v}' for k, v in FORCED_CANONICAL.items())}

congestion:
  # 혼잡도 데이터는 특정 날짜 실측치가 아니라 분기·요일유형별 평균 패턴이다.
  target_semantics: expected_congestion_pattern
  high_congestion_thresholds: [100, 130, 150]
  time_bin_minutes: 30
  day_types: [weekday, saturday, sunday]

route_scoring:
  default_weights:
    alpha_travel_time: 1.0
    beta_congestion: 0.6
    gamma_transfer: 0.8
    delta_event_risk: 0.7
    eta_seat_bonus: 0.3
  modes: [fast, calm, seat, min_transfer, balanced]
"""
        self._write("config/project_scope.yaml", content)

    def build_repo_files(self) -> None:
        self._write("requirements.txt", """# --- core ---
pandas>=2.2
numpy>=1.26
pyarrow>=15.0
duckdb>=1.0
pyyaml>=6.0
openpyxl>=3.1

# --- modeling ---
scikit-learn>=1.4
lightgbm>=4.3
mlflow>=2.12

# --- graph ---
networkx>=3.2

# --- service ---
fastapi>=0.110
uvicorn>=0.29
streamlit>=1.33
pydantic>=2.6

# --- viz / report ---
matplotlib>=3.8
plotly>=5.20
jinja2>=3.1

# --- dev ---
pytest>=8.0
ruff>=0.4
tqdm>=4.66
""")
        self._write(".gitignore", """__pycache__/
*.py[cod]
.venv/
venv/
.env
.ipynb_checkpoints/

# 원본/중간 데이터는 커밋하지 않는다 (용량·재배포 제약)
data/raw/**
data/staging/**
data/interim/**
data/marts/**
!data/**/.gitkeep

# master 는 커밋한다 (프로젝트 정의의 일부)
!data/master/**

models/*.pkl
models/*.txt
mlruns/
reports/figures/*.png
.DS_Store
""")
        self._write("README.md", """# 여유로 서울 (Yeoyuro Seoul)

> 서울교통공사 1~8호선 과거 승하차·혼잡도·환승·역간거리·이벤트 데이터를 결합해
> **기대 혼잡 위험도**를 추정하고, 소요시간뿐 아니라 쾌적성·환승 피로도·이벤트성 혼잡·
> 착석 가능성 proxy 를 함께 고려한 **multi-objective 경로 추천 시스템**.

## 1. 문제 정의
기존 길찾기는 최단시간·최소환승 중심이다. 여유로 서울 은 경로 추천을
`시간 / 혼잡 / 환승 피로 / 이벤트 위험 / 착석 가능성` 을 함께 최적화하는
multi-objective scoring 문제로 재정의한다.

## 2. 데이터 범위와 한계 (선언)
- 혼잡도 원본은 **특정 날짜의 실측값이 아니라 분기·요일유형별 평균 패턴**이다.
  → 본 프로젝트는 "실시간 혼잡도 예측"이 아니라 **과거 패턴 기반 기대 혼잡 위험도 추정**이다.
- 객차 단위 실측 혼잡도가 없다. → "덜 붐비는 칸"이 아니라
  **환승 동선상 유리한 호차/문** 으로만 표현한다.
- 좌석 점유 데이터가 없다. → "착석 확률"이 아니라 **착석 가능성 점수(proxy)** 로 표현한다.
- 프로젝트 범위 밖 노선(9호선·신분당선·공항철도 등)은 그래프와 환승 집계에서 제외한다.

## 3. 파이프라인
```
raw -> staging -> master -> marts -> spike detection -> congestion model
    -> event risk adjustment -> dynamic graph -> route scoring -> service
```

## 4. 실행
```bash
python bootstrap_yeoyuro_seoul.py --root .
python scripts/01_build_station_master.py
python scripts/02_build_ridership_mart.py
```

## 5. 산출물
`docs/data_dictionary.md`, `reports/data_quality/`, `reports/model/`,
Streamlit demo, FastAPI endpoint.
""")

    # ---------- 4) 자체 검증 ----------
    def validate(self) -> None:
        # 환승역 수
        if len(BRANCH_NODE_ROWS) != 4:
            self.result.warnings.append(
                f"분기점 수가 4가 아닙니다: {len(BRANCH_NODE_ROWS)}")
        if len(TRANSFER_STATION_ROWS) != 35:
            self.result.warnings.append(
                f"환승역 수가 35가 아닙니다: {len(TRANSFER_STATION_ROWS)}")
        # 중복 검사
        names = [normalize_station(n) for n, _ in TRANSFER_STATION_ROWS]
        dup = {n for n in names if names.count(n) > 1}
        if dup:
            self.result.warnings.append(f"환승역 중복: {dup}")
        # 분기점이 환승역 목록에 섞이지 않았는지
        branch_names = {normalize_station(n) for n, *_ in BRANCH_NODE_ROWS}
        overlap = branch_names & set(names)
        if overlap:
            self.result.warnings.append(
                f"분기점이 환승역 목록에 포함됨(설계상 분리 필요): {overlap}")
        # 이벤트 캘린더 날짜 정합성
        for r in EVENT_CALENDAR_ROWS:
            eid = r[0]
            for label, s, e in (("official", r[3], r[4]), ("impact", r[5], r[6])):
                if s and e and s > e:
                    self.result.warnings.append(f"{eid}: {label} 시작일 > 종료일")
            if r[5] and r[3] and r[5] < r[3]:
                pass  # impact window 가 official 보다 앞서는 것은 의도된 케이스(CB_2023)
        # 이벤트 날짜 충돌 탐지(같은 날 두 이벤트 → 교란요인)
        univ_dates = {}
        for univ, stations, dates, *_ in UNIV_ESSAY_RAW:
            for ay, v in dates.items():
                if v:
                    univ_dates.setdefault(self._split_range(v)[0], []).append(f"논술:{univ}")
        for r in EVENT_CALENDAR_ROWS:
            d = r[3]
            if d in univ_dates:
                self.result.warnings.append(
                    f"[교란요인] {d} 에 {r[2]} 와 {', '.join(univ_dates[d])} 동시 발생 → spike 해석 시 분리 필요")

    # ---------- 실행 ----------
    def run(self) -> BootstrapResult:
        self.make_directories()
        self.build_line_master()
        self.build_transfer_station_master()
        self.build_branch_node_master()
        self.build_event_station_master()
        self.build_event_calendar_master()
        self.build_university_event_master()
        self.build_station_alias_master()
        self.build_direction_master()
        self.build_station_code_usage()
        self.build_station_master_stub()
        self.build_config()
        self.build_repo_files()
        self.validate()
        return self.result


# =====================================================================
# 4. 리포트 출력
# =====================================================================
def render_report(root: Path, res: BootstrapResult) -> str:
    lines = []
    lines.append("=" * 68)
    lines.append(" 여유로 서울 bootstrap report")
    lines.append(f" root : {root.resolve()}")
    lines.append(f" time : {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append("=" * 68)
    lines.append(f"[생성된 폴더]  {len(res.created_dirs)} 개")
    lines.append(f"[생성된 파일]  {len(res.created_files)} 개")
    for f in res.created_files:
        lines.append(f"   + {f}")
    if res.skipped_files:
        lines.append(f"[건너뛴 파일]  {len(res.skipped_files)} 개 (이미 존재. --force 로 덮어쓰기)")
        for f in res.skipped_files:
            lines.append(f"   . {f}")
    lines.append("")
    lines.append("[master 테이블 요약]")
    lines.append(f"   line_master              : {len(LINE_MASTER_ROWS)} rows (지선 포함)")
    lines.append(f"   transfer_station_master  : {len(TRANSFER_STATION_ROWS)} rows  (기대값 35)")
    lines.append(f"   branch_node_master       : {len(BRANCH_NODE_ROWS)} rows  (기대값 4)")
    lines.append(f"   direction_master         : {len(DIRECTION_MASTER_ROWS)} rows")
    lines.append(f"   station_code_usage       : {len(STATION_CODE_USAGE_ROWS)} rows")
    lines.append(f"   event_station_master     : {len(EVENT_STATION_ROWS)} rows")
    lines.append(f"   event_calendar_master    : {len(EVENT_CALENDAR_ROWS)} rows")
    lines.append(f"   station_alias_master     : {len(ALIAS_SEED_ROWS)} rows (seed)")
    lines.append(f"   station_master           : 0 rows (헤더만 — Phase 1 에서 자동 생성)")
    lines.append("")
    if res.warnings:
        lines.append("[검증 경고 / 확인 필요]")
        for w in res.warnings:
            lines.append(f"   ! {w}")
    else:
        lines.append("[검증] 경고 없음")
    lines.append("")
    lines.append("[다음 단계]")
    lines.append("   1) data/raw/* 하위에 원본 파일을 그대로 복사")
    lines.append("   2) scripts/01_build_station_master.py 로 station_master 자동 생성")
    lines.append("   3) unmapped_station_report.csv 확인 후 alias_master 확장")
    lines.append("=" * 68)
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="여유로 서울 프로젝트 부트스트랩")
    parser.add_argument("--root", default=".", help="프로젝트 루트 경로 (기본: 현재 폴더)")
    parser.add_argument("--force", action="store_true", help="기존 파일 덮어쓰기")
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser()
    root.mkdir(parents=True, exist_ok=True)

    boot = YeoyuroSeoulBootstrapper(root=root, force=args.force)
    res = boot.run()

    report = render_report(root, res)
    print(report)

    report_path = root / "reports" / "bootstrap_report.txt"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding=ENCODING)
    print(f"\n리포트 저장: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
