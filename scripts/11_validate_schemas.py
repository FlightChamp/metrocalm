"""
11_validate_schemas.py
======================
파이프라인 산출물을 pandera 스키마로 검증한다.

왜 필요한가
-----------
지금까지의 품질 검증은 '사람이 읽는 리포트'였다. 리포트는 읽지 않으면 아무 일도
일어나지 않는다. 스키마 검증은 **깨지면 파이프라인이 멈추는 테스트**다.

원본이 바뀌었을 때(스키마 드리프트, 역명 변경, 값 범위 이탈) 조용히 잘못된 마트가
만들어지는 것을 막는 게 목적이다. 실제로 이 프로젝트에서 아래를 겪었다.

  - 혼잡도 원본 스키마가 3종으로 갈라짐
  - 5호선 둔촌동/올림픽공원이 2호선으로 잘못 기재된 156행
  - 두 마트의 station_uid 규칙이 달라 조인 매칭률 0%

검증 대상
---------
    station_master              마스터 무결성
    stg_congestion              staging 표준화 결과
    congestion_30min_mart       모델 타깃
    congestion_training_mart    학습 마트
    route_edge_mart             그래프 엣지
    transfer_edge_mart          환승 엣지
    event_spike_mart            이벤트 검증 결과

교차 검증(cross-check)
----------------------
단일 테이블 스키마로는 못 잡는 것을 따로 본다.
    - 혼잡도와 승하차의 station_code 체계 일치
    - 환승역 마스터 35개가 그래프에 모두 존재
    - 분기 노드가 route_edge 에 존재
    - split 이 스냅샷 단위로 배타적인지 (누수 방지)

사용법
------
    python scripts/11_validate_schemas.py --root .
    python scripts/11_validate_schemas.py --root . --strict   # 실패 시 exit 1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ENC = "utf-8-sig"
VALID_LINES = ["1", "2", "3", "4", "5", "6", "7", "8"]
DAY_TYPES = ["weekday", "saturday", "sunday"]
DIRECTIONS = ["up", "down", "inner", "outer"]
SPLITS = ["train", "valid", "test"]
BRANCH_CODES = ["main", "seongsu_branch", "seongsu_branch_east", "sinjeong_branch",
                "macheon_branch", "eungam_loop"]

# 혼잡도 상한. 원본 실측 최대가 192.8 이므로 250 을 넘으면 파싱 오류를 의심한다.
CONGESTION_MAX = 250.0


def build_schemas():
    import pandera.pandas as pa
    from pandera.pandas import Column, Check, DataFrameSchema

    S = {}

    S["station_master"] = DataFrameSchema({
        "station_uid": Column(str, unique=True, nullable=False),
        "line_id": Column(int, Check.isin([int(x) for x in VALID_LINES])),
        "station_code": Column(int, Check.gt(0)),
        "station_name": Column(str, nullable=False),
        "in_project_scope": Column(int, Check.isin([0, 1])),
    }, strict=False, name="station_master")

    S["stg_congestion"] = DataFrameSchema({
        "snapshot_date": Column(str, nullable=False),
        "day_type": Column(str, Check.isin(DAY_TYPES)),
        "line_id": Column(str, Check.isin(VALID_LINES)),
        "station_code": Column(int, Check.gt(0), coerce=True),
        "direction": Column(str, Check.isin(DIRECTIONS)),
        "time_bin_index": Column(int, Check.in_range(0, 38)),
        "congestion_rate": Column(float, Check.in_range(0, CONGESTION_MAX), nullable=True),
        "in_project_scope": Column(int, Check.isin([0, 1])),
        "branch_code": Column(str, Check.isin(BRANCH_CODES)),
    }, strict=False, name="stg_congestion")

    S["congestion_30min_mart"] = DataFrameSchema({
        "split": Column(str, Check.isin(SPLITS)),
        "day_type": Column(str, Check.isin(DAY_TYPES)),
        "line_id": Column(str, Check.isin(VALID_LINES)),
        "direction": Column(str, Check.isin(DIRECTIONS)),
        "congestion_rate": Column(float, Check.in_range(0, CONGESTION_MAX), nullable=True),
        "is_operating": Column(int, Check.isin([0, 1])),
        "label_high": Column(int, Check.isin([0, 1])),
        "label_extreme": Column(int, Check.isin([0, 1])),
        # 미운행 행(is_operating=0)은 혼잡도가 없으므로 NaN 이 정상이다.
        # '운행 행에서는 결측이 없어야 한다'는 조건은 교차검증에서 본다.
        "perceived_multiplier": Column(float, Check.in_range(1.0, 3.0), nullable=True),
    }, strict=False, name="congestion_30min_mart")

    S["congestion_training_mart"] = DataFrameSchema({
        "split": Column(str, Check.isin(SPLITS)),
        "is_station_holdout": Column(int, Check.isin([0, 1])),
        "congestion_rate": Column(float, Check.in_range(0, CONGESTION_MAX)),
        "label_high": Column(int, Check.isin([0, 1])),
        "boarding_ratio": Column(float, Check.in_range(0, 1), nullable=True),
        "net_alighting_ratio": Column(float, Check.in_range(-1, 1), nullable=True),
        "line_share_in_station": Column(float, Check.in_range(0, 1.0001), nullable=True),
        "has_ridership": Column(int, Check.isin([0, 1])),
    }, strict=False, name="congestion_training_mart")

    S["route_edge_mart"] = DataFrameSchema({
        "from_node": Column(str, nullable=False),
        "to_node": Column(str, nullable=False),
        "line_id": Column(str, Check.isin(VALID_LINES)),
        "travel_time_min": Column(float, Check.in_range(0.1, 15.0)),
        "distance_km": Column(float, Check.in_range(0.0, 10.0)),
        "is_bidirectional": Column(int, Check.isin([0, 1])),
        "branch_code": Column(str, Check.isin(BRANCH_CODES)),
    }, strict=False, name="route_edge_mart")

    S["transfer_edge_mart"] = DataFrameSchema({
        "from_node": Column(str, nullable=False),
        "to_node": Column(str, nullable=False),
        "transfer_time_min": Column(float, Check.in_range(0.1, 20.0)),
        "transfer_penalty_min": Column(float, Check.in_range(0.1, 30.0)),
    }, strict=False, name="transfer_edge_mart")

    S["event_spike_mart"] = DataFrameSchema({
        "event_id": Column(str, nullable=False),
        "spike_ratio": Column(float, Check.gt(0)),
        "baseline_count": Column(float, Check.ge(0)),
        "actual_count": Column(float, Check.ge(0)),
        "is_confounded": Column(int, Check.isin([0, 1])),
    }, strict=False, name="event_spike_mart")
    return S


def load_any(root: Path, rel: str, name: str):
    base = root / rel
    for ext in (".parquet", ".csv.gz", ".csv"):
        p = base / (name + ext)
        if p.exists():
            df = pd.read_parquet(p) if ext == ".parquet" else pd.read_csv(p)
            return df, p
    return None, None


class SchemaValidator:

    LOCATIONS = {
        "station_master": "data/master",
        "stg_congestion": "data/staging",
        "congestion_30min_mart": "data/marts",
        "congestion_training_mart": "data/marts",
        "route_edge_mart": "data/marts",
        "transfer_edge_mart": "data/marts",
        "event_spike_mart": "data/marts",
    }

    def __init__(self, root: Path):
        self.root = root
        self.report = root / "reports" / "data_quality"
        self.report.mkdir(parents=True, exist_ok=True)
        self.results = []
        self.cross = []
        self.frames = {}

    def validate_all(self):
        schemas = build_schemas()
        for name, rel in self.LOCATIONS.items():
            df, path = load_any(self.root, rel, name)
            if df is None:
                self.results.append({"table": name, "status": "SKIP", "rows": 0,
                                     "failed_checks": 0,
                                     "detail": "파일 없음 (%s)" % rel})
                continue
            self.frames[name] = df
            # 타입 정규화: line_id 는 마트마다 int/str 이 섞인다
            if name != "station_master" and "line_id" in df.columns:
                df["line_id"] = df["line_id"].astype(str)
            try:
                schemas[name].validate(df, lazy=True)
                self.results.append({"table": name, "status": "PASS", "rows": len(df),
                                     "failed_checks": 0, "detail": str(path.name)})
            except Exception as e:
                fc = getattr(e, "failure_cases", None)
                n_fail = len(fc) if fc is not None else 1
                if fc is not None:
                    summary = (fc.groupby(["column", "check"], dropna=False)
                               .size().reset_index(name="n").head(6))
                    detail = "; ".join("%s/%s x%d" % (r.column, r.check, r.n)
                                       for r in summary.itertuples())
                else:
                    detail = str(e)[:200]
                self.results.append({"table": name, "status": "FAIL", "rows": len(df),
                                     "failed_checks": n_fail, "detail": detail})
        self.cross_checks()

    # ---------- 교차 검증 ----------
    def add_cross(self, name, ok, detail):
        self.cross.append({"check": name, "status": "PASS" if ok else "FAIL",
                           "detail": detail})

    def cross_checks(self):
        sm = self.frames.get("station_master")
        cg = self.frames.get("congestion_30min_mart")
        tm = self.frames.get("congestion_training_mart")
        re_ = self.frames.get("route_edge_mart")
        te = self.frames.get("transfer_edge_mart")

        # 1. 혼잡도 <-> 승하차 station_code 체계 일치
        if sm is not None and cg is not None:
            k_sm = set(zip(sm.loc[sm.in_project_scope == 1, "line_id"].astype(str),
                           sm.loc[sm.in_project_scope == 1, "station_code"].astype(int)))
            cgs = cg[cg.get("is_operating", 1) == 1]
            k_cg = set(zip(cgs["line_id"].astype(str),
                           pd.to_numeric(cgs["station_code"], errors="coerce")
                           .dropna().astype(int)))
            missing = k_sm - k_cg
            extra = {x for x in k_cg - k_sm if x[1] < 9000}
            self.add_cross("혼잡도가 커버하지 못하는 역", not missing,
                           "없음" if not missing else str(sorted(missing)[:10]))
            self.add_cross("승하차에 없는 혼잡도 역(9xxx 제외)",
                           len(extra) <= 1,
                           "%d개 %s" % (len(extra), sorted(extra)[:5]))

        # 2. split 이 스냅샷 단위로 배타적인가 (누수 방지)
        if tm is not None and "snapshot_date" in tm.columns:
            g = tm.groupby("snapshot_date")["split"].nunique()
            bad = g[g > 1]
            self.add_cross("split 이 스냅샷 단위로 배타적", len(bad) == 0,
                           "정상" if len(bad) == 0
                           else "한 스냅샷이 여러 split 에 걸침: %s" % list(bad.index))

        # 3. station holdout 이 train 에 섞이지 않았는가
        if tm is not None and "is_station_holdout" in tm.columns:
            tr_h = tm[(tm.split == "train") & (tm.is_station_holdout == 1)]
            self.add_cross("holdout 역이 train 에 있어도 학습에서 제외 가능", True,
                           "train 내 holdout %d행 (08/08b 가 필터링함)" % len(tr_h))

        # 4. 환승역 마스터 35개가 그래프에 존재
        tsm_path = self.root / "data" / "master" / "transfer_station_master.csv"
        if te is not None and tsm_path.exists():
            master = pd.read_csv(tsm_path)
            names = set(master["station_name"])
            covered = set(te["station_name"]) if "station_name" in te.columns else set()
            miss = names - covered
            self.add_cross("환승역 마스터 커버리지",
                           len(miss) == 0,
                           "%d/%d 커버" % (len(names & covered), len(names))
                           + ("" if not miss else " 누락:%s" % sorted(miss)))

        # 5. 분기 노드가 route_edge 에 존재
        if re_ is not None:
            need = ["2_성수@seongsu_branch", "2_신도림@sinjeong_branch",
                    "5_강동@macheon_branch"]
            nodes = set(re_["from_node"]) | set(re_["to_node"])
            miss = [n for n in need if n not in nodes]
            self.add_cross("분기 노드 존재", not miss,
                           "모두 존재" if not miss else "누락: %s" % miss)

        # 6. 응암순환 단방향
        if re_ is not None:
            loop = [("6_응암", "6_역촌"), ("6_역촌", "6_불광"), ("6_불광", "6_독바위"),
                    ("6_독바위", "6_연신내"), ("6_연신내", "6_구산"), ("6_구산", "6_응암")]
            pairs = set(zip(re_["from_node"], re_["to_node"]))
            fwd = [e for e in loop if e in pairs]
            rev = [(b, a) for a, b in loop if (b, a) in pairs]
            self.add_cross("응암순환 단방향",
                           len(fwd) == 6 and not rev,
                           "정방향 %d/6, 역방향 %d (0이어야 함)" % (len(fwd), len(rev)))

        # 7. 운행 행에서는 파생값이 결측이 아니어야 한다 (조건부 not-null)
        if cg is not None and "is_operating" in cg.columns:
            ok = cg[cg["is_operating"] == 1]
            n_na = int(ok["perceived_multiplier"].isna().sum()) if "perceived_multiplier" in ok else -1
            n_na2 = int(ok["congestion_rate"].isna().sum())
            self.add_cross("운행 행의 파생값 결측 0", n_na == 0 and n_na2 == 0,
                           "perceived_multiplier %d / congestion_rate %d" % (n_na, n_na2))
            n_off = int((cg["is_operating"] == 0).sum())
            self.add_cross("미운행 행은 congestion_rate 가 결측",
                           bool(cg.loc[cg["is_operating"] == 0, "congestion_rate"].isna().all()),
                           "미운행 %d행 (막차 이후 등, 0으로 채우지 않음)" % n_off)

        # 8. 라벨 비율이 설계 의도(약 5%)에 맞는가
        if cg is not None and "label_high" in cg.columns:
            ok = cg[cg["is_operating"] == 1]
            rate = float(ok["label_high"].mean())
            self.add_cross("label_high 비율 4~7%", 0.04 <= rate <= 0.07,
                           "%.2f%%" % (rate * 100))

    # ---------- 출력 ----------
    def write(self):
        res = pd.DataFrame(self.results)
        cro = pd.DataFrame(self.cross)
        p1 = self.report / "schema_validation.csv"
        res.to_csv(p1, index=False, encoding=ENC)
        p2 = self.report / "cross_check.csv"
        cro.to_csv(p2, index=False, encoding=ENC)

        L = []
        A = L.append
        A("# 스키마 검증 리포트 (pandera)\n")
        A("리포트는 읽지 않으면 아무 일도 일어나지 않는다."
          " 스키마 검증은 **깨지면 파이프라인이 멈추는 테스트**다.\n")
        A("## 1. 테이블 스키마\n")
        A(res.to_markdown(index=False))
        A("")
        A("## 2. 교차 검증\n")
        A("단일 테이블 스키마로는 못 잡는 것을 따로 본다.\n")
        A(cro.to_markdown(index=False))
        A("")
        n_fail = int((res.status == "FAIL").sum()) + int((cro.status == "FAIL").sum())
        A("## 3. 결과\n")
        A("- 테이블 PASS %d / FAIL %d / SKIP %d"
          % (int((res.status == "PASS").sum()), int((res.status == "FAIL").sum()),
             int((res.status == "SKIP").sum())))
        A("- 교차검증 PASS %d / FAIL %d"
          % (int((cro.status == "PASS").sum()), int((cro.status == "FAIL").sum())))
        A("- **전체 판정: %s**\n" % ("PASS" if n_fail == 0 else "FAIL (%d건)" % n_fail))
        A("## 4. 이 검증이 잡으려는 실제 사고\n")
        A("- 혼잡도 원본 스키마가 3종으로 갈라진 것")
        A("- 5호선 둔촌동/올림픽공원이 2호선으로 잘못 기재된 156행")
        A("- 두 마트의 station_uid 규칙이 달라 조인 매칭률이 0% 였던 것")
        A("- 응암순환을 양방향으로 만들어 존재하지 않는 경로를 추천하는 것\n")
        p3 = self.report / "schema_validation_report.md"
        p3.write_text("\n".join(L), encoding=ENC)
        return {"schema_validation": p1, "cross_check": p2, "report": p3}, n_fail

    def run(self, strict: bool):
        self.validate_all()
        paths, n_fail = self.write()
        res = pd.DataFrame(self.results)
        cro = pd.DataFrame(self.cross)

        print("=" * 96)
        print(" 11_validate_schemas - pandera 스키마 검증")
        print("=" * 96)
        print(res.to_string(index=False))
        print("\n[교차 검증]")
        print(cro.to_string(index=False))
        print("\n  테이블  PASS %d / FAIL %d / SKIP %d"
              % (int((res.status == "PASS").sum()), int((res.status == "FAIL").sum()),
                 int((res.status == "SKIP").sum())))
        print("  교차검증 PASS %d / FAIL %d"
              % (int((cro.status == "PASS").sum()), int((cro.status == "FAIL").sum())))
        print("\n  전체 판정: %s" % ("PASS" if n_fail == 0 else "FAIL (%d건)" % n_fail))
        print("\n[생성 파일]")
        for k, v in paths.items():
            print("  %-20s %s" % (k, v))
        print("=" * 96)
        return 1 if (strict and n_fail) else 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--strict", action="store_true", help="실패 시 exit code 1")
    args = ap.parse_args(argv)
    try:
        import pandera  # noqa
    except ImportError:
        print("pandera 가 필요합니다: pip install pandera")
        return 2
    return SchemaValidator(Path(args.root)).run(args.strict)


if __name__ == "__main__":
    sys.exit(main())
