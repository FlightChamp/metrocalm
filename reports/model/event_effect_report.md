# event_spike_mart 검증 리포트

- spike 행: **1,022**
- 검증 이벤트: **87**
- 교란요인 포함 행: 0

## 검증 결과 — event_strength 확정

| event_name                             | station_name   | direction   | scope      | date       |   spike_ratio |   robust_z | event_strength_verified   |   use_weight |
|:---------------------------------------|:---------------|:------------|:-----------|:-----------|--------------:|-----------:|:--------------------------|-------------:|
| 2023 제야의 종·새해맞이 카운트다운                  | 종각             | alighting   | peak_hours | 2023-12-31 |        12.702 |      64.12 | very_strong               |          1   |
| Halloween commercial crowd period 2022 | 이태원            | alighting   | peak_hours | 2022-10-29 |         8.184 |      10.41 | very_strong               |          0   |
| 서울세계불꽃축제 2022                          | 여의나루           | alighting   | peak_hours | 2022-10-08 |         6.837 |      13.33 | very_strong               |          1   |
| 2025 제야의 종                             | 종각             | alighting   | peak_hours | 2025-12-31 |         6.146 |      63.49 | very_strong               |          1   |
| 서울세계불꽃축제 2025                          | 여의나루           | alighting   | daily      | 2025-09-27 |         5.79  |      25.6  | very_strong               |          1   |
| 서울세계불꽃축제 2024                          | 여의나루           | alighting   | daily      | 2024-10-05 |         5.41  |      12.73 | very_strong               |          1   |
| 서울세계불꽃축제 2023                          | 여의나루           | alighting   | peak_hours | 2023-10-07 |         5.162 |      10.69 | very_strong               |          1   |
| 한양대 2023학년도 essay                      | 한양대            | boarding    | daily      | 2022-11-27 |         4.947 |      26.96 | very_strong               |          1   |
| 한양대 2024학년도 essay                      | 한양대            | boarding    | daily      | 2023-11-26 |         4.886 |      27.35 | very_strong               |          1   |
| 2023 영등포 여의도 봄꽃축제                      | 여의나루           | alighting   | daily      | 2023-04-01 |         4.845 |      10.08 | very_strong               |          1   |
| 2024 제야의 종                             | 종각             | alighting   | peak_hours | 2024-12-31 |         4.773 |      34.16 | very_strong               |          1   |
| 2022 제야의 종                             | 종각             | alighting   | peak_hours | 2022-12-31 |         4.272 |      21.52 | very_strong               |          1   |
| 한양대 2025학년도 essay                      | 한양대            | boarding    | daily      | 2024-11-24 |         4.231 |      35.77 | very_strong               |          1   |
| 한양대 2026학년도 essay                      | 한양대            | boarding    | daily      | 2025-11-23 |         3.897 |      18.21 | very_strong               |          1   |
| 2026 영등포 여의도 봄꽃축제                      | 여의나루           | alighting   | daily      | 2026-04-05 |         2.62  |       3.77 | strong                    |          0.7 |
| Halloween commercial crowd period 2024 | 이태원            | alighting   | peak_hours | 2024-10-31 |         2.475 |      17.67 | strong                    |          0.7 |
| 2025 영등포 여의도 봄꽃축제                      | 여의나루           | alighting   | daily      | 2025-04-11 |         2.395 |       6.11 | strong                    |          0.7 |
| 2024 영등포 여의도 봄꽃축제                      | 여의나루           | boarding    | daily      | 2024-03-31 |         2.319 |       2.28 | strong                    |          0.7 |
| 한양대 2025학년도 transfer_exam              | 한양대            | boarding    | daily      | 2025-01-11 |         2.256 |       4.65 | strong                    |          0.7 |
| Halloween commercial crowd period 2025 | 이태원            | alighting   | peak_hours | 2025-10-31 |         2.183 |       9.55 | strong                    |          0.7 |
| 동국대 2023학년도 essay                      | 동대입구           | boarding    | daily      | 2022-11-20 |         2.094 |       4.49 | strong                    |          0.7 |
| 동국대 2025학년도 essay                      | 동대입구           | boarding    | daily      | 2024-11-17 |         1.775 |       4.71 | medium                    |          0.4 |
| 동국대 2026학년도 essay                      | 동대입구           | boarding    | daily      | 2025-11-16 |         1.771 |       3.98 | medium                    |          0.4 |
| 한양대 2023학년도 transfer_exam              | 한양대            | boarding    | daily      | 2023-01-07 |         1.766 |       3    | medium                    |          0.4 |
| 한양대 2026학년도 transfer_exam              | 한양대            | boarding    | daily      | 2026-01-10 |         1.753 |       2.57 | medium                    |          0.4 |
| 동국대 2024학년도 essay                      | 동대입구           | boarding    | daily      | 2023-11-19 |         1.668 |       6.13 | medium                    |          0.4 |
| 고려대 2024학년도 transfer_exam              | 안암             | boarding    | daily      | 2023-12-16 |         1.636 |       2.69 | medium                    |          0.4 |
| 동국대 2025학년도 transfer_exam              | 동대입구           | boarding    | daily      | 2025-01-05 |         1.51  |       3.4  | medium                    |          0.4 |
| 고려대 2025학년도 essay                      | 고려대            | boarding    | daily      | 2024-11-16 |         1.486 |       3.8  | weak                      |          0.2 |
| 고려대 2026학년도 essay                      | 고려대            | boarding    | daily      | 2025-11-16 |         1.463 |       4.66 | weak                      |          0.2 |

