# 스키마 검증 리포트 (pandera)

리포트는 읽지 않으면 아무 일도 일어나지 않는다. 스키마 검증은 **깨지면 파이프라인이 멈추는 테스트**다.

## 1. 테이블 스키마

| table                    | status   |   rows |   failed_checks | detail                           |
|:-------------------------|:---------|-------:|----------------:|:---------------------------------|
| station_master           | PASS     |    283 |               0 | station_master.csv               |
| stg_congestion           | PASS     | 715299 |               0 | stg_congestion.parquet           |
| congestion_30min_mart    | PASS     | 715065 |               0 | congestion_30min_mart.parquet    |
| congestion_training_mart | PASS     | 706678 |               0 | congestion_training_mart.parquet |
| route_edge_mart          | PASS     |    536 |               0 | route_edge_mart.parquet          |
| transfer_edge_mart       | PASS     |     81 |               0 | transfer_edge_mart.parquet       |
| event_spike_mart         | PASS     |   1022 |               0 | event_spike_mart.parquet         |

## 2. 교차 검증

단일 테이블 스키마로는 못 잡는 것을 따로 본다.

| check                             | status   | detail                                     |
|:----------------------------------|:---------|:-------------------------------------------|
| 혼잡도가 커버하지 못하는 역                   | PASS     | 없음                                         |
| 승하차에 없는 혼잡도 역(9xxx 제외)            | PASS     | 1개 [('2', 260)]                            |
| split 이 스냅샷 단위로 배타적               | PASS     | 정상                                         |
| holdout 역이 train 에 있어도 학습에서 제외 가능 | PASS     | train 내 holdout 75559행 (08/08b 가 필터링함)     |
| 환승역 마스터 커버리지                      | PASS     | 35/35 커버                                   |
| 분기 노드 존재                          | PASS     | 모두 존재                                      |
| 응암순환 단방향                          | PASS     | 정방향 6/6, 역방향 0 (0이어야 함)                    |
| 운행 행의 파생값 결측 0                    | PASS     | perceived_multiplier 0 / congestion_rate 0 |
| 미운행 행은 congestion_rate 가 결측       | PASS     | 미운행 8387행 (막차 이후 등, 0으로 채우지 않음)            |
| label_high 비율 4~7%                | PASS     | 5.25%                                      |

## 3. 결과

- 테이블 PASS 7 / FAIL 0 / SKIP 0
- 교차검증 PASS 10 / FAIL 0
- **전체 판정: PASS**

## 4. 이 검증이 잡으려는 실제 사고

- 혼잡도 원본 스키마가 3종으로 갈라진 것
- 5호선 둔촌동/올림픽공원이 2호선으로 잘못 기재된 156행
- 두 마트의 station_uid 규칙이 달라 조인 매칭률이 0% 였던 것
- 응암순환을 양방향으로 만들어 존재하지 않는 경로를 추천하는 것
