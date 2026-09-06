"""
15_build_headway_mart.py
========================
분석 완료된 **30분 단위 평균 배차간격** 파일을 프로젝트 마트로 가져온다.

이번 범위 (MVP)
---------------
원본 열차운행시각표를 다시 파싱해 특정 시각의 next departure 를 찾는 것이 아니다.
이미 계산된 30분 bin 평균 배차간격만 쓴다.

    headway_freq_min  = 30 / n_departures
    expected_wait_min = headway_freq_min / 2      (균등 도착 가정)

**환승 후 다음 노선 승차 전 대기시간에만** 사용한다.
최초 승차 전 대기시간은 계산하지 않는다(사용자가 이미 승강장에 도착하는 시점을
알 수 없고, 출발 시각 입력의 의미가 모호해지기 때문).

키 정규화
---------
    day_type   DAY -> weekday,  SAT -> saturday,  END -> sunday
    direction  UP/DOWN/IN/OUT -> up/down/inner/outer
    time_bin   "08:00-08:30" -> "08:00~08:30"   (프로젝트 표기와 통일)

폴백 사슬
---------
    1) station 단위 (line, station_name, direction, day_type, time_bin)
    2) section 단위 (line, section_name, direction, day_type, time_bin)
    3) line 단위    (line, direction, day_type, time_bin)
station 값이 없거나 미운행(n_departures=0)이면 다음 단계로 내려간다.

출력
----
    data/marts/headway_station_30min.parquet
    data/marts/headway_line_30min.parquet
    data/marts/headway_section_30min.parquet
    reports/data_quality/headway_import_report.md

사용법
------
    python scripts/15_build_headway_mart.py --root . --src <분석파일 폴더>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ENC = "utf-8-sig"

DAY_TYPE_MAP = {"DAY": "weekday", "SAT": "saturday", "END": "sunday"}
DIRECTION_MAP = {"UP": "up", "DOWN": "down", "IN": "inner", "OUT": "outer"}

# 역명 정규화. 프로젝트 station_master 와 표기를 맞춘다.
# (4호선 원본은 '총신대입구', 7호선은 '이수' 로 같은 역을 다르게 적는다)
STATION_ALIAS = {
    "총신대입구": "이수", "총신대입구(이수)": "이수",
    "신천": "잠실새내", "당고개": "불암산", "미아삼거리": "미아사거리",
    "뚝섬유원지": "자양", "동대문운동장": "동대문역사문화공원",
}


def canon_station(name) -> str:
    import re as _re
    s = _re.sub(r"\s+", "", str(name))
    if s in STATION_ALIAS:
        return STATION_ALIAS[s]
    s = _re.sub(r"\(.*?\)$", "", s)          # 올림픽공원(한국체대) -> 올림픽공원
    return STATION_ALIAS.get(s, s)


def load_mart(base: Path, name: str):
    for ext in (".parquet", ".csv.gz", ".csv"):
        p = base / (name + ext)
        if p.exists():
            return pd.read_parquet(p) if ext == ".parquet" else pd.read_csv(p)
    return None


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["line_id"] = out["line_id"].astype(str)
    out["day_type"] = out["day_type"].map(DAY_TYPE_MAP)
    out["direction"] = out["direction"].map(DIRECTION_MAP)
    out["time_bin"] = out["time_bin"].astype(str).str.replace("-", "~", regex=False)
    if "station_name" in out.columns:
        out["station_name_raw"] = out["station_name"]
        out["station_name"] = out["station_name"].map(canon_station)
    for c in ("n_departures", "headway_freq_min", "expected_wait_min"):
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    # section/line 집계 파일에는 n_departures 가 없다(평균 컬럼만 있다).
    if "n_departures" in out.columns:
        out["is_operating"] = (out["n_departures"].fillna(0) > 0).astype(int)
    else:
        col = next((c for c in ("avg_expected_wait_min", "avg_headway_freq_min")
                    if c in out.columns), None)
        for c in ("avg_headway_freq_min", "avg_expected_wait_min",
                  "median_headway_freq_min"):
            if c in out.columns:
                out[c] = pd.to_numeric(out[c], errors="coerce")
        out["is_operating"] = (out[col].notna().astype(int) if col else 1)
    return out


class HeadwayImporter:

    def __init__(self, root: Path, src: Path):
        self.root = root
        self.src = src
        self.marts = root / "data" / "marts"
        self.report = root / "reports" / "data_quality"
        for d in (self.marts, self.report):
            d.mkdir(parents=True, exist_ok=True)
        self.checks: list[dict] = []
        self.notes: list[str] = []

    def add(self, name, ok, detail=""):
        self.checks.append({"check": name, "status": "PASS" if ok else "FAIL",
                            "detail": str(detail)})

    def warn(self, name, detail):
        self.checks.append({"check": name, "status": "WARN", "detail": str(detail)})

    def find(self, keyword: str) -> Path | None:
        cands = sorted(self.src.glob("*%s*analyzed.csv" % keyword))
        return cands[0] if cands else None

    # ---------- 실행 ----------
    def run(self):
        stp = self.find("station_30min")
        if stp is None:
            print("필수 파일을 찾을 수 없습니다: *station_30min*analyzed.csv (%s)" % self.src)
            return 2
        station = normalize(pd.read_csv(stp))
        self.notes.append("station 파일: %s (%d행)" % (stp.name, len(station)))

        section = line = None
        p = self.find("section_30min")
        if p is not None:
            section = normalize(pd.read_csv(p))
            self.notes.append("section 파일: %s (%d행)" % (p.name, len(section)))
        p = self.find("line_30min")
        if p is not None:
            line = normalize(pd.read_csv(p))
            self.notes.append("line 파일: %s (%d행)" % (p.name, len(line)))

        # ---------- 검증 ----------
        self.add("day_type 매핑", station["day_type"].notna().all(),
                 str(sorted(station["day_type"].dropna().unique())))
        self.add("direction 매핑", station["direction"].notna().all(),
                 str(sorted(station["direction"].dropna().unique())))
        self.add("line_id 범위", set(station["line_id"]) <= set("12345678"),
                 str(sorted(set(station["line_id"]))))
        bad = station[(station["is_operating"] == 1)
                      & (~station["expected_wait_min"].between(0.1, 60))]
        self.add("운행 bin 의 expected_wait 가 0.1~60분", len(bad) == 0,
                 "이탈 %d행" % len(bad))
        n_off = int((station["is_operating"] == 0).sum())
        self.warn("미운행 bin", "%d행 (n_departures=0). 폴백 대상" % n_off)

        # 그래프 역과 대조
        ride = load_mart(self.marts, "route_edge_mart")
        if ride is not None:
            g = {(str(r.line_id), r.from_node.split("_", 1)[1].split("@")[0])
                 for r in ride.itertuples()}
            h = set(zip(station["line_id"], station["station_name"]))
            miss = sorted(g - h)
            self.add("그래프 역이 배차 데이터에 존재", len(miss) == 0,
                     "미포함 %d개 %s" % (len(miss), miss[:8]))
        else:
            self.warn("그래프 대조", "route_edge_mart 없음 → 건너뜀")

        # 시간대 커버리지
        self.notes.append("time_bin %d종 (%s ~ %s)"
                          % (station["time_bin"].nunique(),
                             sorted(station["time_bin"].unique())[0],
                             sorted(station["time_bin"].unique())[-1]))

        # ---------- 저장 ----------
        keep_st = ["line_id", "station_uid", "station_code", "station_name",
                   "station_name_raw",
                   "section_name", "day_type", "direction", "time_bin",
                   "n_departures", "headway_freq_min", "expected_wait_min",
                   "is_operating"]
        paths = {}
        paths["headway_station_30min"] = self.save(
            station[[c for c in keep_st if c in station.columns]], "headway_station_30min")
        if section is not None:
            keep = ["line_id", "section_name", "day_type", "direction", "time_bin",
                    "avg_headway_freq_min", "avg_expected_wait_min"]
            paths["headway_section_30min"] = self.save(
                section[[c for c in keep if c in section.columns]], "headway_section_30min")
        if line is not None:
            keep = ["line_id", "day_type", "direction", "time_bin",
                    "avg_headway_freq_min", "avg_expected_wait_min"]
            paths["headway_line_30min"] = self.save(
                line[[c for c in keep if c in line.columns]], "headway_line_30min")

        rep = self.write_report(station, section, line, paths)
        paths["report"] = rep

        chk = pd.DataFrame(self.checks)
        n_fail = int((chk.status == "FAIL").sum())
        print("=" * 86)
        print(" 15_build_headway_mart — 완료")
        print("=" * 86)
        print(chk.to_string(index=False))
        for n in self.notes:
            print("  - " + n)
        print("\n[평일 시간대별 평균 예상 대기시간(분)]")
        wd = station[(station["day_type"] == "weekday") & (station["is_operating"] == 1)]
        piv = (wd.groupby("line_id")["expected_wait_min"]
               .agg(평균="mean", 최소="min", 최대="max").round(2))
        print(piv.to_string())
        print("\n[생성 파일]")
        for k, v in paths.items():
            print("  %-26s %s" % (k, v))
        print("=" * 86)
        return 1 if n_fail else 0

    def save(self, df: pd.DataFrame, name: str) -> Path:
        try:
            p = self.marts / (name + ".parquet")
            df.to_parquet(p, index=False)
        except Exception:
            p = self.marts / (name + ".csv.gz")
            df.to_csv(p, index=False, encoding=ENC, compression="gzip")
        return p

    def write_report(self, station, section, line, paths) -> Path:
        L = []
        A = L.append
        A("# 배차간격 마트 임포트 리포트\n")
        A("30분 단위 평균 배차간격을 **환승 후 대기시간** 계산에만 사용한다.\n")
        A("```\nheadway_freq_min  = 30 / n_departures\n"
          "expected_wait_min = headway_freq_min / 2   (균등 도착 가정)\n```\n")
        A("## 검증\n")
        A(pd.DataFrame(self.checks).to_markdown(index=False))
        A("")
        A("## 평일 노선별 예상 대기시간(분)\n")
        wd = station[(station["day_type"] == "weekday") & (station["is_operating"] == 1)]
        A(wd.groupby("line_id")["expected_wait_min"]
          .agg(평균="mean", 중앙="median", 최소="min", 최대="max").round(2).to_markdown())
        A("")
        A("## 출퇴근 시간대 비교 (평일)\n")
        peak = wd[wd["time_bin"].isin(["08:00~08:30", "08:30~09:00",
                                       "18:00~18:30", "18:30~19:00"])]
        A(peak.groupby(["line_id", "time_bin"])["expected_wait_min"]
          .mean().unstack().round(2).to_markdown())
        A("")
        A("## 이번 MVP 의 범위와 한계\n")
        A("- **최초 승차 전 대기시간은 반영하지 않는다.** 사용자가 승강장에 도착하는 시점을"
          " 알 수 없어 출발 시각 입력의 의미가 흐려진다.")
        A("- 특정 시각의 실제 다음 열차 출발시각을 찾지 않는다. 30분 bin 평균이다.")
        A("- 균등 도착 가정(기댓값 = 배차간격/2)이며, 열차 지연·혼잡 시 승차 실패는"
          " 고려하지 않는다.")
        A("- 정밀 시각표 기반 next departure 계산은 다음 버전 과제로 남긴다.\n")
        A("## 산출물\n")
        for k, v in paths.items():
            A("- `%s` : %s" % (k, v))
        p = self.report / "headway_import_report.md"
        p.write_text("\n".join(L), encoding=ENC)
        return p


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--src", default="data/raw/headway",
                    help="분석 완료 CSV 가 있는 폴더")
    args = ap.parse_args(argv)
    src = Path(args.src)
    if not src.exists():
        cand = [p.parent for p in Path(args.root).rglob("*station_30min*analyzed.csv")]
        if not cand:
            print("분석 파일 폴더를 찾을 수 없습니다: %s" % src)
            return 2
        src = cand[0]
    return HeadwayImporter(Path(args.root), src).run()


if __name__ == "__main__":
    sys.exit(main())