## 강도별 집계

| event_strength_verified   |   count |
|:--------------------------|--------:|
| weak                      |      35 |
| not_significant           |      24 |
| very_strong               |      14 |
| strong                    |       7 |
| medium                    |       7 |

## 유형별 최대 배율

| event_type      |   n |   max_ratio |   median_ratio |
|:----------------|----:|------------:|---------------:|
| cherry_blossom  |   4 |        4.84 |           2.51 |
| essay           |  35 |        4.95 |           1.32 |
| fireworks       |   4 |        6.84 |           5.6  |
| halloween_proxy |   4 |        8.18 |           2.33 |
| new_year_bell   |   4 |       12.7  |           5.46 |
| transfer_exam   |  36 |        2.26 |           1.19 |

## 일 총량 vs 시간대 한정 비교 (제야의 종 유형)

|                           |   daily |   peak_hours |
|:--------------------------|--------:|-------------:|
| ('NYB_2022', 'alighting') |    1.01 |         4.27 |
| ('NYB_2022', 'boarding')  |    0.82 |         0.79 |
| ('NYB_2023', 'alighting') |    2.18 |        12.7  |
| ('NYB_2023', 'boarding')  |    1.6  |         2.55 |
| ('NYB_2024', 'alighting') |    0.99 |         4.77 |
| ('NYB_2024', 'boarding')  |    0.95 |         0.85 |
| ('NYB_2025', 'alighting') |    1.08 |         6.15 |
| ('NYB_2025', 'boarding')  |    0.99 |         0.89 |

> 일 총량으로는 효과가 없어 보이지만 야간 시간대만 보면 강하게 튄다. 일 총량 피처로 쓰면 이벤트가 사라진다.

## 학습 제외 이벤트

- **Halloween commercial crowd period 2022** (배율 8.184배) — 2022-10-29 이태원 참사를 포함한 기간. 이태원 하차 5.73배(야간 8.18배)로 다른 해(1.3~1.6배)의 3~5배이며, 이듬해 급감은 사회적 자제의 결과다. 반복되는 할로윈 패턴이 아니므로 학습에서 제외한다.

## 교란요인 (같은 날 두 이벤트)

없음.

> 날짜가 겹치는 이벤트는 있다(불꽃축제 ↔ 홍익대 논술 3년 연속 등). 그러나 영향역이 겹치지 않는다. 불꽃축제는 여의나루·여의도·마포·공덕, 홍익대 논술은 홍대입구·상수다. **역-날짜 단위로 보면 교란은 0건**이며, 날짜 단위 경고는 과하다.

## 처리 노트

- 이벤트 87건 로드 (캘린더 16 / 대학 71)
- 승하차 1,636,320행 로드 (대상 역 22개)
- halloween_proxy: 제외 이벤트를 뺀 대표 배율(중앙값) 2.18배 — 이 값을 유형 기준 강도로 쓴다.

## 산출물

- `event_spike_mart` : data\marts\event_spike_mart.parquet
- `event_strength_verified` : data\master\event_strength_verified.csv