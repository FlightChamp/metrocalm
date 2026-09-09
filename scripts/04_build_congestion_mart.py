"""
04_build_congestion_mart.py
===========================
여유로 서울 Phase 1 마무리 — stg_congestion 을 모델링/그래프에 바로 쓸 수 있는 mart 로 변환한다.

전제
----
    scripts/03_build_stg_congestion.py 실행 완료
    data/staging/stg_congestion.parquet (또는 .csv.gz)
    data/master/station_master.csv        (01_build_ridership_mart.py 산출)
    data/master/transfer_station_master.csv, branch_node_master.csv (bootstrap 산출)

이 스크립트가 결정하는 것 (감사 결과에 근거한 설계 판단)
-------------------------------------------------------
1. 미운행 bin 처리
   원본 결측 8,399셀은 대부분 24:00 이후 bin 이다. '혼잡도 0'과 '열차 없음'은 다른 사건이므로
   0으로 채우지 않고 is_operating=0 으로 표시하고 통계·학습에서 제외한다.

2. 고혼잡 라벨을 분위수로 정의
   범위 내 715,065셀 중 130% 이상은 0.087%, 150% 이상은 38셀(0.005%)뿐이다.
   절대 임계 라벨은 평가지표가 불안정해지므로 주 라벨을 분위수로 둔다.
     label_high    : 역×방향 내 상위 5% (p95) 초과            <- 주 지표
     label_extreme : congestion_rate >= 100                    <- 보조(절대 기준)

3. split 은 시간축 고정
   스냅샷은 11개뿐이고 같은 격자 셀의 값이 스냅샷 간 매우 유사하다.
   random split 은 사실상 정답 암기이므로 스냅샷 단위로 자른다.
     train : ~2025-03 (6개)   valid : 2025-06~2025-11 (3개)   test : 2026 (2개)

4. 그래프용 대표값
   경로 추천 edge cost 는 특정 스냅샷이 아니라 안정적인 대표 패턴이어야 한다.
   스냅샷 간 median 을 대표값으로 쓰고, 체감시간 배수로 환산한 컬럼을 함께 만든다.
     perceived_multiplier = 1 + KAPPA * max(0, congestion - C0) / 100

출력
----
    data/marts/congestion_30min_mart.parquet     모델 학습용 (셀 단위)
    data/marts/congestion_station_profile.csv    역·방향·요일유형 단위 요약 지표
    data/marts/congestion_edge_lookup.parquet    그래프 edge cost 용 대표값
    reports/data_quality/congestion_mart_report.md

사용법
------
    python scripts/04_build_congestion_mart.py --root .
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ENC = "utf-8-sig"

# 체감시간 환산 파라미터 (route_scoring_design.md 와 값을 공유해야 한다)
C0 = 80.0      # 이 혼잡도까지는 체감 페널티 없음
KAPPA = 0.5    # 혼잡 180% -> 체감 이동시간 1.5배

# 스냅샷 → split (시간축 고정)
SPLIT_RULES = [
    ("train", "1900-01-01", "2025-03-31"),
    ("valid", "2025-04-01", "2025-12-31"),
    ("test",  "2026-01-01", "2099-12-31"),
]

HIGH_QUANTILE = 0.95
EXTREME_THRESHOLD = 100.0
PEAK_DURATION_THRESHOLD = 80.0   # 피크 지속시간 산정 기준


class CongestionMartBuilder:

    def __init__(self, root: Path):
        self.root = root
        self.stg_dir = root / "data" / "staging"
        self.mart_dir = root / "data" / "marts"
        self.master_dir = root / "data" / "master"
        self.report_dir = root / "reports" / "data_quality"
        for d in (self.mart_dir, self.report_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.notes: list[str] = []

    # ---------- IO ----------
    def _load_staging(self) -> pd.DataFrame:
        pq = self.stg_dir / "stg_congestion.parquet"
        gz = self.stg_dir / "stg_congestion.csv.gz"
        if pq.exists():
            return pd.read_parquet(pq)
        if gz.exists():
            return pd.read_csv(gz)
        raise FileNotFoundError(
            f"stg_congestion 이 없습니다. 먼저 03_build_stg_congestion.py 를 실행하세요. ({self.stg_dir})")

    def _save(self, df: pd.DataFrame, name: str) -> Path:
        try:
            p = self.mart_dir / f"{name}.parquet"
            df.to_parquet(p, index=False)
        except Exception:
            p = self.mart_dir / f"{name}.csv.gz"
            df.to_csv(p, index=False, encoding=ENC, compression="gzip")
            self.notes.append(f"pyarrow 미설치로 {name} 을 csv.gz 로 저장했습니다.")
        return p

    @staticmethod
    def _assign_split(snapshot_date: pd.Series) -> pd.Series:
        out = pd.Series("train", index=snapshot_date.index)
        for name, lo, hi in SPLIT_RULES:
            out[(snapshot_date >= lo) & (snapshot_date <= hi)] = name
        return out

    # ---------- 1. 셀 단위 mart ----------
    def build_cell_mart(self, stg: pd.DataFrame) -> pd.DataFrame:
        df = stg[stg["in_project_scope"] == 1].copy()
        df["line_id"] = df["line_id"].astype(str)

        # 미운행 판정
        df["is_operating"] = df["congestion_rate"].notna().astype(int)

        # station_master 조인 (있으면 승하차 귀속 플래그까지 가져온다)
        sm_path = self.master_dir / "station_master.csv"
        if sm_path.exists():
            sm = pd.read_csv(sm_path)
            sm["line_id"] = sm["line_id"].astype(str)
            keep = [c for c in ["line_id", "station_code", "is_transfer_station",
                                "attribution_flag", "share_in_station"] if c in sm.columns]
            df = df.merge(sm[keep], on=["line_id", "station_code"], how="left")
            self.notes.append(
                f"station_master 조인: 미매칭 {int(df['is_transfer_station'].isna().sum()):,}행"
                " (분기 계통 코드 9xxx 는 원래 승하차에 없음)")
        else:
            df["is_transfer_station"] = np.nan
            df["attribution_flag"] = np.nan
            self.notes.append("station_master.csv 없음 → 환승/귀속 플래그 결측")

        df["is_transfer_station"] = df["is_transfer_station"].fillna(0).astype(int)
        df["attribution_flag"] = df["attribution_flag"].fillna("unknown")

        # split
        df["split"] = self._assign_split(df["snapshot_date"])

        # 분위수 라벨: 역×방향 단위 (train 구간에서만 임계 산출 → 누수 방지)
        grp = ["line_id", "station_code", "direction"]
        tr = df[(df["split"] == "train") & (df["is_operating"] == 1)]
        thr = (tr.groupby(grp)["congestion_rate"]
               .quantile(HIGH_QUANTILE).rename("p95_train").reset_index())
        df = df.merge(thr, on=grp, how="left")
        # train 에 없던 조합은 전체 분포로 보정
        global_p95 = float(tr["congestion_rate"].quantile(HIGH_QUANTILE))
        df["p95_train"] = df["p95_train"].fillna(global_p95)

        df["label_high"] = ((df["congestion_rate"] > df["p95_train"])
                            & (df["is_operating"] == 1)).astype(int)
        df["label_extreme"] = ((df["congestion_rate"] >= EXTREME_THRESHOLD)
                               & (df["is_operating"] == 1)).astype(int)

        # 체감시간 배수
        df["perceived_multiplier"] = 1.0 + KAPPA * np.clip(
            df["congestion_rate"] - C0, 0, None) / 100.0

        cols = ["snapshot_id", "snapshot_date", "split", "day_type", "is_holiday_labeled",
                "line_id", "station_code", "station_uid", "station_name",
                "branch_code", "is_branch_row", "is_transfer_station", "attribution_flag",
                "direction", "time_bin_index", "time_bin", "bin_start_min",
                "is_am_peak", "is_pm_peak",
                "congestion_rate", "is_operating", "p95_train",
                "label_high", "label_extreme", "perceived_multiplier"]
        return df[[c for c in cols if c in df.columns]]

    # ---------- 2. 역·방향 프로파일 ----------
    def build_station_profile(self, mart: pd.DataFrame) -> pd.DataFrame:
        ok = mart[mart["is_operating"] == 1]
        grp = ["line_id", "station_code", "station_uid", "station_name",
               "branch_code", "direction", "day_type"]

        agg = (ok.groupby(grp)["congestion_rate"]
               .agg(mean_congestion="mean", median_congestion="median",
                    max_congestion="max", p95_congestion=lambda s: s.quantile(0.95),
                    n_cells="size").reset_index())

        # 피크 시간대 = 스냅샷 평균이 가장 높은 bin
        bin_mean = (ok.groupby(grp + ["time_bin"])["congestion_rate"]
                    .mean().reset_index(name="bin_mean"))
        peak = (bin_mean.sort_values("bin_mean", ascending=False)
                .groupby(grp).head(1)
                .rename(columns={"time_bin": "peak_time_bin",
                                 "bin_mean": "peak_bin_mean"}))

        # 피크 지속시간 = 스냅샷 평균이 임계 이상인 bin 수 × 30분
        dur = (bin_mean.assign(over=lambda d: (d["bin_mean"] >= PEAK_DURATION_THRESHOLD).astype(int))
               .groupby(grp)["over"].sum().reset_index(name="n_bins_over80"))
        dur["peak_duration_min"] = dur["n_bins_over80"] * 30

        # 출퇴근 시간대 평균
        amp = (ok[ok["is_am_peak"] == 1].groupby(grp)["congestion_rate"]
               .mean().reset_index(name="am_peak_mean"))
        pmp = (ok[ok["is_pm_peak"] == 1].groupby(grp)["congestion_rate"]
               .mean().reset_index(name="pm_peak_mean"))

        prof = (agg.merge(peak, on=grp, how="left")
                .merge(dur, on=grp, how="left")
                .merge(amp, on=grp, how="left")
                .merge(pmp, on=grp, how="left"))
        for c in ["mean_congestion", "median_congestion", "max_congestion",
                  "p95_congestion", "peak_bin_mean", "am_peak_mean", "pm_peak_mean"]:
            prof[c] = prof[c].round(2)
        return prof.sort_values(["line_id", "station_code", "direction", "day_type"])

    # ---------- 3. 그래프 edge lookup ----------
    def build_edge_lookup(self, mart: pd.DataFrame) -> pd.DataFrame:
        ok = mart[mart["is_operating"] == 1]
        grp = ["line_id", "station_uid", "station_name", "branch_code",
               "direction", "day_type", "time_bin_index", "time_bin"]
        lk = (ok.groupby(grp)["congestion_rate"]
              .agg(congestion_median="median", congestion_p90=lambda s: s.quantile(0.90),
                   n_snapshots="size").reset_index())
        lk["perceived_multiplier"] = 1.0 + KAPPA * np.clip(
            lk["congestion_median"] - C0, 0, None) / 100.0
        lk["congestion_median"] = lk["congestion_median"].round(2)
        lk["congestion_p90"] = lk["congestion_p90"].round(2)
        lk["perceived_multiplier"] = lk["perceived_multiplier"].round(4)
        return lk

    # ---------- 4. 리포트 ----------
    def write_report(self, mart: pd.DataFrame, prof: pd.DataFrame,
                     lookup: pd.DataFrame, paths: dict) -> Path:
        ok = mart[mart["is_operating"] == 1]
        lines = []
        A = lines.append
        A("# congestion_30min_mart 생성 리포트\n")
        A(f"- 생성 셀 수: {len(mart):,} (운행 {len(ok):,} / 미운행 {len(mart) - len(ok):,})")
        A(f"- 역·방향·요일유형 조합: {len(prof):,}")
        A(f"- 그래프 lookup 행: {len(lookup):,}\n")

        A("## split 구성\n")
        s = (mart.groupby("split")
             .agg(snapshots=("snapshot_date", "nunique"), cells=("congestion_rate", "size"))
             .reset_index())
        A(s.to_markdown(index=False))
        A("\n> 스냅샷 단위 시간축 분할. 2026년 2개 스냅샷은 학습에 일절 사용하지 않는다.\n")

        A("## 라벨 분포\n")
        A(f"- label_high (역×방향 train p95 초과): {int(ok['label_high'].sum()):,} "
          f"({ok['label_high'].mean() * 100:.2f}%)")
        A(f"- label_extreme (>=100%): {int(ok['label_extreme'].sum()):,} "
          f"({ok['label_extreme'].mean() * 100:.3f}%)")
        A(f"- p95 임계 중앙값: {ok['p95_train'].median():.1f}%, "
          f"최소 {ok['p95_train'].min():.1f}% / 최대 {ok['p95_train'].max():.1f}%\n")

        A("## 평일 기준 최혼잡 구간 Top 15\n")
        top = (prof[prof["day_type"] == "weekday"]
               .sort_values("max_congestion", ascending=False).head(15)
               [["line_id", "station_name", "direction", "peak_time_bin",
                 "mean_congestion", "max_congestion", "peak_duration_min"]])
        A(top.to_markdown(index=False))
        A("")

        A("## 노선별 평일 평균 혼잡도\n")
        lm = (prof[prof["day_type"] == "weekday"]
              .groupby("line_id")
              .agg(mean_congestion=("mean_congestion", "mean"),
                   max_congestion=("max_congestion", "max"),
                   mean_peak_duration_min=("peak_duration_min", "mean"))
              .round(1).reset_index())
        A(lm.to_markdown(index=False))
        A("")

        A("## 요일유형별 평균\n")
        d = ok.groupby("day_type")["congestion_rate"].agg(["mean", "max", "size"]).round(2)
        A(d.to_markdown())
        A("")

        if self.notes:
            A("## 처리 노트\n")
            for n in self.notes:
                A(f"- {n}")
            A("")

        A("## 산출물\n")
        for k, v in paths.items():
            A(f"- `{k}` : {v}")

        p = self.report_dir / "congestion_mart_report.md"
        p.write_text("\n".join(lines), encoding=ENC)
        return p

    # ---------- 실행 ----------
    def run(self) -> None:
        stg = self._load_staging()
        mart = self.build_cell_mart(stg)
        prof = self.build_station_profile(mart)
        lookup = self.build_edge_lookup(mart)

        paths = {
            "congestion_30min_mart": self._save(mart, "congestion_30min_mart"),
            "congestion_edge_lookup": self._save(lookup, "congestion_edge_lookup"),
        }
        pp = self.mart_dir / "congestion_station_profile.csv"
        prof.to_csv(pp, index=False, encoding=ENC)
        paths["congestion_station_profile"] = pp
        rp = self.write_report(mart, prof, lookup, paths)
        paths["report"] = rp

        ok = mart[mart["is_operating"] == 1]
        print("=" * 74)
        print(" 04_build_congestion_mart — 완료")
        print("=" * 74)
        print(f"  셀 수            : {len(mart):,} (운행 {len(ok):,})")
        print(f"  split            : "
              + ", ".join(f"{k}={v:,}" for k, v in mart['split'].value_counts().items()))
        print(f"  label_high       : {int(ok['label_high'].sum()):,} "
              f"({ok['label_high'].mean() * 100:.2f}%)")
        print(f"  label_extreme    : {int(ok['label_extreme'].sum()):,} "
              f"({ok['label_extreme'].mean() * 100:.3f}%)")
        print(f"  역·방향 프로파일 : {len(prof):,}행")
        print(f"  edge lookup      : {len(lookup):,}행")
        print("\n[평일 최혼잡 Top 10]")
        top = (prof[prof["day_type"] == "weekday"]
               .sort_values("max_congestion", ascending=False).head(10)
               [["line_id", "station_name", "direction", "peak_time_bin",
                 "mean_congestion", "max_congestion", "peak_duration_min"]])
        print(top.to_string(index=False))
        print("\n[생성 파일]")
        for k, v in paths.items():
            print(f"  {k:28s} {v}")
        if self.notes:
            print("\n[노트]")
            for n in self.notes:
                print(f"  - {n}")
        print("=" * 74)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    args = ap.parse_args(argv)
    CongestionMartBuilder(Path(args.root)).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
