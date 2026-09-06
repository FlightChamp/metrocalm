# Model B — 기대 혼잡도 예측 리포트

- 엔진: lightgbm
- **판정: baseline 채택, 모델 폐기**

## 사전 등록한 통과 조건

> LightGBM 이 `역 × 방향 × 요일유형 × 시간대 평균` baseline 을 valid 에서 이기지 못하면, 모델을 폐기하고 baseline 을 서비스에 사용한다.

이 조건은 학습 전에 정했다. 결과를 보고 기준을 바꾸지 않는다.

## 평가 결과

| regime              | model                |     MAE |    RMSE |       R2 |   coverage |   precision |   recall |       f1 |   n_positive |
|:--------------------|:---------------------|--------:|--------:|---------:|-----------:|------------:|---------:|---------:|-------------:|
| A. 시간축 valid (기존 역) | baseline             |   2.364 |   4.575 |   0.947  |     0.9997 |      0.8993 |   0.6868 |   0.7788 |         8701 |
| A. 시간축 valid (기존 역) | LGBM model1(역식별자 포함) |   3.174 |   5.444 |   0.925  |     1      |      0.7241 |   0.6213 |   0.6688 |         8701 |
| A. 시간축 valid (기존 역) | LGBM model2(역식별자 제외) |   6.545 |   9.73  |   0.7604 |     1      |      0.3048 |   0.5143 |   0.3827 |         8701 |
| B. 미관측 역 valid      | baseline(폴백 없음)      | nan     | nan     | nan      |     0      |    nan      | nan      | nan      |          nan |
| B. 미관측 역 valid      | baseline(노선평균 폴백)    |  11.019 |  15.088 |   0.4014 |     1      |      0.116  |   0.2754 |   0.1632 |         2280 |
| B. 미관측 역 valid      | LGBM model2(역식별자 제외) |  11.291 |  15.677 |   0.3538 |     1      |      0.1412 |   0.3465 |   0.2007 |         2280 |
| C. 시간축 test (2026)  | baseline             |   2.215 |   3.859 |   0.9619 |     0.9997 |      0.9034 |   0.6886 |   0.7815 |         5812 |
| C. 시간축 test (2026)  | LGBM model1          |   2.947 |   4.487 |   0.9485 |     1      |      0.6848 |   0.6793 |   0.682  |         5812 |

## 판정 근거

- **Regime A** (시간축 valid, 학습에 나온 역): baseline MAE 2.364 vs model1 MAE 3.174 → **FAIL**
- **Regime B** (미관측 역 56개): baseline(노선평균 폴백) MAE 11.019 vs model2 MAE 11.291 → **FAIL**
- baseline 은 미관측 역에서 **0.0%** 만 값을 낼 수 있다. 폴백 없이는 예측 자체가 불가능하다.
- 참고: Regime B 의 고혼잡 탐지 F1 은 baseline 0.1632 vs model2 0.2007 다. MAE 기준으로는 동률이지만 고혼잡 탐지에서는 모델이 낫다. 다만 사전 등록한 조건은 MAE 기준이므로 판정을 바꾸지 않는다.

## 해석

혼잡도 스냅샷은 격자 하나에 값이 사실상 하나뿐이라, 학습에 나온 역을 맞히는 문제에서는 groupby 평균이 대단히 강하다. 이건 모델이 나쁜 게 아니라 문제의 성질이다. 그래서 모델의 가치는 다음 세 가지로 한정해 주장한다.

1. **미관측 조합 보간** — 신설역·결측 시간대에 값을 낼 수 있다.
2. **혼잡도의 설명** — 어떤 요인이 혼잡을 만드는지 feature importance 로 보인다.
3. **이벤트 보정의 base 제공** — Model C 의 입력이 된다.

## Model-1 feature importance (상위 15)

| feature               |        gain |
|:----------------------|------------:|
| station_code          | 3.43921e+08 |
| flow_total            | 1.03132e+08 |
| time_bin_index        | 8.62048e+07 |
| boarding_cnt          | 6.92151e+07 |
| log_flow_total        | 6.82461e+07 |
| day_type              | 5.7645e+07  |
| line_id               | 3.42729e+07 |
| station_flow_total    | 3.28846e+07 |
| direction             | 3.14217e+07 |
| log_station_flow      | 1.97439e+07 |
| is_pm_peak            | 1.51e+07    |
| boarding_ratio        | 9.98602e+06 |
| line_share_in_station | 8.89382e+06 |
| alighting_cnt         | 8.25577e+06 |
| is_am_peak            | 7.26874e+06 |

## Model-2 feature importance (역 식별자 제외, 상위 15)

| feature               |        gain |
|:----------------------|------------:|
| log_flow_total        | 9.98014e+07 |
| flow_total            | 9.43231e+07 |
| line_id               | 8.96454e+07 |
| time_bin_index        | 8.67135e+07 |
| boarding_cnt          | 8.09216e+07 |
| day_type              | 6.39016e+07 |
| station_flow_total    | 5.58977e+07 |
| boarding_ratio        | 4.21896e+07 |
| alighting_cnt         | 3.74285e+07 |
| direction             | 3.42175e+07 |
| log_station_flow      | 2.99313e+07 |
| net_alighting_ratio   | 2.97911e+07 |
| line_share_in_station | 2.6347e+07  |
| is_pm_peak            | 1.63425e+07 |
| bin_start_min         | 1.10069e+07 |

## 서비스 적용

- baseline lookup: `congestion_baseline_lookup.parquet` — 09 경로 스코어링이 이 값을 쓴다.
- 미관측 역이 생기면 Model-2 로 보간한다.
- 이벤트 보정(Model C)은 이 값 위에 배수로 곱한다.

## 한계

- 타깃이 분기·요일유형별 **평균 패턴**이라 특정 날짜의 실측 혼잡도가 아니다.
- 스냅샷이 11개뿐이라 시계열 변동을 학습할 표본이 부족하다.
- 따라서 '실시간 혼잡도 예측'이 아니라 **기대 혼잡 위험도 추정**이다.

## 처리 노트

- train 305,274 / valid(time) 156,546 / valid(station) 38,961 / test(time) 104,364
- model1: lightgbm, best_iter=3000
- model2: lightgbm, best_iter=3000