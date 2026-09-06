# congestion_training_mart 리포트

- 행: **706,678** / 컬럼 34
- 룩백 구간: 스냅샷 직전 **90일**
- station holdout: 140,494행

## split × station_holdout 교차

| split   |      0 |     1 |
|:--------|-------:|------:|
| test    | 104364 | 25974 |
| train   | 305274 | 75559 |
| valid   | 156546 | 38961 |

## 피처 결측률

|                       |   결측률(%) |
|:----------------------|---------:|
| boarding_ratio        |     2.47 |
| net_alighting_ratio   |     2.47 |
| line_share_in_station |     2.11 |
| flow_total            |     1.96 |
| alighting_cnt         |     1.96 |
| boarding_cnt          |     1.96 |
| station_flow_total    |     0.22 |

## 타깃 분포 (split 별)

| split   |   count |   mean |   50% |    max |
|:--------|--------:|-------:|------:|-------:|
| test    |  130338 |   27.8 | 25.32 | 147.66 |
| train   |  380833 |   27.6 | 25    | 192.8  |
| valid   |  195507 |   27.5 | 24.8  | 164.3  |

## 승하차 피처와 타깃의 상관 (참고)

|                       |   corr |
|:----------------------|-------:|
| log_flow_total        |  0.448 |
| log_station_flow      |  0.488 |
| boarding_ratio        |  0.164 |
| net_alighting_ratio   | -0.164 |
| line_share_in_station | -0.055 |
| bin_start_min         | -0.033 |

> 상관이 낮다고 쓸모없는 건 아니다. 혼잡도는 역·시간대 고정효과가 지배적이라 선형 상관은 약하게 나온다. 실제 기여는 08 의 feature importance 로 확인한다.

## 처리 노트

- 승하차 집계 캐시 생성: ridership_station_hour.parquet (1,576,200행)
- 승하차 피처 매칭 692,802/706,678 (98.0%)
- 역명 기준 총량으로 보완한 행 13,876
- station holdout: 56/282개 역 (140,494행)