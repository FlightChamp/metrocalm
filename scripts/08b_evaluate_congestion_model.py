"""
08b_evaluate_congestion_model.py
================================
Model B 평가를 업계 표준 체계로 재구성한다. 08 의 판정(baseline 채택)을 뒤집지 않고,
그 판정이 어느 정도로 견고한지를 정량화한다.

08 대비 추가된 것
-----------------
1. Baseline ladder 4단
   L0 전체 평균
   L1 line x direction x day_type x time_bin 평균
   L2 station x direction x day_type x time_bin 평균   <- 사전 등록한 비교 대상
   L3 L2 + 최근 스냅샷 값 (recency)
   어느 수준의 정보가 얼마나 기여하는지 계단으로 보인다.

2. Skill Score = 1 - MAE_model / MAE_reference
   MASE 는 시계열 naive forecast 를 분모로 쓰는 지표라 격자 회귀인 이 문제에 맞지 않는다.
   정의가 정확한 Skill Score 를 쓴다. 양수면 개선, 음수면 열세다.

3. 불균형 분류 지표
   PR-AUC(Average Precision), Recall@Precision>=0.5.
   고혼잡 라벨이 5% 대라 Accuracy 는 무의미하고 단일 임계 F1 도 임계에 의존한다.

4. Ablation study
   시간 -> +역속성 -> +승하차flow -> 전체. 어떤 피처군이 실제로 기여하는지 분해한다.

5. SHAP (Model-2 에만)
   08 판정에서 채택된 것은 baseline 이고, 모델은 미관측 역 보간용 Model-2 다.
   폐기한 Model-1 을 설명하는 것은 앞뒤가 맞지 않으므로 Model-2 만 설명한다.

6. Error analysis
   노선별 / 시간대별 / 혼잡도 구간별 오차 분해.

주의
----
`label_high` 는 역x방향별 train p95 로 정의된다. station holdout 의 역은 train 에
없으므로 전역 p95 로 대체된다. 즉 **미관측 역의 라벨 기준이 다른 역과 다르다.**
Regime B 의 분류 지표는 이 편향 위에서 계산된 값이며, 리포트에 명시한다.

출력
----
    reports/model/congestion_model_eval.md
    reports/model/baseline_ladder.csv
    reports/model/ablation_study.csv
    reports/model/error_analysis_by_line.csv
    reports/model/shap_summary_model2.csv
    reports/figures/shap_model2.png

사용법
------
    python scripts/08b_evaluate_congestion_model.py --root .
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
TIME_FEATURES = ["time_bin_index", "bin_start_min", "is_am_peak", "is_pm_peak",
                 "month", "quarter"]
STATION_FEATURES = ["is_branch_row", "is_transfer_station", "has_ridership"]
FLOW_FEATURES = ["boarding_cnt", "alighting_cnt", "flow_total", "station_flow_total",
                 "log_flow_total", "log_station_flow", "boarding_ratio",
                 "net_alighting_ratio", "line_share_in_station"]
ID_FEATURES = ["station_code"]

ABLATIONS = [
    ("A. 시간만", TIME_FEATURES + ["line_id", "direction", "day_type"]),
    ("B. +역속성", TIME_FEATURES + ["line_id", "direction", "day_type"]
     + STATION_FEATURES + ["branch_code", "attribution_flag"]),
    ("C. +승하차flow", TIME_FEATURES + ["line_id", "direction", "day_type"]
     + STATION_FEATURES + ["branch_code", "attribution_flag"] + FLOW_FEATURES),
    ("D. +역식별자(전체)", TIME_FEATURES + ["line_id", "direction", "day_type"]
     + STATION_FEATURES + ["branch_code", "attribution_flag"] + FLOW_FEATURES + ID_FEATURES),
]


def load_mart(base: Path, name: str) -> pd.DataFrame:
    for ext in (".parquet", ".csv.gz"):
        p = base / (name + ext)
        if p.exists():
            return pd.read_parquet(p) if ext == ".parquet" else pd.read_csv(p)
    raise FileNotFoundError(name + " 없음. 07 을 먼저 실행하세요.")


def mae(y, p):
    m = ~np.isnan(p)
    return float(np.mean(np.abs(np.asarray(y)[m] - p[m]))) if m.any() else np.nan


def rmse(y, p):
    m = ~np.isnan(p)
    return float(np.sqrt(np.mean((np.asarray(y)[m] - p[m]) ** 2))) if m.any() else np.nan


def r2(y, p):
    m = ~np.isnan(p)
    if not m.any():
        return np.nan
    y2, p2 = np.asarray(y)[m], p[m]
    ss = float(np.sum((y2 - np.mean(y2)) ** 2))
    return 1 - float(np.sum((y2 - p2) ** 2)) / ss if ss > 0 else np.nan


def pr_metrics(df: pd.DataFrame, pred: np.ndarray) -> dict:
    """
    고혼잡 탐지 성능. 예측 혼잡도를 점수로 보고 PR 곡선을 그린다.
    점수가 높을수록 고혼잡이므로 threshold 를 바꿔가며 precision/recall 을 얻는다.
    """
    from sklearn.metrics import average_precision_score, precision_recall_curve
    m = ~np.isnan(pred)
    if m.sum() == 0:
        return {"pr_auc": np.nan, "recall_at_p50": np.nan, "base_rate": np.nan}
    y = df.loc[m, "label_high"].to_numpy()
    # 역별 임계 차이를 흡수하기 위해 예측값을 해당 행의 p95 로 나눈 비율을 점수로 쓴다.
    score = pred[m] / df.loc[m, "p95_train"].replace(0, np.nan).to_numpy()
    ok = ~np.isnan(score)
    y, score = y[ok], score[ok]
    if y.sum() == 0 or y.sum() == len(y):
        return {"pr_auc": np.nan, "recall_at_p50": np.nan,
                "base_rate": round(float(y.mean()), 4)}
    ap = float(average_precision_score(y, score))
    prec, rec, _ = precision_recall_curve(y, score)
    valid = prec[:-1] >= 0.5
    r_at_p50 = float(rec[:-1][valid].max()) if valid.any() else 0.0
    return {"pr_auc": round(ap, 4), "recall_at_p50": round(r_at_p50, 4),
            "base_rate": round(float(y.mean()), 4)}


class BaselineLadder:
    """어느 수준의 정보가 얼마나 기여하는지 계단으로 확인한다."""

    KEYS = {
        "L0 전체평균": [],
        "L1 노선x방향x요일x시간대": ["line_id", "direction", "day_type", "time_bin_index"],
        "L2 역x방향x요일x시간대": ["station_uid", "direction", "day_type", "time_bin_index"],
    }

    def __init__(self, train: pd.DataFrame):
        self.global_mean = float(train["congestion_rate"].mean())
        self.tables = {}
        for name, key in self.KEYS.items():
            if key:
                self.tables[name] = train.groupby(key)["congestion_rate"].mean()
        # L3: L2 + 최근 스냅샷 가중 (최근 스냅샷 값을 우선 사용)
        last = train["snapshot_date"].max()
        recent = train[train["snapshot_date"] == last]
        self.l3_recent = recent.groupby(
            ["station_uid", "direction", "day_type", "time_bin_index"])["congestion_rate"].mean()
        self.l3_base = self.tables["L2 역x방향x요일x시간대"]
        self.last_snapshot = last

    def predict(self, name: str, df: pd.DataFrame) -> np.ndarray:
        if name == "L0 전체평균":
            return np.full(len(df), self.global_mean)
        if name == "L3 L2+최근스냅샷":
            idx = pd.MultiIndex.from_frame(
                df[["station_uid", "direction", "day_type", "time_bin_index"]])
            p = np.array(self.l3_recent.reindex(idx).to_numpy(), dtype=float, copy=True)
            base = np.array(self.l3_base.reindex(idx).to_numpy(), dtype=float, copy=True)
            miss = np.isnan(p)
            p[miss] = base[miss]
            return p
        key = self.KEYS[name]
        idx = pd.MultiIndex.from_frame(df[key])
        return np.array(self.tables[name].reindex(idx).to_numpy(), dtype=float, copy=True)


def fit_lgbm(train, valid, feats, seed=SEED):
    import lightgbm as lgb
    cats = [c for c in CAT_FEATURES if c in feats]

    def prep(df):
        X = df[feats].copy()
        for c in cats:
            X[c] = X[c].astype("category")
        return X

    dtr = lgb.Dataset(prep(train), label=train["congestion_rate"],
                      categorical_feature=cats, free_raw_data=False)
    dva = lgb.Dataset(prep(valid), label=valid["congestion_rate"],
                      categorical_feature=cats, reference=dtr, free_raw_data=False)
    params = {"objective": "regression", "metric": "l1", "learning_rate": 0.05,
              "num_leaves": 63, "min_data_in_leaf": 100, "feature_fraction": 0.9,
              "bagging_fraction": 0.8, "bagging_freq": 1, "verbose": -1, "seed": seed}
    booster = lgb.train(params, dtr, num_boost_round=1500, valid_sets=[dva],
                        callbacks=[lgb.early_stopping(100, verbose=False),
                                   lgb.log_evaluation(0)])
    return booster, prep, feats


class ModelEvaluator:

    def __init__(self, root: Path, shap_sample: int):
        self.root = root
        self.mart = root / "data" / "marts"
        self.report = root / "reports" / "model"
        self.figs = root / "reports" / "figures"
        for d in (self.report, self.figs):
            d.mkdir(parents=True, exist_ok=True)
        self.shap_sample = shap_sample
        self.notes = []

    def run(self):
        mart = load_mart(self.mart, "congestion_training_mart")
        mart["line_id"] = mart["line_id"].astype(str)

        tr = mart[(mart["split"] == "train") & (mart["is_station_holdout"] == 0)].reset_index(drop=True)
        va_t = mart[(mart["split"] == "valid") & (mart["is_station_holdout"] == 0)].reset_index(drop=True)
        va_s = mart[(mart["split"] == "valid") & (mart["is_station_holdout"] == 1)].reset_index(drop=True)
        te_t = mart[(mart["split"] == "test") & (mart["is_station_holdout"] == 0)].reset_index(drop=True)
        self.notes.append("train %d / valid(time) %d / valid(station) %d / test(time) %d"
                          % (len(tr), len(va_t), len(va_s), len(te_t)))

        # ---------- 1. Baseline ladder ----------
        lad = BaselineLadder(tr)
        names = list(BaselineLadder.KEYS) + ["L3 L2+최근스냅샷"]
        rows = []
        preds_time = {}
        for n in names:
            p = lad.predict(n, va_t)
            preds_time[n] = p
            rows.append({"level": n, "regime": "A. 시간축 valid",
                         "MAE": round(mae(va_t["congestion_rate"], p), 3),
                         "RMSE": round(rmse(va_t["congestion_rate"], p), 3),
                         "R2": round(r2(va_t["congestion_rate"], p), 4),
                         "coverage": round(float((~np.isnan(p)).mean()), 4),
                         **pr_metrics(va_t, p)})
        for n in names:
            p = lad.predict(n, va_s)
            rows.append({"level": n, "regime": "B. 미관측 역 valid",
                         "MAE": round(mae(va_s["congestion_rate"], p), 3),
                         "RMSE": round(rmse(va_s["congestion_rate"], p), 3),
                         "R2": round(r2(va_s["congestion_rate"], p), 4),
                         "coverage": round(float((~np.isnan(p)).mean()), 4),
                         **pr_metrics(va_s, p)})
        ladder = pd.DataFrame(rows)

        # ---------- 2. Ablation ----------
        abl_rows = []
        boosters = {}
        for tag, feats in ABLATIONS:
            feats = [f for f in feats if f in tr.columns]
            b, prep, _ = fit_lgbm(tr, va_t, feats)
            boosters[tag] = (b, prep, feats)
            pt = b.predict(prep(va_t))
            ps = b.predict(prep(va_s))
            abl_rows.append({
                "ablation": tag, "n_features": len(feats),
                "MAE_time": round(mae(va_t["congestion_rate"], pt), 3),
                "R2_time": round(r2(va_t["congestion_rate"], pt), 4),
                "PR_AUC_time": pr_metrics(va_t, pt)["pr_auc"],
                "MAE_station": round(mae(va_s["congestion_rate"], ps), 3),
                "R2_station": round(r2(va_s["congestion_rate"], ps), 4),
                "PR_AUC_station": pr_metrics(va_s, ps)["pr_auc"],
                "best_iter": b.best_iteration,
            })
        ablation = pd.DataFrame(abl_rows)

        # ---------- 3. Skill Score (사전 등록 비교) ----------
        ref_name = "L2 역x방향x요일x시간대"
        ref_mae_t = float(ladder[(ladder.level == ref_name)
                                 & (ladder.regime == "A. 시간축 valid")]["MAE"].iloc[0])
        ref_mae_s = float(ladder[(ladder.level == ref_name)
                                 & (ladder.regime == "B. 미관측 역 valid")]["MAE"].iloc[0])
        l1_mae_s = float(ladder[(ladder.level == "L1 노선x방향x요일x시간대")
                                & (ladder.regime == "B. 미관측 역 valid")]["MAE"].iloc[0])

        m_full = ablation[ablation.ablation == "D. +역식별자(전체)"].iloc[0]
        m_noid = ablation[ablation.ablation == "C. +승하차flow"].iloc[0]
        skill_a = 1 - float(m_full["MAE_time"]) / ref_mae_t
        skill_b = 1 - float(m_noid["MAE_station"]) / l1_mae_s

        # ---------- 4. Error analysis ----------
        b2, prep2, feats2 = boosters["C. +승하차flow"]
        va_t = va_t.copy()
        va_t["pred_baseline"] = preds_time[ref_name]
        va_t["abs_err_baseline"] = (va_t["congestion_rate"] - va_t["pred_baseline"]).abs()
        by_line = (va_t.groupby("line_id")
                   .agg(n=("congestion_rate", "size"),
                        mean_congestion=("congestion_rate", "mean"),
                        MAE=("abs_err_baseline", "mean"),
                        p95_err=("abs_err_baseline", lambda s: s.quantile(0.95)))
                   .round(3).reset_index())
        va_t["cong_band"] = pd.cut(va_t["congestion_rate"],
                                   [-1, 30, 60, 80, 100, 130, 999],
                                   labels=["~30", "30-60", "60-80", "80-100", "100-130", "130+"])
        by_band = (va_t.groupby("cong_band", observed=True)
                   .agg(n=("congestion_rate", "size"),
                        MAE=("abs_err_baseline", "mean"))
                   .round(3).reset_index())
        by_bin = (va_t.groupby("time_bin")
                  .agg(n=("congestion_rate", "size"),
                       MAE=("abs_err_baseline", "mean"))
                  .round(3).reset_index().nlargest(8, "MAE"))

        # ---------- 5. SHAP (Model-2 = 역식별자 제외) ----------
        shap_df, shap_png = self.run_shap(b2, prep2, feats2, va_s)

        paths = self.write_outputs(ladder, ablation, by_line, by_band, by_bin, shap_df)
        rep = self.write_report(ladder, ablation, by_line, by_band, by_bin, shap_df,
                                ref_mae_t, ref_mae_s, l1_mae_s, skill_a, skill_b,
                                m_full, m_noid, shap_png, paths)
        paths["report"] = rep

        # ---------- 콘솔 ----------
        print("=" * 96)
        print(" 08b_evaluate_congestion_model - 완료")
        print("=" * 96)
        for n in self.notes:
            print("  " + n)
        print("\n[Baseline ladder]")
        print(ladder.to_string(index=False))
        print("\n[Ablation study]")
        print(ablation.to_string(index=False))
        print("\n[Skill Score]  (양수면 개선, 음수면 열세)")
        print("  Regime A  LGBM(전체) vs L2 baseline : %+.3f" % skill_a)
        print("  Regime B  LGBM(식별자제외) vs L1 baseline : %+.3f" % skill_b)
        print("\n[노선별 baseline 오차]")
        print(by_line.to_string(index=False))
        print("\n[혼잡도 구간별 baseline 오차]")
        print(by_band.to_string(index=False))
        if shap_df is not None:
            print("\n[SHAP 상위 10 - Model-2]")
            print(shap_df.head(10).to_string(index=False))
        print("\n[생성 파일]")
        for k, v in paths.items():
            print("  %-28s %s" % (k, v))
        print("=" * 96)

    # ---------- SHAP ----------
    def run_shap(self, booster, prep, feats, sample_df):
        try:
            import shap
        except ImportError:
            self.notes.append("shap 미설치 → SHAP 분석 생략 (pip install shap)")
            return None, None
        n = min(self.shap_sample, len(sample_df))
        X = prep(sample_df.sample(n, random_state=SEED))
        try:
            expl = shap.TreeExplainer(booster)
            sv = expl.shap_values(X)
        except Exception as e:
            self.notes.append("SHAP 계산 실패: %s" % e)
            return None, None
        imp = np.abs(sv).mean(axis=0)
        df = (pd.DataFrame({"feature": feats, "mean_abs_shap": imp})
              .sort_values("mean_abs_shap", ascending=False).reset_index(drop=True))
        df["share_pct"] = (df["mean_abs_shap"] / df["mean_abs_shap"].sum() * 100).round(2)
        df["mean_abs_shap"] = df["mean_abs_shap"].round(4)
        self.notes.append("SHAP: Model-2(역식별자 제외) 기준, 미관측 역 %d행 샘플" % n)

        png = None
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            top = df.head(12).iloc[::-1]
            fig, ax = plt.subplots(figsize=(8, 5.5))
            ax.barh(top["feature"], top["mean_abs_shap"], color="steelblue")
            ax.set_xlabel("mean |SHAP value|  (congestion %p)")
            ax.set_title("Model-2 feature contribution (station identity excluded)\n"
                         "evaluated on unseen-station holdout", fontsize=11)
            fig.tight_layout()
            png = self.figs / "shap_model2.png"
            fig.savefig(png, dpi=150)
            plt.close(fig)
        except ImportError:
            self.notes.append("matplotlib 미설치 → SHAP 그림 생략")
        return df, png

    # ---------- 출력 ----------
    def write_outputs(self, ladder, ablation, by_line, by_band, by_bin, shap_df):
        paths = {}
        p = self.report / "baseline_ladder.csv"
        ladder.to_csv(p, index=False, encoding=ENC); paths["baseline_ladder"] = p
        p = self.report / "ablation_study.csv"
        ablation.to_csv(p, index=False, encoding=ENC); paths["ablation_study"] = p
        p = self.report / "error_analysis_by_line.csv"
        by_line.to_csv(p, index=False, encoding=ENC); paths["error_analysis_by_line"] = p
        if shap_df is not None:
            p = self.report / "shap_summary_model2.csv"
            shap_df.to_csv(p, index=False, encoding=ENC); paths["shap_summary_model2"] = p
        return paths

    def write_report(self, ladder, ablation, by_line, by_band, by_bin, shap_df,
                     ref_mae_t, ref_mae_s, l1_mae_s, skill_a, skill_b,
                     m_full, m_noid, shap_png, paths):
        L = []
        A = L.append
        A("# Model B 평가 리포트 (표준 지표 재구성)\n")
        A("08 의 판정(**baseline 채택, LightGBM 폐기**)은 그대로다."
          " 이 문서는 그 판정이 어느 정도로 견고한지를 표준 지표로 정량화한다.\n")

        A("## 1. Baseline ladder\n")
        A("어느 수준의 정보가 얼마나 기여하는지 계단으로 확인한다.\n")
        A(ladder.to_markdown(index=False))
        A("")
        A("- 사전 등록한 비교 대상은 **L2 (역x방향x요일x시간대 평균)** 이다.")
        A("- Regime B(미관측 역)에서 L2 는 coverage 0 이다. 학습에 없던 역은"
          " 값 자체를 만들 수 없다. 그래서 L1(노선 단위)이 실질 비교 대상이 된다.\n")

        A("## 2. Skill Score\n")
        A("MASE 는 시계열 naive forecast 를 분모로 쓰는 지표라 격자 회귀인 이 문제에"
          " 맞지 않는다. 정의가 정확한 Skill Score 를 쓴다.\n")
        A("```\nSkill Score = 1 - MAE_model / MAE_reference   (양수면 개선)\n```\n")
        A("| Regime | 모델 | 기준 baseline | Skill Score |")
        A("|---|---|---|---|")
        A("| A. 시간축 valid | LGBM 전체 피처 | L2 (MAE %.3f) | **%+.3f** |"
          % (ref_mae_t, skill_a))
        A("| B. 미관측 역 | LGBM 역식별자 제외 | L1 (MAE %.3f) | **%+.3f** |"
          % (l1_mae_s, skill_b))
        A("")
        A("Regime A 의 Skill Score 가 음수라는 것은 baseline 이 더 낫다는 뜻이다."
          " 혼잡도 스냅샷은 격자 하나에 값이 사실상 하나뿐이라 groupby 평균이"
          " 정답표에 가깝다. 모델이 나쁜 게 아니라 문제의 성질이다.\n")

        A("## 3. 불균형 분류 지표\n")
        A("고혼잡 라벨은 5% 대다. Accuracy 는 무의미하고 단일 임계 F1 도 임계에 의존한다."
          " PR-AUC(Average Precision)와 Recall@Precision>=0.5 를 쓴다.\n")
        A(ladder[["level", "regime", "pr_auc", "recall_at_p50", "base_rate"]]
          .to_markdown(index=False))
        A("")
        A("> **주의**: `label_high` 는 역x방향별 train p95 로 정의된다."
          " station holdout 의 역은 train 에 없어 전역 p95 로 대체된다."
          " 즉 미관측 역의 라벨 기준이 다른 역과 다르며, Regime B 의 분류 지표는"
          " 이 편향 위에서 계산된 값이다.\n")

        A("## 4. Ablation study\n")
        A("어떤 피처군이 실제로 기여하는지 분해한다.\n")
        A(ablation.to_markdown(index=False))
        A("")
        A("- A→B: 역 속성(환승역·분기·귀속 플래그)의 기여")
        A("- B→C: **승하차 흐름 피처의 기여** — 이 프로젝트가 승하차 데이터를 쓰는 이유")
        A("- C→D: 역 식별자의 기여. 크다면 모델이 역을 외우고 있다는 뜻이다.\n")

        A("## 5. SHAP — Model-2 (역 식별자 제외)\n")
        A("08 판정에서 채택된 것은 baseline 이고, 모델은 미관측 역 보간용 Model-2 다."
          " 폐기한 Model-1 을 설명하는 것은 앞뒤가 맞지 않으므로 Model-2 만 설명한다.\n")
        if shap_df is not None:
            A(shap_df.head(15).to_markdown(index=False))
            A("")
            if shap_png is not None:
                A("![shap](../figures/%s)\n" % shap_png.name)
            A("> 역이 어디인지 모르는 상태에서 무엇으로 혼잡도를 설명하는지 보여준다.\n")
        else:
            A("(shap 미설치 — `pip install shap` 후 재실행하면 생성된다)\n")

        A("## 6. Error analysis\n")
        A("### 6-1. 노선별 (L2 baseline 기준)\n")
        A(by_line.to_markdown(index=False))
        A("")
        A("### 6-2. 혼잡도 구간별\n")
        A(by_band.to_markdown(index=False))
        A("")
        A("> 고혼잡 구간일수록 오차가 커진다면, 정작 중요한 영역에서 부정확하다는 뜻이다."
          " 서비스 신뢰도 측면에서 반드시 밝혀야 할 지점이다.\n")
        A("### 6-3. 오차가 큰 시간대 top 8\n")
        A(by_bin.to_markdown(index=False))
        A("")

        A("## 7. 결론\n")
        A("1. 사전 등록한 조건대로 **baseline(L2)을 서비스에 사용한다.** 결과를 보고"
          " 기준을 바꾸지 않았다.")
        A("2. 다만 baseline 은 미관측 역에서 **coverage 0** 이다. 신설역이 생기면"
          " 무력하므로 Model-2 를 보간용으로 유지한다.")
        A("3. Ablation 으로 승하차 흐름 피처의 기여를 정량화했고, SHAP 으로 그 내용을"
          " 설명했다. 모델의 가치는 예측 정확도가 아니라 **설명과 보간**에 있다.\n")

        if self.notes:
            A("## 8. 처리 노트\n")
            for n in self.notes:
                A("- " + n)
            A("")
        A("## 9. 산출물\n")
        for k, v in paths.items():
            A("- `%s` : %s" % (k, v))

        p = self.report / "congestion_model_eval.md"
        p.write_text("\n".join(L), encoding=ENC)
        return p


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--shap-sample", type=int, default=5000)
    args = ap.parse_args(argv)
    ModelEvaluator(Path(args.root), args.shap_sample).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
