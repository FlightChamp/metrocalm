"""
05_build_event_spike_mart.py
============================
Model A — 이벤트가 실제 승하차 spike 로 나타나는지 48개월 데이터로 검증한다.

왜 필요한가
-----------
공식 이벤트 캘린더를 그대로 믿고 피처로 넣으면, 효과 없는 이벤트가 노이즈가 된다.
파일럿 검증에서 이미 확인된 사실이다.

  봄꽃축제 2023 : 공식 기간 0.93배(효과 없음) / 직전 주말 4.16배
  제야의 종     : 일 총량 1.08배(거의 없음)   / 21시 이후 7.22배

그래서 이 스크립트는 '이벤트가 있었다'가 아니라 '이벤트가 얼마나 튀었나'를 산출하고,
검증되지 않은 이벤트는 가중치 0 으로 내린다.

방법
----
  baseline    = 같은 역 · 같은 요일 · ±8주 중앙값 (다른 이벤트일 제외)
  spike_ratio = actual / baseline
  robust_z    = (actual − median) / (1.4826 × MAD)

표본이 작아 표준편차 기반 z 는 이벤트 자체에 오염되므로 MAD 를 쓴다.

교란요인
--------
불꽃축제와 홍익대 논술이 3년 연속 같은 날이다(2022-10-08, 2023-10-07, 2024-10-05).
2025-09-27 은 불꽃축제와 연세대 논술이 겹친다.
같은 날 같은 역에 두 이벤트가 걸리면 `confounding_event_ids` 에 기록하고,
효과 추정에서 제외한다.

출력
----
    data/marts/event_spike_mart.parquet        이벤트×날짜×역×방향 단위 spike
    data/master/event_strength_verified.csv    검증 결과로 확정한 event_strength
    reports/model/event_effect_report.md

사용법
------
    python scripts/05_build_event_spike_mart.py --root .
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ENC = "utf-8-sig"

BASELINE_WEEKS = 8          # ±8주 같은 요일
MIN_BASELINE_SAMPLES = 4    # 이보다 적으면 baseline 신뢰 불가
MAD_C = 1.4826

# spike_ratio -> event_strength
STRENGTH_BINS = [
    (3.0, "very_strong"),
    (2.0, "strong"),
    (1.5, "medium"),
    (1.2, "weak"),
]
MIN_Z_FOR_SIGNIFICANCE = 3.0

# 학습에서 제외할 이벤트.
# 배율이 크다고 다 쓸 수 있는 게 아니다. 반복되지 않는 단일 사건을 '연례 이벤트'로
# 학습하면 모델이 매년 그 배율을 예측한다.
EXCLUDED_EVENTS = {
    "HW_2022": ("2022-10-29 이태원 참사를 포함한 기간. 이태원 하차 5.73배(야간 8.18배)로 "
                "다른 해(1.3~1.6배)의 3~5배이며, 이듬해 급감은 사회적 자제의 결과다. "
                "반복되는 할로윈 패턴이 아니므로 학습에서 제외한다."),
}


def to_date(v) -> date | None:
    if pd.isna(v) or str(v).strip() in ("", "없음", "nan"):
        return None
    return datetime.strptime(str(v).strip()[:10], "%Y-%m-%d").date()


def daterange(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def parse_hour_bins(v) -> set[int] | None:
    """'20;21;22;23;00;01' -> {20,21,22,23,24}  (00/01 은 '24시 이후' bin 으로 접는다)"""
    if pd.isna(v) or not str(v).strip():
        return None
    out = set()
    for x in str(v).split(";"):
        x = x.strip()
        if not x:
            continue
        h = int(x)
        out.add(24 if h < 5 else h)
    return out or None


# --------------------------------------------------------------------------
class EventSpikeBuilder:

    def __init__(self, root: Path):
        self.root = root
        self.mart = root / "data" / "marts"
        self.master = root / "data" / "master"
        self.report = root / "reports" / "model"
        for d in (self.mart, self.report):
            d.mkdir(parents=True, exist_ok=True)
        self.notes: list[str] = []

    # ---------- 이벤트 로드 ----------
    def load_events(self) -> pd.DataFrame:
        rows = []

        ec = pd.read_csv(self.master / "event_calendar_master.csv")
        for r in ec.itertuples():
            anchors = [s for s in str(r.anchor_stations).split(";") if s]
            impacts = [s for s in str(r.impact_stations).split(";") if s and s != "nan"]
            s = to_date(r.impact_start_date) or to_date(r.official_start_date)
            e = to_date(r.impact_end_date) or to_date(r.official_end_date)
            if not s or not e:
                continue
            rows.append({
                "event_id": r.event_id, "event_type": r.event_type,
                "event_name": r.event_name, "start_date": s, "end_date": e,
                "peak_date": to_date(r.peak_date),
                "anchor_stations": anchors, "impact_stations": impacts,
                "hour_bins": parse_hour_bins(r.peak_hour_bins),
                "confidence": r.confidence, "source": "event_calendar",
            })

        ue = pd.read_csv(self.master / "university_event_master.csv")
        for r in ue.itertuples():
            s, e = to_date(r.exam_start_date), to_date(r.exam_end_date)
            if not s or not e:
                continue
            stations = [x for x in str(r.impact_stations).split(";") if x]
            rows.append({
                "event_id": r.event_id, "event_type": r.event_type,
                "event_name": f"{r.university} {r.academic_year}학년도 {r.event_type}",
                "start_date": s, "end_date": e, "peak_date": s,
                "anchor_stations": stations, "impact_stations": [],
                "hour_bins": None, "confidence": r.confidence, "source": "university",
            })

        ev = pd.DataFrame(rows)
        self.notes.append(f"이벤트 {len(ev)}건 로드 "
                          f"(캘린더 {int((ev['source'] == 'event_calendar').sum())} / "
                          f"대학 {int((ev['source'] == 'university').sum())})")
        return ev

    # ---------- 승하차 로드 ----------
    def load_ridership(self, stations: set[str]) -> pd.DataFrame:
        pq = self.mart / "ridership_hourly_mart.parquet"
        gz = self.mart / "ridership_hourly_mart.csv.gz"
        cols = ["date", "dow", "station_uid", "station_name", "direction",
                "hour_start", "passenger_count"]
        if pq.exists():
            try:
                df = pd.read_parquet(pq, columns=cols,
                                     filters=[("station_name", "in", sorted(stations))])
            except Exception:
                df = pd.read_parquet(pq, columns=cols)
                df = df[df["station_name"].isin(stations)]
        elif gz.exists():
            parts = []
            for chunk in pd.read_csv(gz, usecols=cols, chunksize=1_000_000):
                parts.append(chunk[chunk["station_name"].isin(stations)])
            df = pd.concat(parts, ignore_index=True)
        else:
            raise FileNotFoundError("ridership_hourly_mart 이 없습니다. 01 을 먼저 실행하세요.")
        df["date"] = pd.to_datetime(df["date"]).dt.date
        self.notes.append(f"승하차 {len(df):,}행 로드 (대상 역 {df['station_name'].nunique()}개)")
        return df

    # ---------- 집계 ----------
    @staticmethod
    def aggregate(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        daily = (df.groupby(["station_name", "direction", "date", "dow"], as_index=False)
                 ["passenger_count"].sum())
        hourly = (df.groupby(["station_name", "direction", "date", "dow", "hour_start"],
                             as_index=False)["passenger_count"].sum())
        return daily, hourly

    # ---------- 이벤트일 집합 ----------
    @staticmethod
    def event_day_index(ev: pd.DataFrame) -> dict[str, set[date]]:
        idx: dict[str, set[date]] = {}
        for r in ev.itertuples():
            for st in set(r.anchor_stations) | set(r.impact_stations):
                idx.setdefault(st, set()).update(daterange(r.start_date, r.end_date))
        return idx

    # ---------- spike 계산 ----------
    def compute(self, ev: pd.DataFrame, daily: pd.DataFrame,
                hourly: pd.DataFrame) -> pd.DataFrame:
        ev_days = self.event_day_index(ev)

        # (역, 방향) -> {date: value}
        dmap: dict[tuple[str, str], dict[date, float]] = {}
        for (st, dr), g in daily.groupby(["station_name", "direction"]):
            dmap[(st, dr)] = dict(zip(g["date"], g["passenger_count"]))

        hmap: dict[tuple[str, str], dict[date, float]] = {}
        for (st, dr, hs), g in hourly.groupby(["station_name", "direction", "hour_start"]):
            hmap[(st, dr, hs)] = dict(zip(g["date"], g["passenger_count"]))

        def baseline(series: dict[date, float], target: date, station: str):
            """같은 요일 ±8주 중앙값. 다른 이벤트일은 제외한다."""
            bad = ev_days.get(station, set())
            vals = []
            for k in range(1, BASELINE_WEEKS + 1):
                for d in (target - timedelta(weeks=k), target + timedelta(weeks=k)):
                    if d in bad:
                        continue
                    v = series.get(d)
                    if v is not None and not pd.isna(v):
                        vals.append(float(v))
            if len(vals) < MIN_BASELINE_SAMPLES:
                return None, None, None
            arr = np.array(vals)
            med = float(np.median(arr))
            mad = float(np.median(np.abs(arr - med)))
            return med, mad, len(arr)

        rows = []
        for r in ev.itertuples():
            targets = set(r.anchor_stations) | set(r.impact_stations)
            for st in targets:
                role = "anchor" if st in r.anchor_stations else "impact"
                for d in daterange(r.start_date, r.end_date):
                    for dr in ("boarding", "alighting"):
                        # 일 총량
                        series = dmap.get((st, dr))
                        if series is None or d not in series:
                            continue
                        actual = float(series[d])
                        med, mad, n = baseline(series, d, st)
                        if med is None or med <= 0:
                            continue
                        z = (actual - med) / (MAD_C * mad) if mad and mad > 0 else np.nan
                        rows.append({
                            "event_id": r.event_id, "event_type": r.event_type,
                            "event_name": r.event_name, "date": d,
                            "station_name": st, "station_role": role,
                            "direction": dr, "scope": "daily", "hour_bin": "all",
                            "actual_count": actual, "baseline_count": round(med, 1),
                            "spike_ratio": round(actual / med, 3),
                            "robust_z": round(z, 2) if not pd.isna(z) else np.nan,
                            "n_baseline": n, "confidence": r.confidence,
                            "is_peak_date": int(r.peak_date == d) if r.peak_date else 0,
                        })

                        # 시간대 한정 이벤트 (제야의 종 등)
                        if r.hour_bins:
                            act = 0.0
                            meds, mads, ns = 0.0, 0.0, []
                            ok = True
                            for hs in sorted(r.hour_bins):
                                hser = hmap.get((st, dr, hs))
                                if hser is None or d not in hser:
                                    continue
                                act += float(hser[d])
                                m, a, nn = baseline(hser, d, st)
                                if m is None:
                                    ok = False
                                    break
                                meds += m
                                mads += a
                                ns.append(nn)
                            if ok and meds > 0:
                                z2 = (act - meds) / (MAD_C * mads) if mads > 0 else np.nan
                                rows.append({
                                    "event_id": r.event_id, "event_type": r.event_type,
                                    "event_name": r.event_name, "date": d,
                                    "station_name": st, "station_role": role,
                                    "direction": dr, "scope": "peak_hours",
                                    "hour_bin": ";".join(str(h) for h in sorted(r.hour_bins)),
                                    "actual_count": act, "baseline_count": round(meds, 1),
                                    "spike_ratio": round(act / meds, 3),
                                    "robust_z": round(z2, 2) if not pd.isna(z2) else np.nan,
                                    "n_baseline": int(np.mean(ns)) if ns else 0,
                                    "confidence": r.confidence,
                                    "is_peak_date": int(r.peak_date == d) if r.peak_date else 0,
                                })

        spike = pd.DataFrame(rows)
        if spike.empty:
            return spike
        spike = self.mark_confounding(spike, ev)
        return spike

    # ---------- 교란요인 ----------
    @staticmethod
    def mark_confounding(spike: pd.DataFrame, ev: pd.DataFrame) -> pd.DataFrame:
        by_day: dict[tuple[date, str], set[str]] = {}
        for r in ev.itertuples():
            for st in set(r.anchor_stations) | set(r.impact_stations):
                for d in daterange(r.start_date, r.end_date):
                    by_day.setdefault((d, st), set()).add(r.event_id)
        spike["confounding_event_ids"] = [
            ";".join(sorted(by_day.get((d, st), set()) - {eid}))
            for d, st, eid in zip(spike["date"], spike["station_name"], spike["event_id"])
        ]
        spike["is_confounded"] = (spike["confounding_event_ids"] != "").astype(int)
        return spike

    # ---------- event_strength 확정 ----------
    def verify_strength(self, spike: pd.DataFrame) -> pd.DataFrame:
        clean = spike[(spike["is_confounded"] == 0) & (spike["station_role"] == "anchor")]
        if clean.empty:
            clean = spike[spike["station_role"] == "anchor"]

        best = (clean.sort_values("spike_ratio", ascending=False)
                .groupby(["event_id", "event_type", "event_name"], as_index=False)
                .first()[["event_id", "event_type", "event_name", "station_name",
                          "direction", "scope", "date", "spike_ratio", "robust_z",
                          "actual_count", "baseline_count"]])

        def strength(row):
            if pd.isna(row["robust_z"]) or row["robust_z"] < MIN_Z_FOR_SIGNIFICANCE:
                if row["spike_ratio"] < 1.2:
                    return "not_significant"
            for thr, label in STRENGTH_BINS:
                if row["spike_ratio"] >= thr:
                    return label
            return "not_significant"

        best["event_strength_verified"] = best.apply(strength, axis=1)
        best["use_weight"] = np.where(
            best["event_strength_verified"] == "not_significant", 0.0,
            best["event_strength_verified"].map(
                {"very_strong": 1.0, "strong": 0.7, "medium": 0.4, "weak": 0.2}).fillna(0.0))

        # 학습 제외 처리
        best["exclude_from_training"] = best["event_id"].isin(EXCLUDED_EVENTS).astype(int)
        best["exclude_reason"] = best["event_id"].map(
            {k: v[0] if isinstance(v, tuple) else v for k, v in EXCLUDED_EVENTS.items()}).fillna("")
        best.loc[best["exclude_from_training"] == 1, "use_weight"] = 0.0

        # 제외 이벤트가 있는 유형은 나머지 연도의 중앙값을 대표 배율로 남긴다
        for etype in best.loc[best["exclude_from_training"] == 1, "event_type"].unique():
            sub = best[(best["event_type"] == etype) & (best["exclude_from_training"] == 0)]
            if len(sub):
                self.notes.append(
                    f"{etype}: 제외 이벤트를 뺀 대표 배율(중앙값) "
                    f"{sub['spike_ratio'].median():.2f}배 — 이 값을 유형 기준 강도로 쓴다.")
        # 시간대 한정 이벤트 표시
        best["apply_scope"] = np.where(best["scope"] == "peak_hours",
                                       "peak_hours_only", "daily")
        return best.sort_values("spike_ratio", ascending=False)

    # ---------- 리포트 ----------
    def write_report(self, spike: pd.DataFrame, strength: pd.DataFrame,
                     paths: dict) -> Path:
        L = []
        A = L.append
        A("# event_spike_mart 검증 리포트\n")
        A(f"- spike 행: **{len(spike):,}**")
        A(f"- 검증 이벤트: **{len(strength)}**")
        A(f"- 교란요인 포함 행: {int(spike['is_confounded'].sum()):,}\n")

        A("## 검증 결과 — event_strength 확정\n")
        show = strength[["event_name", "station_name", "direction", "scope", "date",
                         "spike_ratio", "robust_z", "event_strength_verified",
                         "use_weight"]].head(30)
        A(show.to_markdown(index=False))
        A("")

        A("## 강도별 집계\n")
        A(strength["event_strength_verified"].value_counts().to_markdown())
        A("")

        A("## 유형별 최대 배율\n")
        t = (strength.groupby("event_type")
             .agg(n=("event_id", "size"), max_ratio=("spike_ratio", "max"),
                  median_ratio=("spike_ratio", "median")).round(2))
        A(t.to_markdown())
        A("")

        A("## 일 총량 vs 시간대 한정 비교 (제야의 종 유형)\n")
        nb = spike[spike["event_type"] == "new_year_bell"]
        if len(nb):
            cmp = (nb[nb["station_name"] == "종각"]
                   .groupby(["event_id", "scope", "direction"])["spike_ratio"]
                   .max().unstack("scope"))
            A(cmp.round(2).to_markdown())
            A("\n> 일 총량으로는 효과가 없어 보이지만 야간 시간대만 보면 강하게 튄다. "
              "일 총량 피처로 쓰면 이벤트가 사라진다.")
        A("")

        A("## 학습 제외 이벤트\n")
        ex = strength[strength["exclude_from_training"] == 1]
        if len(ex):
            for r in ex.itertuples():
                A(f"- **{r.event_name}** (배율 {r.spike_ratio}배) — {r.exclude_reason}")
        else:
            A("없음")
        A("")

        A("## 교란요인 (같은 날 두 이벤트)\n")
        cf = (spike[spike["is_confounded"] == 1]
              .groupby(["date", "station_name", "event_id", "confounding_event_ids"])
              .size().reset_index(name="rows").head(20))
        if len(cf):
            A(cf.to_markdown(index=False))
        else:
            A("없음.\n")
            A("> 날짜가 겹치는 이벤트는 있다(불꽃축제 ↔ 홍익대 논술 3년 연속 등). "
              "그러나 영향역이 겹치지 않는다. 불꽃축제는 여의나루·여의도·마포·공덕, "
              "홍익대 논술은 홍대입구·상수다. **역-날짜 단위로 보면 교란은 0건**이며, "
              "날짜 단위 경고는 과하다.")
        A("")

        if self.notes:
            A("## 처리 노트\n")
            for n in self.notes:
                A(f"- {n}")
            A("")
        A("## 산출물\n")
        for k, v in paths.items():
            A(f"- `{k}` : {v}")

        p = self.report / "event_effect_report.md"
        p.write_text("\n".join(L), encoding=ENC)
        return p

    # ---------- 실행 ----------
    def run(self) -> None:
        ev = self.load_events()
        stations = set()
        for r in ev.itertuples():
            stations |= set(r.anchor_stations) | set(r.impact_stations)
        ride = self.load_ridership(stations)
        missing = stations - set(ride["station_name"])
        if missing:
            self.notes.append(f"승하차에 없는 이벤트 역 {len(missing)}개: {sorted(missing)}")

        daily, hourly = self.aggregate(ride)
        spike = self.compute(ev, daily, hourly)
        if spike.empty:
            print("spike 계산 결과가 비어 있습니다. 이벤트 역/기간을 확인하세요.")
            return
        strength = self.verify_strength(spike)

        paths = {}
        try:
            p = self.mart / "event_spike_mart.parquet"
            spike.to_parquet(p, index=False)
        except Exception:
            p = self.mart / "event_spike_mart.csv.gz"
            spike.to_csv(p, index=False, encoding=ENC, compression="gzip")
        paths["event_spike_mart"] = p

        sp = self.master / "event_strength_verified.csv"
        strength.to_csv(sp, index=False, encoding=ENC)
        paths["event_strength_verified"] = sp
        paths["report"] = self.write_report(spike, strength, paths)

        print("=" * 84)
        print(" 05_build_event_spike_mart — 완료")
        print("=" * 84)
        for n in self.notes:
            print(f"  {n}")
        print(f"\n  spike 행        : {len(spike):,}")
        print(f"  검증 이벤트     : {len(strength)}")
        print(f"  교란요인 행     : {int(spike['is_confounded'].sum()):,}")
        print("\n[강도별]")
        print(strength["event_strength_verified"].value_counts().to_string())
        print("\n[최대 배율 상위 15]")
        print(strength[["event_name", "station_name", "direction", "scope",
                        "spike_ratio", "robust_z", "event_strength_verified"]]
              .head(15).to_string(index=False))
        print("\n[생성 파일]")
        for k, v in paths.items():
            print(f"  {k:26s} {v}")
        print("=" * 84)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    args = ap.parse_args(argv)
    EventSpikeBuilder(Path(args.root)).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
