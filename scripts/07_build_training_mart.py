"""
07_build_training_mart.py
=========================
Model B 학습용 마트를 만든다. 타깃은 congestion_rate, 피처는 승하차 흐름과 역 속성이다.

조인 키 주의 (실측으로 발견한 결함)
-----------------------------------
두 마트의 station_uid 규칙이 다르다.
    혼잡도 마트 : "2_성수"  (line_id + 역명, 분기는 @branch 접미)
    승하차 마트 : "2_211"   (line_id + 역번호)
그대로 조인하면 매칭률 0% 가 된다. 여기서는 **(line_id, station_code)** 로 조인하고,
분기 계통 코드(9xxx)처럼 승하차에 없는 노드는 역명 기준 총량으로 보완한다.
장기적으로는 마스터에서 uid 규칙을 하나로 통일해야 한다.

누수 방지 설계 (가장 중요)
--------------------------
혼잡도 스냅샷은 분기별 평균 패턴이다. 여기에 '같은 기간의 승하차'를 그대로 붙이면
사실상 정답을 다른 형태로 넣는 셈이 될 수 있다. 그래서 두 가지를 지킨다.

1. 승하차 피처는 **스냅샷 날짜 직전 90일** 구간만 집계한다.
   실서비스에서도 예측 시점에 '직전 3개월 승하차'는 알 수 있으므로 운영 가능한 설계다.

2. split 은 04 에서 정한 스냅샷 단위 시간축 분할을 그대로 승계한다.
   추가로 **station holdout** 을 만든다. 학습에 한 번도 안 나온 역 20% 를 따로 뺀다.
   이 구간이 Model B 의 존재 이유를 검증하는 자리다.
   (역×방향×요일×시간대 평균 baseline 은 미관측 역에 대해 예측 자체가 불가능하다)

승하차 귀속 문제 대응
---------------------
환승역 4곳(신내·연신내·충무로3·까치산2)은 호선 귀속이 0 이다.
그래서 '호선 귀속량'과 '역 전체 총량'을 **둘 다** 피처로 넣고 모델이 고르게 한다.

시간 축 정합
------------
혼잡도는 30분 bin, 승하차는 1시간 bin 이다. 30분 bin 을 포함하는 시간 bin 에 매핑한다.
  05:30~06:00 -> before_06,  24:00~24:30 / 24:30~25:00 -> after_24

출력
----
    data/interim/ridership_station_hour.parquet   승하차 집계 캐시 (재사용)
    data/marts/congestion_training_mart.parquet
    reports/model/training_mart_report.md

사용법
------
    python scripts/07_build_training_mart.py --root .
    python scripts/07_build_training_mart.py --root . --rebuild-agg
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ENC = "utf-8-sig"
LOOKBACK_DAYS = 90
STATION_HOLDOUT_FRAC = 0.20
SEED = 42


def load_any(base: Path, name: str, **kw) -> pd.DataFrame:
    for ext in (".parquet", ".csv.gz", ".csv"):
        p = base / f"{name}{ext}"
        if p.exists():
            if ext == ".parquet":
                return pd.read_parquet(p, **kw)
            return pd.read_csv(p, **kw)
    raise FileNotFoundError(f"{name} 을 찾을 수 없습니다: {base}")


def bin_to_hour(bin_start_min: int) -> str:
    """30분 bin 시작(분) -> 승하차 hour_bin 코드"""
    h = bin_start_min // 60
    if h < 6:
        return "before_06"
    if h >= 24:
        return "after_24"
    return f"{h:02d}"


class TrainingMartBuilder:

    def __init__(self, root: Path, rebuild_agg: bool = False):
        self.root = root
        self.mart = root / "data" / "marts"
        self.master = root / "data" / "master"
        self.interim = root / "data" / "interim"
        self.report = root / "reports" / "model"
        for d in (self.interim, self.report):
            d.mkdir(parents=True, exist_ok=True)
        self.rebuild_agg = rebuild_agg
        self.notes: list[str] = []

    # ---------- 승하차 집계 (메모리 안전) ----------
    def build_ridership_agg(self) -> pd.DataFrame:
        cache = self.interim / "ridership_station_hour.parquet"
        cache_gz = self.interim / "ridership_station_hour.csv.gz"
        if not self.rebuild_agg:
            for p in (cache, cache_gz):
                if p.exists():
                    df = pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p)
                    if "station_code" not in df.columns:
                        df["station_code"] = pd.to_numeric(
                            df["station_uid"].astype(str).str.split("_").str[-1],
                            errors="coerce")
                    self.notes.append(f"승하차 집계 캐시 사용: {p.name} ({len(df):,}행)")
                    return df

        pq = self.mart / "ridership_hourly_mart.parquet"
        gz = self.mart / "ridership_hourly_mart.csv.gz"
        cols = ["date", "day_type", "line_id", "station_uid", "station_name",
                "direction", "hour_bin", "passenger_count"]

        parts = []
        if pq.exists():
            import pyarrow.parquet as pq_mod
            f = pq_mod.ParquetFile(pq)
            for batch in f.iter_batches(batch_size=2_000_000, columns=cols):
                parts.append(self._agg_chunk(batch.to_pandas()))
        elif gz.exists():
            for chunk in pd.read_csv(gz, usecols=cols, chunksize=2_000_000):
                parts.append(self._agg_chunk(chunk))
        else:
            raise FileNotFoundError("ridership_hourly_mart 이 없습니다. 01 을 먼저 실행하세요.")

        agg = (pd.concat(parts, ignore_index=True)
               .groupby(["ym", "day_type", "line_id", "station_uid", "station_name",
                         "direction", "hour_bin"], as_index=False)
               .agg(sum_cnt=("sum_cnt", "sum"), n_days=("n_days", "sum")))
        agg["mean_cnt"] = agg["sum_cnt"] / agg["n_days"]
        agg["station_code"] = pd.to_numeric(
            agg["station_uid"].astype(str).str.split("_").str[-1], errors="coerce")
        try:
            agg.to_parquet(cache, index=False)
            self.notes.append(f"승하차 집계 캐시 생성: {cache.name} ({len(agg):,}행)")
        except Exception:
            agg.to_csv(cache_gz, index=False, encoding=ENC, compression="gzip")
            self.notes.append(f"승하차 집계 캐시 생성: {cache_gz.name} ({len(agg):,}행)")
        return agg

    @staticmethod
    def _agg_chunk(df: pd.DataFrame) -> pd.DataFrame:
        df["ym"] = pd.to_datetime(df["date"]).dt.to_period("M").astype(str)
        g = (df.groupby(["ym", "day_type", "line_id", "station_uid", "station_name",
                         "direction", "hour_bin"], as_index=False)
             .agg(sum_cnt=("passenger_count", "sum"),
                  n_days=("date", "nunique")))
        return g

    # ---------- 룩백 집계 ----------
    def lookback_features(self, agg: pd.DataFrame, snapshots: list[str]) -> pd.DataFrame:
        """각 스냅샷에 대해 직전 LOOKBACK_DAYS 구간의 승하차 평균을 만든다."""
        agg = agg.copy()
        agg["ym_ts"] = pd.PeriodIndex(agg["ym"], freq="M").to_timestamp()

        out = []
        for snap in snapshots:
            end = pd.Timestamp(snap)
            start = end - timedelta(days=LOOKBACK_DAYS)
            sub = agg[(agg["ym_ts"] >= start.to_period("M").to_timestamp())
                      & (agg["ym_ts"] <= end.to_period("M").to_timestamp())]
            if sub.empty:
                continue
            w = (sub.groupby(["day_type", "line_id", "station_code", "station_name",
                              "direction", "hour_bin"], as_index=False)
                 .agg(cnt=("mean_cnt", "mean")))
            w["snapshot_date"] = snap
            out.append(w)
        if not out:
            raise RuntimeError("룩백 구간에 해당하는 승하차 데이터가 없습니다.")
        lb = pd.concat(out, ignore_index=True)

        wide = lb.pivot_table(
            index=["snapshot_date", "day_type", "line_id", "station_code",
                   "station_name", "hour_bin"],
            columns="direction", values="cnt").reset_index()
        wide = wide.rename(columns={"boarding": "boarding_cnt",
                                    "alighting": "alighting_cnt"})
        for c in ("boarding_cnt", "alighting_cnt"):
            if c not in wide.columns:
                wide[c] = np.nan
        wide["flow_total"] = wide["boarding_cnt"].fillna(0) + wide["alighting_cnt"].fillna(0)
        wide["boarding_ratio"] = wide["boarding_cnt"] / wide["flow_total"].replace(0, np.nan)
        wide["net_alighting_ratio"] = (
            (wide["alighting_cnt"].fillna(0) - wide["boarding_cnt"].fillna(0))
            / wide["flow_total"].replace(0, np.nan))

        # 역 전체 총량 (환승역 호선 귀속 비대칭 대응)
        station_tot = (wide.groupby(["snapshot_date", "day_type", "station_name", "hour_bin"],
                                    as_index=False)["flow_total"].sum()
                       .rename(columns={"flow_total": "station_flow_total"}))
        wide = wide.merge(station_tot,
                          on=["snapshot_date", "day_type", "station_name", "hour_bin"],
                          how="left")
        wide["line_share_in_station"] = (
            wide["flow_total"] / wide["station_flow_total"].replace(0, np.nan))
        return wide

    # ---------- 학습 마트 ----------
    def build(self) -> pd.DataFrame:
        cong = load_any(self.mart, "congestion_30min_mart")
        cong = cong[cong["is_operating"] == 1].copy()
        cong["line_id"] = cong["line_id"].astype(str)
        cong["hour_bin"] = cong["bin_start_min"].map(bin_to_hour)
        cong["snapshot_date"] = cong["snapshot_date"].astype(str)

        agg = self.build_ridership_agg()
        agg["line_id"] = agg["line_id"].astype(str)
        lb = self.lookback_features(agg, sorted(cong["snapshot_date"].unique()))

        # 조인 1: 호선 귀속 흐름
        cong["station_code"] = pd.to_numeric(cong["station_code"], errors="coerce")
        lb["station_code"] = pd.to_numeric(lb["station_code"], errors="coerce")
        m = cong.merge(
            lb.drop(columns=["station_name"]),
            on=["snapshot_date", "day_type", "line_id", "station_code", "hour_bin"],
            how="left")
        matched = int(m["flow_total"].notna().sum())
        self.notes.append(
            f"승하차 피처 매칭 {matched:,}/{len(m):,} ({matched / len(m) * 100:.1f}%)")

        # 조인 2: 분기 노드(9xxx)는 승하차에 없다 -> 역명 기준 총량으로 보완
        need = m["station_flow_total"].isna()
        if need.any():
            byname = (lb.groupby(["snapshot_date", "day_type", "station_name", "hour_bin"],
                                 as_index=False)["station_flow_total"].max())
            fix = m.loc[need, ["snapshot_date", "day_type", "station_name", "hour_bin"]].merge(
                byname, on=["snapshot_date", "day_type", "station_name", "hour_bin"], how="left")
            m.loc[need, "station_flow_total"] = fix["station_flow_total"].values
            self.notes.append(f"역명 기준 총량으로 보완한 행 {int(need.sum()):,}")

        # 파생 피처
        m["log_flow_total"] = np.log1p(m["flow_total"].fillna(0))
        m["log_station_flow"] = np.log1p(m["station_flow_total"].fillna(0))
        m["month"] = pd.to_datetime(m["snapshot_date"]).dt.month
        m["quarter"] = pd.to_datetime(m["snapshot_date"]).dt.quarter
        m["is_branch_row"] = m["is_branch_row"].astype(int)
        m["is_transfer_station"] = m["is_transfer_station"].fillna(0).astype(int)
        m["has_ridership"] = m["flow_total"].notna().astype(int)

        # station holdout
        rng = np.random.default_rng(SEED)
        uids = np.array(sorted(m["station_uid"].unique()))
        hold = set(rng.choice(uids, size=int(len(uids) * STATION_HOLDOUT_FRAC),
                              replace=False).tolist())
        m["is_station_holdout"] = m["station_uid"].isin(hold).astype(int)
        self.notes.append(
            f"station holdout: {len(hold)}/{len(uids)}개 역 "
            f"({int(m['is_station_holdout'].sum()):,}행)")

        cols = [
            # 키
            "snapshot_date", "split", "is_station_holdout",
            "line_id", "station_uid", "station_name", "station_code",
            "direction", "day_type", "time_bin", "time_bin_index",
            # 타깃
            "congestion_rate", "label_high", "label_extreme", "p95_train",
            # 피처 - 시간
            "bin_start_min", "is_am_peak", "is_pm_peak", "month", "quarter",
            # 피처 - 역 속성
            "branch_code", "is_branch_row", "is_transfer_station", "attribution_flag",
            # 피처 - 승하차 흐름
            "boarding_cnt", "alighting_cnt", "flow_total", "station_flow_total",
            "log_flow_total", "log_station_flow", "boarding_ratio",
            "net_alighting_ratio", "line_share_in_station", "has_ridership",
        ]
        return m[[c for c in cols if c in m.columns]]

    # ---------- 리포트 ----------
    def write_report(self, mart: pd.DataFrame, path: Path) -> Path:
        L = []
        A = L.append
        A("# congestion_training_mart 리포트\n")
        A(f"- 행: **{len(mart):,}** / 컬럼 {mart.shape[1]}")
        A(f"- 룩백 구간: 스냅샷 직전 **{LOOKBACK_DAYS}일**")
        A(f"- station holdout: {int(mart['is_station_holdout'].sum()):,}행\n")

        A("## split × station_holdout 교차\n")
        A(pd.crosstab(mart["split"], mart["is_station_holdout"]).to_markdown())
        A("")

        A("## 피처 결측률\n")
        na = (mart.isna().mean() * 100).round(2)
        na = na[na > 0].sort_values(ascending=False)
        A(na.to_frame("결측률(%)").to_markdown() if len(na) else "결측 없음")
        A("")

        A("## 타깃 분포 (split 별)\n")
        t = mart.groupby("split")["congestion_rate"].describe()[
            ["count", "mean", "50%", "max"]].round(2)
        A(t.to_markdown())
        A("")

        A("## 승하차 피처와 타깃의 상관 (참고)\n")
        num = ["log_flow_total", "log_station_flow", "boarding_ratio",
               "net_alighting_ratio", "line_share_in_station", "bin_start_min"]
        cor = mart[[c for c in num if c in mart.columns] + ["congestion_rate"]].corr()
        A(cor["congestion_rate"].drop("congestion_rate").round(3)
          .to_frame("corr").to_markdown())
        A("\n> 상관이 낮다고 쓸모없는 건 아니다. 혼잡도는 역·시간대 고정효과가 지배적이라"
          " 선형 상관은 약하게 나온다. 실제 기여는 08 의 feature importance 로 확인한다.")
        A("")

        if self.notes:
            A("## 처리 노트\n")
            for n in self.notes:
                A(f"- {n}")
        p = self.report / "training_mart_report.md"
        p.write_text("\n".join(L), encoding=ENC)
        return p

    def run(self) -> None:
        mart = self.build()
        try:
            out = self.mart / "congestion_training_mart.parquet"
            mart.to_parquet(out, index=False)
        except Exception:
            out = self.mart / "congestion_training_mart.csv.gz"
            mart.to_csv(out, index=False, encoding=ENC, compression="gzip")
        rep = self.write_report(mart, out)

        print("=" * 80)
        print(" 07_build_training_mart — 완료")
        print("=" * 80)
        for n in self.notes:
            print(f"  {n}")
        print(f"\n  행 : {len(mart):,} / 컬럼 {mart.shape[1]}")
        print("\n[split × station_holdout]")
        print(pd.crosstab(mart["split"], mart["is_station_holdout"]).to_string())
        print("\n[타깃 분포]")
        print(mart.groupby("split")["congestion_rate"].describe()[
            ["count", "mean", "50%", "max"]].round(2).to_string())
        print(f"\n  마트   : {out}")
        print(f"  리포트 : {rep}")
        print("=" * 80)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--rebuild-agg", action="store_true", help="승하차 집계 캐시 재생성")
    args = ap.parse_args(argv)
    TrainingMartBuilder(Path(args.root), args.rebuild_agg).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
