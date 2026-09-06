"""
06_build_route_graph.py
=======================
MetroCalm Phase 4 착수 — 경로 탐색용 그래프를 구축한다.

원본 구조 (직접 확인한 사실)
----------------------------
`서울교통공사_역간거리_및_소요시간_240810.csv` 는 역 목록이 아니라
**호선별 단일 순회 경로**다. 그래서 다음 3가지를 반드시 처리해야 한다.

1. 순환 폐합
   2호선 : ... 충정로 -> 시청  (index 43 의 '시청' 은 index 0 과 같은 노드)
   6호선 : 응암 -> 역촌 -> 불광 -> 독바위 -> 연신내 -> 구산 -> 응암 (응암순환)

2. 지선 시작 행의 소요시간·거리는 '직전 행'이 아니라 '분기역' 기준이다
   2호선 용답(03:00, 2.3km)  <- 성수 기준   (직전 행은 시청)
   2호선 도림천(01:30, 1.0km) <- 신도림 기준 (직전 행은 신설동)
   5호선 둔촌동(01:50, 1.2km) <- 강동 기준   (직전 행은 하남검단산)
   6호선 새절(01:20, 0.9km)   <- 응암 기준   (직전 행은 순환 종료 응암)
   이걸 무시하면 '하남검단산 - 둔촌동' 같은 존재하지 않는 구간이 생긴다.

3. 단방향 구간
   6호선 응암순환 6개 엣지는 한 방향뿐이다. 양방향으로 만들면
   실제로 못 가는 경로를 추천하게 된다. (혼잡도 원본에서 해당 역이
   '하선'만 존재한다는 사실로 교차 확인됨)

소요시간 포맷은 mm:ss 다. '01:30' = 1.5분, '10:00' = 10분.

출력
----
    data/marts/route_edge_mart.parquet      승차 이동 엣지
    data/marts/transfer_edge_mart.parquet   환승 엣지 (그래프용)
    data/marts/transfer_tip_mart.parquet    방면별 추천 호차/문 (안내용)
    reports/data_quality/route_graph_report.md

사용법
------
    python scripts/06_build_route_graph.py --root .
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd

ENC = "utf-8-sig"
CSV_ENCODINGS = ["cp949", "utf-8-sig", "utf-8"]

# --------------------------------------------------------------------------
# 역명 정규화 (01/03 스크립트와 동일 규칙)
# --------------------------------------------------------------------------
SUFFIX_EXEMPT = {"서울역"}
FORCED = {
    "당고개": "불암산",
    "총신대입구": "이수",
    "총신대입구(이수)": "이수",
    "신천": "잠실새내",
    "미아삼거리": "미아사거리",
    "뚝섬유원지": "자양",
    "동대문운동장": "동대문역사문화공원",
}


def canon(name: str) -> str:
    s = re.sub(r"\s+", "", str(name).strip())
    if not s:
        return s
    if s in FORCED:
        return FORCED[s]
    s = re.sub(r"\(.*?\)$", "", s)          # 올림픽공원(한국체대) -> 올림픽공원
    if s not in SUFFIX_EXEMPT and s.endswith("역") and len(s) >= 3:
        s = s[:-1]                           # 신내역 -> 신내
    return FORCED.get(s, s)


# --------------------------------------------------------------------------
# 순회 경로 -> 그래프 변환 규칙
# --------------------------------------------------------------------------
# 지선 첫 역의 실제 직전역 (분기역)
SEQUENCE_BREAKS = {
    ("2", "용답"): "성수",      # 성수지선
    ("2", "도림천"): "신도림",  # 신정지선
    ("5", "둔촌동"): "강동",    # 마천지선
    ("6", "새절"): "응암",      # 응암순환 종료 후 본선 재개
}

# 응암순환 재승차 노드.
#   구산에서 온 열차는 응암에 도착한 뒤 **새절 방향으로 빠져나간다**.
#   따라서 구산 -> 응암 -> 역촌 을 한 번에 탈 수 없고, 응암에서 내려 다음 열차를
#   기다려야 한다. 이를 표현하려고 순환 종료 지점을 별도 노드로 분리한다.
#     6_응암@eungam_loop : 순환을 마치고 도착한 응암 (구산에서 진입)
#     6_응암             : 본선 응암 (새절에서 진입, 역촌으로 순환 시작)
#   두 노드 사이에는 재승차 대기를 나타내는 환승 엣지를 만든다.
EUNGAM_LOOP_END_NODE = "6_응암@eungam_loop"
EUNGAM_REBOARD_MIN = 1.0          # 승강장 이동 없음. 열차를 다시 타는 시간만.

# 단방향 엣지 (6호선 응암순환)
UNIDIRECTIONAL = {
    ("6", "응암", "역촌"),
    ("6", "역촌", "불광"),
    ("6", "불광", "독바위"),
    ("6", "독바위", "연신내"),
    ("6", "연신내", "구산"),
    ("6", "구산", "응암"),
}

# 엣지가 속한 운행계통
BRANCH_OF_EDGE = {
    ("2", "성수", "용답"): "seongsu_branch",
    ("2", "용답", "신답"): "seongsu_branch",
    ("2", "신답", "용두"): "seongsu_branch",
    ("2", "용두", "신설동"): "seongsu_branch",
    ("2", "신도림", "도림천"): "sinjeong_branch",
    ("2", "도림천", "양천구청"): "sinjeong_branch",
    ("2", "양천구청", "신정네거리"): "sinjeong_branch",
    ("2", "신정네거리", "까치산"): "sinjeong_branch",
    ("5", "강동", "둔촌동"): "macheon_branch",
    ("5", "둔촌동", "올림픽공원"): "macheon_branch",
    ("5", "올림픽공원", "방이"): "macheon_branch",
    ("5", "방이", "오금"): "macheon_branch",
    ("5", "오금", "개롱"): "macheon_branch",
    ("5", "개롱", "거여"): "macheon_branch",
    ("5", "거여", "마천"): "macheon_branch",
}

# 분기역은 본선 노드와 지선 노드를 분리한다.
# 원본 환승 데이터가 성수·신도림·강동의 '동일 호선 내 환승'을 별도 행으로 담고 있고,
# 혼잡도 원본도 9xxx 코드로 계통을 분리해 두었다. 노드를 합치면 지선 승객이
# 갈아타지 않고 통과하는 것으로 계산되어 환승 비용이 누락된다.
JUNCTIONS = {
    ("2", "성수"): ("seongsu_branch", 9002),
    ("2", "신도림"): ("sinjeong_branch", 9003),
    ("5", "강동"): ("macheon_branch", 9005),
}

# station_master 에 없지만 그래프에 필요한 노드 (혼잡도 원본 기준 코드)
STATION_CODE_OVERRIDE = {("2", "까치산"): 260}

# 환승 페널티 파라미터 (route_scoring_design.md 와 공유)
TRANSFER_VOLUME_PENALTY_MAX_MIN = 3.0


def parse_mmss(v) -> float:
    """'01:30' -> 1.5 (분).  mm:ss 포맷."""
    s = str(v).strip()
    m = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if not m:
        return np.nan
    return int(m.group(1)) + int(m.group(2)) / 60.0


def read_csv_any(path: Path) -> pd.DataFrame:
    last = None
    for enc in CSV_ENCODINGS:
        try:
            return pd.read_csv(path, encoding=enc)
        except (UnicodeDecodeError, LookupError) as e:
            last = e
    raise RuntimeError(f"인코딩 판별 실패: {path.name} ({last})")


# --------------------------------------------------------------------------
class RouteGraphBuilder:

    def __init__(self, root: Path):
        self.root = root
        self.raw = root / "data" / "raw"
        self.master = root / "data" / "master"
        self.mart = root / "data" / "marts"
        self.report = root / "reports" / "data_quality"
        for d in (self.mart, self.report):
            d.mkdir(parents=True, exist_ok=True)
        self.notes: list[str] = []
        self.station_code: dict[tuple[str, str], int] = {}

    # ---------- 파일 찾기 ----------
    def _find(self, subdir: str, pattern: str) -> list[Path]:
        d = self.raw / subdir
        if not d.exists():
            return []
        return sorted(d.glob(pattern))

    def _pick_distance_file(self) -> tuple[Path, Path | None]:
        files = self._find("route_distance_time", "*역간거리*소요시간*")
        if not files:
            files = self._find("route_distance_time", "*역간거리*")
        if not files:
            raise FileNotFoundError("역간거리 파일을 찾을 수 없습니다: data/raw/route_distance_time")
        # 최신(파일명 숫자 큰 것) 사용, 나머지는 교차검증용
        def key(p: Path):
            """파일명 날짜 정규화. '240810'(yymmdd) 과 '20230614'(yyyymmdd) 가 섞여 있어
            자릿수를 맞추지 않으면 구버전이 최신으로 뽑힌다(남위례역 누락 원인)."""
            best = 0
            for x in re.findall(r"(\d{6,8})", p.name):
                v = int("20" + x) if len(x) == 6 else int(x)
                best = max(best, v)
            return best
        files = sorted(files, key=key, reverse=True)
        primary = files[0]
        secondary = next((f for f in files[1:] if "소요시간" in f.name), None)
        return primary, secondary

    # ---------- 마스터 ----------
    def load_masters(self) -> None:
        sm_path = self.master / "station_master.csv"
        if sm_path.exists():
            sm = pd.read_csv(sm_path)
            if len(sm) == 0:
                self.notes.append("station_master.csv 가 비어 있습니다. 01 스크립트를 먼저 실행하세요.")
            for _, r in sm.iterrows():
                self.station_code[(str(r["line_id"]), canon(r["station_name"]))] = int(r["station_code"])
        else:
            self.notes.append("station_master.csv 없음 → station_code 미부여")
        self.station_code.update({k: v for k, v in STATION_CODE_OVERRIDE.items()})

    def node_id(self, line: str, name: str, branch: str = "main") -> str:
        return f"{line}_{name}" if branch == "main" else f"{line}_{name}@{branch}"

    @staticmethod
    def _split_junction(line: str, name: str, branch_code: str) -> str:
        """분기역이면서 지선 계통 엣지라면 지선 노드로 분리한다."""
        j = JUNCTIONS.get((line, name))
        if j and branch_code == j[0]:
            return j[0]
        return "main"

    # ---------- 1. 승차 엣지 ----------
    def build_ride_edges(self) -> pd.DataFrame:
        primary, secondary = self._pick_distance_file()
        self.notes.append(f"역간거리 기준 파일: {primary.name}")
        df = read_csv_any(primary)
        df.columns = [str(c).strip() for c in df.columns]

        time_col = next((c for c in ("소요시간", "운행시간") if c in df.columns), None)
        dist_col = next((c for c in df.columns if "역간거리" in c), None)
        if time_col is None or dist_col is None:
            raise KeyError(f"{primary.name}: 소요시간/역간거리 컬럼을 찾을 수 없습니다. {list(df.columns)}")

        df["line_id"] = df["호선"].astype(str).str.replace("호선", "", regex=False).str.strip()
        df["name"] = df["역명"].map(canon)
        df["t_min"] = df[time_col].map(parse_mmss)
        df["dist"] = pd.to_numeric(df[dist_col], errors="coerce")

        rows = []
        for line, g in df.groupby("line_id", sort=True):
            g = g.reset_index(drop=True)
            for i in range(1, len(g)):
                cur = g.loc[i, "name"]
                prev = SEQUENCE_BREAKS.get((line, cur), g.loc[i - 1, "name"])
                t = g.loc[i, "t_min"]
                d = g.loc[i, "dist"]
                if prev == cur:
                    continue
                rows.append({
                    "line_id": line,
                    "from_station": prev, "to_station": cur,
                    "travel_time_min": t, "distance_km": d,
                    "branch_code": BRANCH_OF_EDGE.get((line, prev, cur),
                                                      BRANCH_OF_EDGE.get((line, cur, prev), "main")),
                })

        e = pd.DataFrame(rows).drop_duplicates(subset=["line_id", "from_station", "to_station"])

        # 방향 전개: 단방향 지정된 것만 한 방향, 나머지는 양방향
        out = []
        for _, r in e.iterrows():
            key = (r["line_id"], r["from_station"], r["to_station"])
            rkey = (r["line_id"], r["to_station"], r["from_station"])
            uni = key in UNIDIRECTIONAL or rkey in UNIDIRECTIONAL
            out.append({**r, "direction_of_edge": "forward", "is_bidirectional": 0 if uni else 1})
            if not uni:
                out.append({**r, "from_station": r["to_station"], "to_station": r["from_station"],
                            "direction_of_edge": "reverse", "is_bidirectional": 1})
        ed = pd.DataFrame(out)

        ed["from_branch"] = [self._split_junction(l, s, b)
                             for l, s, b in zip(ed["line_id"], ed["from_station"], ed["branch_code"])]
        ed["to_branch"] = [self._split_junction(l, s, b)
                           for l, s, b in zip(ed["line_id"], ed["to_station"], ed["branch_code"])]
        ed["from_node"] = [self.node_id(l, s, b)
                           for l, s, b in zip(ed["line_id"], ed["from_station"], ed["from_branch"])]
        ed["to_node"] = [self.node_id(l, s, b)
                         for l, s, b in zip(ed["line_id"], ed["to_station"], ed["to_branch"])]
        ed["from_code"] = [JUNCTIONS[(l, s)][1] if b != "main" else self.station_code.get((l, s))
                           for l, s, b in zip(ed["line_id"], ed["from_station"], ed["from_branch"])]
        ed["to_code"] = [JUNCTIONS[(l, s)][1] if b != "main" else self.station_code.get((l, s))
                         for l, s, b in zip(ed["line_id"], ed["to_station"], ed["to_branch"])]
        ed["edge_type"] = "ride"

        # 교차검증: 구버전 파일과 거리 비교
        if secondary is not None:
            self._cross_check_distance(secondary, e)

        cols = ["from_node", "to_node", "line_id", "from_station", "to_station",
                "from_code", "to_code", "travel_time_min", "distance_km",
                "edge_type", "branch_code", "from_branch", "to_branch",
                "is_bidirectional", "direction_of_edge"]
        ed = ed[cols].reset_index(drop=True)
        ed = self._split_eungam_loop_end(ed)
        return ed

    def _split_eungam_loop_end(self, ed: pd.DataFrame) -> pd.DataFrame:
        """응암순환 종료 지점을 별도 노드로 분리한다.

        구산에서 온 열차는 응암에 도착한 뒤 새절 방향으로 빠져나간다.
        그래서 '구산 -> 응암 -> 역촌' 을 한 번에 탈 수 없는데, 노드를 하나로 두면
        그래프상 환승 없이 이어져 존재하지 않는 경로가 추천된다.

            구산 -> 6_응암@eungam_loop -> 새절     (순환을 마치고 본선 복귀)
            새절 -> 6_응암             -> 역촌     (본선에서 순환 진입)

        두 노드 사이는 build_transfer_edges 에서 재승차 환승 엣지로 잇는다.
        """
        m_in = (ed["from_node"] == "6_구산") & (ed["to_node"] == "6_응암")
        if not m_in.any():
            self.notes.append("응암순환 분리: '6_구산 -> 6_응암' 엣지를 찾지 못해 건너뜀")
            return ed
        ed.loc[m_in, "to_node"] = EUNGAM_LOOP_END_NODE
        ed.loc[m_in, "to_branch"] = "eungam_loop"

        # 응암 -> 새절 (본선 복귀)은 순환 종료 노드에서 출발해야 한다.
        m_out = (ed["from_node"] == "6_응암") & (ed["to_node"] == "6_새절")
        ed.loc[m_out, "from_node"] = EUNGAM_LOOP_END_NODE
        ed.loc[m_out, "from_branch"] = "eungam_loop"
        ed.loc[m_out, "is_bidirectional"] = 0

        # 새절 -> 응암 (본선에서 순환 진입)은 본선 응암으로 들어온다. 그대로 둔다.
        n_in, n_out = int(m_in.sum()), int(m_out.sum())
        self.notes.append(
            "응암순환 종료 노드 분리: 구산->%s %d개, %s->새절 %d개 "
            "(구산->응암->역촌 직통 차단)"
            % (EUNGAM_LOOP_END_NODE, n_in, EUNGAM_LOOP_END_NODE, n_out))
        return ed

    def _cross_check_distance(self, path: Path, edges: pd.DataFrame) -> None:
        try:
            d2 = read_csv_any(path)
            d2.columns = [str(c).strip() for c in d2.columns]
            tcol = next((c for c in ("소요시간", "운행시간") if c in d2.columns), None)
            dcol = next((c for c in d2.columns if "역간거리" in c), None)
            d2["line_id"] = d2["호선"].astype(str).str.replace("호선", "", regex=False).str.strip()
            d2["name"] = d2["역명"].map(canon)
            d2["dist"] = pd.to_numeric(d2[dcol], errors="coerce")
            d2["t_min"] = d2[tcol].map(parse_mmss)
            rows = []
            for line, g in d2.groupby("line_id"):
                g = g.reset_index(drop=True)
                for i in range(1, len(g)):
                    cur = g.loc[i, "name"]
                    prev = SEQUENCE_BREAKS.get((line, cur), g.loc[i - 1, "name"])
                    if prev == cur:
                        continue
                    rows.append({"line_id": line, "from_station": prev, "to_station": cur,
                                 "dist_old": g.loc[i, "dist"], "t_old": g.loc[i, "t_min"]})
            old = pd.DataFrame(rows)
            m = edges.merge(old, on=["line_id", "from_station", "to_station"], how="inner")
            diff = m[(m["distance_km"] - m["dist_old"]).abs() > 0.05]
            self.notes.append(
                f"거리 교차검증({path.name}): 공통 {len(m)}구간 중 0.05km 초과 불일치 {len(diff)}건")
            if len(diff):
                self.diff_sample = diff[["line_id", "from_station", "to_station",
                                         "distance_km", "dist_old"]].head(10)
        except Exception as e:  # 교차검증 실패는 치명적이지 않다
            self.notes.append(f"거리 교차검증 실패: {e}")

    # ---------- 2. 환승 엣지 ----------
    def load_transfer_volume(self) -> pd.DataFrame:
        files = self._find("transfer_volume", "*환승역*환승인원*")
        if not files:
            self.notes.append("환승인원 파일 없음 → 환승 혼잡 페널티 0")
            return pd.DataFrame(columns=["station_name", "weekday_volume"])
        recs = []
        for f in files:
            try:
                d = pd.read_excel(f) if f.suffix.lower() in (".xlsx", ".xls") else read_csv_any(f)
            except Exception as e:
                self.notes.append(f"환승인원 로드 실패 {f.name}: {e}")
                continue
            d.columns = [re.sub(r"\s+", "", str(c)) for c in d.columns]
            name_col = next((c for c in ("역명", "출발역명", "역") if c in d.columns), None)
            vol_col = next((c for c in d.columns if c.startswith("평일")), None)
            if not name_col or not vol_col:
                self.notes.append(f"환승인원 스키마 미인식 {f.name}: {list(d.columns)}")
                continue
            recs.append(pd.DataFrame({
                "station_name": d[name_col].map(canon),
                "weekday_volume": pd.to_numeric(d[vol_col], errors="coerce"),
                "source": f.name,
            }))
        if not recs:
            return pd.DataFrame(columns=["station_name", "weekday_volume"])
        allv = pd.concat(recs, ignore_index=True)
        self.notes.append(f"환승인원 파일 {allv['source'].nunique()}개 통합 → 역별 중앙값 사용")
        return (allv.groupby("station_name")["weekday_volume"].median()
                .reset_index().rename(columns={"weekday_volume": "weekday_volume"}))

    def build_transfer_edges(self, ride: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        files = self._find("transfer_detail", "*환승*")
        if not files:
            raise FileNotFoundError("환승 상세 파일이 없습니다: data/raw/transfer_detail")
        d = read_csv_any(files[0])
        d.columns = [str(c).strip() for c in d.columns]

        valid_lines = {"1", "2", "3", "4", "5", "6", "7", "8"}
        t = pd.DataFrame({
            "station_name": d["환승시작역"].map(canon),
            "to_station_name": d["환승종료역"].map(canon),
            "from_line": d["환승시작 호선"].astype(str).str.strip(),
            "to_line": d["환승종료 호선"].astype(str).str.strip(),
            "arrive_toward": d["하차 열차 방면"].astype(str).str.strip(),
            "depart_toward": d["환승 열차 방면"].astype(str).str.strip(),
            "alight_car": d["하차위치(호차)"],
            "alight_door": d["하차위치(문)"],
            "board_car": d["환승 승차위치(호차)"],
            "board_door": d["환승 승차위치(문)"],
            "transfer_time_min": d["소요시간"].map(parse_mmss),
        })
        n_all = len(t)
        t = t[t["from_line"].isin(valid_lines) & t["to_line"].isin(valid_lines)]
        t = t[t["station_name"] == t["to_station_name"]]      # 동일역 내 환승만
        self.notes.append(f"환승 상세 {n_all}행 중 범위 내 {len(t)}행 사용 "
                          f"({len(t) / n_all * 100:.0f}%)")

        # 그래프 노드로 존재하는 조합만
        nodes = set(ride["from_node"])
        t["from_node"] = [self.node_id(l, s) for l, s in zip(t["from_line"], t["station_name"])]
        t["to_node"] = [self.node_id(l, s) for l, s in zip(t["to_line"], t["station_name"])]

        # 동일 호선 내 환승(= 분기역 계통 환승)은 지선 노드를 대상으로 연결한다.
        same = t["from_node"] == t["to_node"]
        t.loc[same, "to_node"] = [
            self.node_id(l, s, JUNCTIONS[(l, s)][0]) if (l, s) in JUNCTIONS else n
            for l, s, n in zip(t.loc[same, "from_line"], t.loc[same, "station_name"],
                               t.loc[same, "to_node"])]
        t["is_branch_transfer"] = same.astype(int)
        still = t["from_node"] == t["to_node"]
        if still.any():
            detail = (t[still].groupby(["station_name", "from_line"]).size()
                      .reset_index(name="n"))
            desc = ", ".join(f"{r.station_name}({r.from_line}호선 {r.n}행)"
                             for r in detail.itertuples())
            self.notes.append(
                f"분기역 마스터에 없는 동일호선 환승 {int(still.sum())}행 제외 — {desc}. "
                "1호선 경부선/경인선 계통 분기로, 프로젝트 1호선 구간(서울역~청량리) 밖이다.")
            t = t[~still]
        miss = t[~t["from_node"].isin(nodes) | ~t["to_node"].isin(nodes)]
        if len(miss):
            # 짝 중 한쪽만 범위 밖인 경우가 많다. 실제로 그래프에 없는 노드만 나열한다.
            gone = sorted({n for n in set(miss["from_node"]) | set(miss["to_node"])
                           if n not in nodes})
            self.notes.append(
                f"범위 밖 노드가 포함된 환승 {len(miss)}행 제외 "
                f"(그래프에 없는 노드 {len(gone)}개: {', '.join(gone)})")
        t = t[t["from_node"].isin(nodes) & t["to_node"].isin(nodes)].copy()

        # 안내용 상세 (방면별 추천 호차/문)
        tip = (t.sort_values("transfer_time_min")
               .groupby(["station_name", "from_line", "to_line", "arrive_toward", "depart_toward"],
                        as_index=False).first())

        # 그래프용 집계
        agg = (t.groupby(["from_node", "to_node", "station_name", "from_line", "to_line"],
                         as_index=False)
               .agg(transfer_time_min=("transfer_time_min", "median"),
                    transfer_time_min_best=("transfer_time_min", "min"),
                    n_patterns=("transfer_time_min", "size"),
                    is_branch_transfer=("is_branch_transfer", "max")))

        # 환승인원 기반 페널티
        vol = self.load_transfer_volume()
        agg = agg.merge(vol, on="station_name", how="left")
        if agg["weekday_volume"].notna().any():
            pct = agg["weekday_volume"].rank(pct=True)
            agg["volume_penalty_min"] = (pct.fillna(0) * TRANSFER_VOLUME_PENALTY_MAX_MIN).round(2)
        else:
            agg["volume_penalty_min"] = 0.0
        agg["transfer_penalty_min"] = (agg["transfer_time_min"]
                                       + agg["volume_penalty_min"]).round(3)
        agg["edge_type"] = "transfer"
        agg["is_bidirectional"] = 1
        agg = self._add_eungam_reboard_edge(agg, ride)
        return agg, tip

    def _add_eungam_reboard_edge(self, agg: pd.DataFrame, ride: pd.DataFrame):
        """응암순환 종료 노드 -> 본선 응암 재승차 엣지.

        원본 환승 데이터에는 같은 역 안에서 열차를 다시 타는 경우가 없다.
        하지만 구산에서 온 승객이 역촌 방향으로 가려면 응암에서 내려 다음 열차를
        기다려야 하므로, 이 대기를 환승으로 모델링해야 한다.
        승강장 이동이 없으므로 도보 시간은 짧게 잡는다(EUNGAM_REBOARD_MIN).
        **단방향이다.** 본선 응암에서 순환 종료 노드로 가는 이동은 존재하지 않는다.
        """
        nodes = set(ride["from_node"]) | set(ride["to_node"])
        if EUNGAM_LOOP_END_NODE not in nodes or "6_응암" not in nodes:
            self.notes.append("응암 재승차 엣지: 노드가 없어 건너뜀")
            return agg
        row = {
            "from_node": EUNGAM_LOOP_END_NODE, "to_node": "6_응암",
            "station_name": "응암", "from_line": "6", "to_line": "6",
            "transfer_time_min": EUNGAM_REBOARD_MIN,
            "transfer_time_min_best": EUNGAM_REBOARD_MIN,
            "n_patterns": 1, "is_branch_transfer": 1,
            "weekday_volume": np.nan, "volume_penalty_min": 0.0,
            "transfer_penalty_min": EUNGAM_REBOARD_MIN,
            "edge_type": "transfer", "is_bidirectional": 0,
        }
        out = pd.concat([agg, pd.DataFrame([row])], ignore_index=True)
        self.notes.append(
            "응암 재승차 환승 엣지 추가: %s -> 6_응암 (%.1f분, 단방향). "
            "구산에서 온 승객이 역촌 방향으로 가려면 응암에서 다음 열차를 기다린다."
            % (EUNGAM_LOOP_END_NODE, EUNGAM_REBOARD_MIN))
        return out

    # ---------- 3. 연결성 검증 ----------
    @staticmethod
    def connectivity(edges: list[tuple[str, str]], nodes: set[str]) -> dict:
        adj = defaultdict(list)
        radj = defaultdict(list)
        for a, b in edges:
            adj[a].append(b)
            radj[b].append(a)

        def bfs(start, g):
            seen = {start}
            q = deque([start])
            while q:
                u = q.popleft()
                for v in g[u]:
                    if v not in seen:
                        seen.add(v)
                        q.append(v)
            return seen

        start = sorted(nodes)[0]
        fwd = bfs(start, adj)
        bwd = bfs(start, radj)
        deg = {n: len(adj[n]) + len(radj[n]) for n in nodes}
        return {
            "n_nodes": len(nodes),
            "reachable_from": len(fwd),
            "reaching_to": len(bwd),
            "strongly_connected": len(fwd) == len(nodes) and len(bwd) == len(nodes),
            "isolated": sorted([n for n in nodes if deg[n] == 0]),
            "start_node": start,
            "unreachable": sorted(nodes - fwd)[:20],
        }

    # ---------- 실행 ----------
    def run(self) -> None:
        self.load_masters()
        ride = self.build_ride_edges()
        transfer, tip = self.build_transfer_edges(ride)

        nodes = set(ride["from_node"]) | set(ride["to_node"])
        edges = list(zip(ride["from_node"], ride["to_node"]))
        edges += list(zip(transfer["from_node"], transfer["to_node"]))
        edges += list(zip(transfer["to_node"], transfer["from_node"]))
        conn = self.connectivity(edges, nodes)

        paths = {}
        for name, df in (("route_edge_mart", ride),
                         ("transfer_edge_mart", transfer),
                         ("transfer_tip_mart", tip)):
            try:
                p = self.mart / f"{name}.parquet"
                df.to_parquet(p, index=False)
            except Exception:
                p = self.mart / f"{name}.csv.gz"
                df.to_csv(p, index=False, encoding=ENC, compression="gzip")
            paths[name] = p

        rp = self.write_report(ride, transfer, tip, conn, paths)
        paths["report"] = rp
        self.print_summary(ride, transfer, tip, conn, paths)

    def write_report(self, ride, transfer, tip, conn, paths) -> Path:
        L = []
        A = L.append
        A("# route_graph 구축 리포트\n")
        A(f"- 노드: **{conn['n_nodes']}**")
        A(f"- 승차 엣지: **{len(ride)}** (단방향 {int((ride['is_bidirectional'] == 0).sum())})")
        A(f"- 환승 엣지: **{len(transfer)}**")
        A(f"- 환승 안내 패턴(방면별): {len(tip)}\n")

        A("## 연결성\n")
        A(f"- 시작 노드 `{conn['start_node']}` 에서 도달 가능: {conn['reachable_from']} / {conn['n_nodes']}")
        A(f"- 시작 노드로 도달 가능: {conn['reaching_to']} / {conn['n_nodes']}")
        A(f"- 강연결(strongly connected): **{conn['strongly_connected']}**")
        A(f"- 고립 노드: {conn['isolated'] if conn['isolated'] else '없음'}")
        if conn["unreachable"]:
            A(f"- 도달 불가 노드(최대 20): {conn['unreachable']}")
        A("")

        A("## 호선별 노드/엣지\n")
        t = (ride.groupby("line_id")
             .agg(nodes=("from_node", "nunique"), edges=("from_node", "size"),
                  mean_time_min=("travel_time_min", "mean"),
                  total_km=("distance_km", lambda s: s.sum() / 2))
             .round(2).reset_index())
        A(t.to_markdown(index=False))
        A("")

        A("## 단방향 엣지 (응암순환)\n")
        u = ride[ride["is_bidirectional"] == 0][["line_id", "from_station", "to_station",
                                                 "travel_time_min", "distance_km"]]
        A(u.to_markdown(index=False))
        A("")

        A("## 분기 계통 엣지\n")
        b = ride[ride["branch_code"] != "main"][["line_id", "from_station", "to_station",
                                                 "branch_code"]].drop_duplicates()
        A(b.to_markdown(index=False))
        A("")

        A("## 환승역 커버리지\n")
        tm = self.master / "transfer_station_master.csv"
        if tm.exists():
            master = pd.read_csv(tm)
            names = set(master["station_name"].map(canon))
            covered = set(transfer["station_name"])
            A(f"- 마스터 환승역 {len(names)}개 중 환승 엣지 보유: **{len(names & covered)}**")
            missing = sorted(names - covered)
            if missing:
                A(f"- 엣지 없음: {missing}")
                A("\n> 원본 환승 데이터(호차/문)가 커버하지 않는 역이다. "
                  "추정하지 말고 해당 환승쌍은 기본 도보시간으로 처리하거나 안내를 비활성화한다.")
        A("")

        A("## 환승 페널티 상위 10\n")
        top = (transfer.sort_values("transfer_penalty_min", ascending=False).head(10)
               [["station_name", "from_line", "to_line", "transfer_time_min",
                 "weekday_volume", "volume_penalty_min", "transfer_penalty_min"]])
        A(top.to_markdown(index=False))
        A("")

        if self.notes:
            A("## 처리 노트\n")
            for n in self.notes:
                A(f"- {n}")
            A("")
        if hasattr(self, "diff_sample"):
            A("## 역간거리 버전 간 불일치 샘플\n")
            A(self.diff_sample.to_markdown(index=False))
            A("")

        A("## 산출물\n")
        for k, v in paths.items():
            A(f"- `{k}` : {v}")

        p = self.report / "route_graph_report.md"
        p.write_text("\n".join(L), encoding=ENC)
        return p

    def print_summary(self, ride, transfer, tip, conn, paths) -> None:
        print("=" * 74)
        print(" 06_build_route_graph — 완료")
        print("=" * 74)
        print(f"  노드            : {conn['n_nodes']}")
        print(f"  승차 엣지       : {len(ride)} (단방향 {int((ride['is_bidirectional'] == 0).sum())})")
        print(f"  환승 엣지       : {len(transfer)}")
        print(f"  환승 안내 패턴  : {len(tip)}")
        print(f"  강연결          : {conn['strongly_connected']}")
        print(f"  고립 노드       : {conn['isolated'] if conn['isolated'] else '없음'}")
        if conn["unreachable"]:
            print(f"  도달 불가       : {conn['unreachable']}")
        print("\n[호선별]")
        print(ride.groupby("line_id").agg(nodes=("from_node", "nunique"),
                                          edges=("from_node", "size")).to_string())
        print("\n[단방향 엣지]")
        print(ride[ride["is_bidirectional"] == 0][
            ["line_id", "from_station", "to_station", "travel_time_min"]].to_string(index=False))
        print("\n[생성 파일]")
        for k, v in paths.items():
            print(f"  {k:20s} {v}")
        if self.notes:
            print("\n[노트]")
            for n in self.notes:
                print(f"  - {n}")
        print("=" * 74)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    args = ap.parse_args(argv)
    RouteGraphBuilder(Path(args.root)).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
