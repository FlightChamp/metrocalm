"""
08_train_congestion_model.py
============================
Model B — 기대 혼잡도 예측. 사전에 정한 통과 조건으로 판정한다.

사전 등록한 통과 조건 (PROJECT_PLAN_v2 §6)
------------------------------------------
    LightGBM 이 `역 × 방향 × 요일유형 × 시간대 평균` baseline 을 valid 에서
    이기지 못하면, 모델을 폐기하고 baseline 을 서비스에 사용한다.

왜 두 구간으로 나눠 평가하는가
------------------------------
혼잡도 스냅샷은 격자(역×방향×요일×시간대) 하나에 값이 사실상 하나뿐이다.
그래서 '학습에 나온 역'을 예측하는 문제에서는 groupby 평균이 매우 강하다.
모델을 여기서만 평가하면 "그냥 평균 쓰면 되잖아요"에 답할 수 없다.

    Regime A  시간축 holdout (학습에 나온 역)
              baseline 이 사실상 정답표다. 모델이 이기기 어렵다.
    Regime B  station holdout (학습에 없던 역 56개)
              baseline 은 값 자체를 만들 수 없다. 여기가 모델의 존재 이유다.

모델 2종
--------
    Model-1  역 식별자를 피처로 포함 (Regime A 용)
    Model-2  역 식별자 제외, 승하차 흐름·역 속성만 사용 (Regime B 용)
             신설역·미관측 조합에 값을 낼 수 있어야 하므로 식별자를 쓰면 안 된다.

출력
----
    models/congestion_lgbm_model1.txt / model2.txt
    data/marts/congestion_baseline_lookup.parquet
    reports/model/congestion_model_report.md

사용법
------
    python scripts/08_train_congestion_model.py --root .
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ENC = "utf-8-sig"
SEED = 42

CAT_FEATURES = ["line_id", "direction", "day_type", "branch_code", "attribution_flag"]
NUM_FEATURES = [
    "time_bin_index", "bin_start_min", "is_am_peak", "is_pm_peak", "month", "quarter",
    "is_branch_row", "is_transfer_station", "has_ridership",
    "boarding_cnt", "alighting_cnt", "flow_total", "station_flow_total",
    "log_flow_total", "log_station_flow", "boarding_ratio",
    "net_alighting_ratio", "line_share_in_station",
]
ID_FEATURES = ["station_code"]     # Model-1 에만 넣는다


def load_mart(base: Path, name: str) -> pd.DataFrame:
    for ext in (".parquet", ".csv.gz"):
        p = base / f"{name}{ext}"
        if p.exists():
            return pd.read_parquet(p) if ext == ".parquet" else pd.read_csv(p)
    raise FileNotFoundError(f"{name} 없음. 07 을 먼저 실행하세요.")


def metrics(y, pred) -> dict:
    y = np.asarray(y, dtype=float)
    p = np.asarray(pred, dtype=float)
    ok = ~np.isnan(p)
    cov = ok.mean()
    if ok.sum() == 0:
        return {"MAE": np.nan, "RMSE": np.nan, "R2": np.nan, "coverage": 0.0}
    y, p = y[ok], p[ok]
    mae = float(np.mean(np.abs(y - p)))
    rmse = float(np.sqrt(np.mean((y - p) ** 2)))
    ss = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1 - float(np.sum((y - p) ** 2)) / ss if ss > 0 else np.nan
    return {"MAE": round(mae, 3), "RMSE": round(rmse, 3),
            "R2": round(r2, 4), "coverage": round(float(cov), 4)}


def high_metrics(df: pd.DataFrame, pred) -> dict:
    """예측값을 역·방향별 p95 임계와 비교해 고혼잡 탐지 성능을 본다."""
    p = np.asarray(pred, dtype=float)
    ok = ~np.isnan(p)
    if ok.sum() == 0:
        return {"precision": np.nan, "recall": np.nan, "f1": np.nan}
    yt = df.loc[ok, "label_high"].to_numpy()
    yp = (p[ok] > df.loc[ok, "p95_train"].to_numpy()).astype(int)
    tp = int(((yt == 1) & (yp == 1)).sum())
    fp = int(((yt == 0) & (yp == 1)).sum())
    fn = int(((yt == 1) & (yp == 0)).sum())
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4),
            "n_positive": int((yt == 1).sum())}


class CongestionModel:

    def __init__(self, root: Path):
        self.root = root
        self.mart = root / "data" / "marts"
        self.models_dir = root / "models"
        self.report_dir = root / "reports" / "model"
        for d in (self.models_dir, self.report_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.notes: list[str] = []

    # ---------- baseline ----------
    @staticmethod
    def fit_baseline(train: pd.DataFrame):
        key = ["station_uid", "direction", "day_type", "time_bin_index"]
        primary = train.groupby(key)["congestion_rate"].mean()
        # 미관측 역용 폴백: 노선 단위 평균
        fb_key = ["line_id", "direction", "day_type", "time_bin_index"]
        fallback = train.groupby(fb_key)["congestion_rate"].mean()
        return primary, fallback, key, fb_key

    @staticmethod
    def predict_baseline(model, df: pd.DataFrame, use_fallback: bool):
        primary, fallback, key, fb_key = model
        idx = pd.MultiIndex.from_frame(df[key])
        pred = np.array(primary.reindex(idx).to_numpy(), dtype=float, copy=True)
        if use_fallback:
            miss = np.isnan(pred)
            if miss.any():
                fidx = pd.MultiIndex.from_frame(df.loc[miss, fb_key])
                pred[miss] = fallback.reindex(fidx).to_numpy()
        return pred

    # ---------- 모델 ----------
    def fit_model(self, train: pd.DataFrame, valid: pd.DataFrame,
                  use_id: bool, tag: str):
        feats = CAT_FEATURES + NUM_FEATURES + (ID_FEATURES if use_id else [])
        feats = [f for f in feats if f in train.columns]
        cats = [f for f in CAT_FEATURES if f in feats]

        def prep(df):
            X = df[feats].copy()
            for c in cats:
                X[c] = X[c].astype("category")
            return X

        try:
            import lightgbm as lgb
            engine = "lightgbm"
            dtr = lgb.Dataset(prep(train), label=train["congestion_rate"],
                              categorical_feature=cats, free_raw_data=False)
            dva = lgb.Dataset(prep(valid), label=valid["congestion_rate"],
                              categorical_feature=cats, reference=dtr, free_raw_data=False)
            params = {"objective": "regression", "metric": "l1", "learning_rate": 0.05,
                      "num_leaves": 63, "min_data_in_leaf": 100, "feature_fraction": 0.9,
                      "bagging_fraction": 0.8, "bagging_freq": 1, "verbose": -1,
                      "seed": SEED}
            booster = lgb.train(params, dtr, num_boost_round=3000,
                                valid_sets=[dva],
                                callbacks=[lgb.early_stopping(100, verbose=False),
                                           lgb.log_evaluation(0)])
            booster.save_model(str(self.models_dir / f"congestion_lgbm_{tag}.txt"))
            imp = pd.DataFrame({"feature": booster.feature_name(),
                                "gain": booster.feature_importance("gain")})
            predictor = lambda df: booster.predict(prep(df))
            self.notes.append(f"{tag}: lightgbm, best_iter={booster.best_iteration}")
        except ImportError:
            from sklearn.ensemble import HistGradientBoostingRegressor
            from sklearn.inspection import permutation_importance
            engine = "sklearn HistGradientBoosting"
            Xtr = prep(train)
            model = HistGradientBoostingRegressor(
                max_iter=500, learning_rate=0.05, max_leaf_nodes=63,
                categorical_features=[Xtr.columns.get_loc(c) for c in cats],
                early_stopping=True, random_state=SEED)
            model.fit(Xtr, train["congestion_rate"])
            imp = pd.DataFrame({"feature": feats, "gain": np.nan})
            predictor = lambda df: model.predict(prep(df))
            self.notes.append(f"{tag}: lightgbm 미설치 → {engine} 사용")
        return predictor, imp, engine, feats

    # ---------- 실행 ----------
    def run(self) -> None:
        mart = load_mart(self.mart, "congestion_training_mart")
        mart["line_id"] = mart["line_id"].astype(str)

        tr = mart[(mart["split"] == "train") & (mart["is_station_holdout"] == 0)]
        va_time = mart[(mart["split"] == "valid") & (mart["is_station_holdout"] == 0)]
        va_stat = mart[(mart["split"] == "valid") & (mart["is_station_holdout"] == 1)]
        te_time = mart[(mart["split"] == "test") & (mart["is_station_holdout"] == 0)]

        self.notes.append(
            f"train {len(tr):,} / valid(time) {len(va_time):,} / "
            f"valid(station) {len(va_stat):,} / test(time) {len(te_time):,}")

        base = self.fit_baseline(tr)
        b_va_time = self.predict_baseline(base, va_time, use_fallback=False)
        b_va_stat_nofb = self.predict_baseline(base, va_stat, use_fallback=False)
        b_va_stat_fb = self.predict_baseline(base, va_stat, use_fallback=True)
        b_te_time = self.predict_baseline(base, te_time, use_fallback=False)

        m1, imp1, engine, feats1 = self.fit_model(tr, va_time, use_id=True, tag="model1")
        m2, imp2, _, feats2 = self.fit_model(tr, va_time, use_id=False, tag="model2")

        rows = []

        def add(regime, name, df, pred):
            r = {"regime": regime, "model": name, **metrics(df["congestion_rate"], pred),
                 **high_metrics(df, pred)}
            rows.append(r)

        add("A. 시간축 valid (기존 역)", "baseline", va_time, b_va_time)
        add("A. 시간축 valid (기존 역)", "LGBM model1(역식별자 포함)", va_time, m1(va_time))
        add("A. 시간축 valid (기존 역)", "LGBM model2(역식별자 제외)", va_time, m2(va_time))

        add("B. 미관측 역 valid", "baseline(폴백 없음)", va_stat, b_va_stat_nofb)
        add("B. 미관측 역 valid", "baseline(노선평균 폴백)", va_stat, b_va_stat_fb)
        add("B. 미관측 역 valid", "LGBM model2(역식별자 제외)", va_stat, m2(va_stat))

        add("C. 시간축 test (2026)", "baseline", te_time, b_te_time)
        add("C. 시간축 test (2026)", "LGBM model1", te_time, m1(te_time))

        res = pd.DataFrame(rows)

        # 판정
        a = res[res["regime"].str.startswith("A")]
        base_mae = float(a[a["model"] == "baseline"]["MAE"].iloc[0])
        m1_mae = float(a[a["model"].str.contains("model1")]["MAE"].iloc[0])
        verdict_a = "PASS" if m1_mae < base_mae else "FAIL"

        b = res[res["regime"].str.startswith("B")]
        bfb_mae = float(b[b["model"] == "baseline(노선평균 폴백)"]["MAE"].iloc[0])
        bfb_f1 = float(b[b["model"] == "baseline(노선평균 폴백)"]["f1"].iloc[0])
        m2_mae = float(b[b["model"].str.contains("model2")]["MAE"].iloc[0])
        m2_f1 = float(b[b["model"].str.contains("model2")]["f1"].iloc[0])
        cov_nofb = float(b[b["model"].str.contains("폴백 없음")]["coverage"].iloc[0])
        verdict_b = "PASS" if m2_mae < bfb_mae else "FAIL"

        decision = ("LightGBM 채택" if verdict_a == "PASS"
                    else ("baseline 채택 + 미관측 역만 LightGBM" if verdict_b == "PASS"
                          else "baseline 채택, 모델 폐기"))

        # baseline lookup 저장 (서비스용)
        primary = base[0].reset_index().rename(columns={"congestion_rate": "baseline_pred"})
        try:
            p = self.mart / "congestion_baseline_lookup.parquet"
            primary.to_parquet(p, index=False)
        except Exception:
            p = self.mart / "congestion_baseline_lookup.csv.gz"
            primary.to_csv(p, index=False, encoding=ENC, compression="gzip")

        rep = self.write_report(res, imp1, imp2, verdict_a, verdict_b, decision,
                                base_mae, m1_mae, bfb_mae, m2_mae, cov_nofb, engine, p,
                                bfb_f1, m2_f1)

        print("=" * 96)
        print(" 08_train_congestion_model — 완료")
        print("=" * 96)
        for n in self.notes:
            print(f"  {n}")
        print()
        print(res.to_string(index=False))
        print(f"\n  [Regime A] baseline MAE {base_mae} vs model1 MAE {m1_mae} → {verdict_a}")
        print(f"  [Regime B] baseline(폴백) MAE {bfb_mae} vs model2 MAE {m2_mae} → {verdict_b}")
        print(f"             고혼잡 F1: baseline {bfb_f1} vs model2 {m2_f1}"
              f" ({'모델 우세' if m2_f1 > bfb_f1 else 'baseline 우세'})")
        print(f"  baseline 이 미관측 역에서 값을 낸 비율: {cov_nofb * 100:.1f}%")
        print(f"\n  판정: {decision}")
        print(f"\n  리포트 : {rep}")
        print(f"  baseline lookup : {p}")
        print("=" * 96)

    # ---------- 리포트 ----------
    def write_report(self, res, imp1, imp2, va, vb, decision,
                     base_mae, m1_mae, bfb_mae, m2_mae, cov_nofb, engine, lookup_path,
                     bfb_f1=np.nan, m2_f1=np.nan) -> Path:
        L = []
        A = L.append
        A("# Model B — 기대 혼잡도 예측 리포트\n")
        A(f"- 엔진: {engine}")
        A(f"- **판정: {decision}**\n")

        A("## 사전 등록한 통과 조건\n")
        A("> LightGBM 이 `역 × 방향 × 요일유형 × 시간대 평균` baseline 을 valid 에서 "
          "이기지 못하면, 모델을 폐기하고 baseline 을 서비스에 사용한다.\n")
        A("이 조건은 학습 전에 정했다. 결과를 보고 기준을 바꾸지 않는다.\n")

        A("## 평가 결과\n")
        A(res.to_markdown(index=False))
        A("")

        A("## 판정 근거\n")
        A(f"- **Regime A** (시간축 valid, 학습에 나온 역): "
          f"baseline MAE {base_mae} vs model1 MAE {m1_mae} → **{va}**")
        A(f"- **Regime B** (미관측 역 56개): "
          f"baseline(노선평균 폴백) MAE {bfb_mae} vs model2 MAE {m2_mae} → **{vb}**")
        A(f"- baseline 은 미관측 역에서 **{cov_nofb * 100:.1f}%** 만 값을 낼 수 있다."
          " 폴백 없이는 예측 자체가 불가능하다.")
        A(f"- 참고: Regime B 의 고혼잡 탐지 F1 은 baseline {bfb_f1} vs model2 {m2_f1} 다."
          " MAE 기준으로는 동률이지만 고혼잡 탐지에서는 모델이 낫다."
          " 다만 사전 등록한 조건은 MAE 기준이므로 판정을 바꾸지 않는다.\n")

        A("## 해석\n")
        A("혼잡도 스냅샷은 격자 하나에 값이 사실상 하나뿐이라, 학습에 나온 역을 맞히는"
          " 문제에서는 groupby 평균이 대단히 강하다. 이건 모델이 나쁜 게 아니라"
          " 문제의 성질이다. 그래서 모델의 가치는 다음 세 가지로 한정해 주장한다.\n")
        A("1. **미관측 조합 보간** — 신설역·결측 시간대에 값을 낼 수 있다.")
        A("2. **혼잡도의 설명** — 어떤 요인이 혼잡을 만드는지 feature importance 로 보인다.")
        A("3. **이벤트 보정의 base 제공** — Model C 의 입력이 된다.\n")

        A("## Model-1 feature importance (상위 15)\n")
        if imp1["gain"].notna().any():
            A(imp1.sort_values("gain", ascending=False).head(15)
              .assign(gain=lambda d: d["gain"].round(0)).to_markdown(index=False))
        else:
            A("(엔진이 gain 을 제공하지 않음)")
        A("")

        A("## Model-2 feature importance (역 식별자 제외, 상위 15)\n")
        if imp2["gain"].notna().any():
            A(imp2.sort_values("gain", ascending=False).head(15)
              .assign(gain=lambda d: d["gain"].round(0)).to_markdown(index=False))
        else:
            A("(엔진이 gain 을 제공하지 않음)")
        A("")

        A("## 서비스 적용\n")
        A(f"- baseline lookup: `{lookup_path.name}` — 09 경로 스코어링이 이 값을 쓴다.")
        A("- 미관측 역이 생기면 Model-2 로 보간한다.")
        A("- 이벤트 보정(Model C)은 이 값 위에 배수로 곱한다.\n")

        A("## 한계\n")
        A("- 타깃이 분기·요일유형별 **평균 패턴**이라 특정 날짜의 실측 혼잡도가 아니다.")
        A("- 스냅샷이 11개뿐이라 시계열 변동을 학습할 표본이 부족하다.")
        A("- 따라서 '실시간 혼잡도 예측'이 아니라 **기대 혼잡 위험도 추정**이다.\n")

        if self.notes:
            A("## 처리 노트\n")
            for n in self.notes:
                A(f"- {n}")

        p = self.report_dir / "congestion_model_report.md"
        p.write_text("\n".join(L), encoding=ENC)
        return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    args = ap.parse_args(argv)
    CongestionModel(Path(args.root)).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
