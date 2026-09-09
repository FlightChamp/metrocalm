# 여유로 서울 (Yeoyuro Seoul) 프로젝트 기획안 v2 (실측 반영본)

- 작성일: 2026-08-31
- 근거: 보유 데이터 74개 파일 전수 감사 (`data_audit_report.md`, `congestion_audit_report.md`)
- v1 대비 성격: **추정을 실측으로 교체**한 개정본. 구조는 유지하고, 데이터가 반박한 부분만 수정한다.

---

## 0. v1 → v2 변경 요약

| # | v1 기획서 | 실측 | v2 조치 |
|---|---|---|---|
| 1 | 승하차 = `호선 × 역 × 시간대` 수요 | 환승역 4곳(신내·연신내·충무로3·까치산2) 귀속 0 | **역 단위 피처로 재정의**. 호선 수요로 해석 금지 |
| 2 | 고혼잡 임계 100/130/150% | 130% = 0.087%, 150% = **38셀** | **분위수 라벨(역×방향 p95)** 주 지표, 100%는 보조 |
| 3 | 분기점 3개 | 원본에 9000번대 계통코드 **5개** 존재 | **응암순환(6호선) 추가 → 분기 4종** |
| 4 | 그래프 대칭 edge 가정 | 단방향 역 **9개** 확인 | `is_bidirectional` 컬럼 필수 |
| 5 | edge_cost = α·분 + β·% | 단위 불일치 | **전 항목 체감시간(분) 환산** |
| 6 | 혼잡도 3개월 단위 11개 | 확인. **스키마 3종** 드리프트 | 스키마 자동 감지 파서로 대응 완료 |
| 7 | 이벤트 강도 정성 기술 | 배율 실측 완료 | **event_strength 를 실측 배율로 확정** |
| 8 | 환승인원 10개 파일 | **9개** | 인벤토리 정정 |
| 9 | 역명 그대로 조인 | 기획서 역명 18개가 원본과 불일치 | `station_alias_master`(69행) 경유 필수 |
| 10 | Model B = 혼잡도 예측 | 스냅샷 11개, 격자당 값 11개 | **baseline 대비 우위 검증을 통과 조건으로 명문화** |

---

## 1. 프로젝트 정의 (유지)

서울교통공사 1~8호선의 과거 승하차·혼잡도·환승·역간거리·이벤트 데이터를 결합해
특정 시간대의 **기대 혼잡 위험도**를 추정하고, 최단시간뿐 아니라 쾌적성·환승 피로도·
착석 가능성 proxy 를 함께 고려한 대안 경로를 추천하는 시스템.

경로 추천을 최단경로 문제가 아니라 **multi-objective route scoring 문제**로 정의한다.

---

## 2. 확정된 데이터 인벤토리 (실측)

| 데이터 | 파일 | 실측 규모 | 상태 |
|---|---|---|---|
| 역별 시간대별 이용인원 | 48 | 797,946행 / 2022-07 ~ 2026-06 | ✅ 결측·중복·음수 0 |
| 지하철혼잡도정보 | 11 | long 715,299행 / 2022-12 ~ 2026-06 | ✅ 스키마 3종 대응 완료 |
| 환승역 환승인원 | 9 | 각 73행 | ⚠️ 컬럼명 3종 드리프트 |
| 수도권 환승 데이터(호차/문) | 1 | 892행 중 **범위 내 375행(42%)** | ⚠️ alias 필수 |
| 역간거리 및 소요시간 | 2 | 279 / 278행 | 최신(240810) 사용 |
| 국가철도공단 역간거리 | 1 | 318행 | 교차검증용 |
| 열차운행현황 | 1 | 12행 × 8호선 | 참고용(배차 예측 금지) |
| 일별 승객유형별 수송인원 | 1 | 576,714행 / **2022년만** | 시간대 없음. 보조 검증용 |

### 데이터가 없어서 못 하는 것 (선언)

- 실시간 위치·재차 인원 → **실시간 혼잡도 예측 불가**
- 객차 단위 재차 → **호차별 혼잡도 예측 불가**
- 좌석 점유 → **착석 확률 계산 불가**
- 날짜별 실측 혼잡도 → **이벤트 당일 혼잡도 직접 예측 불가**

---

## 3. 확정된 범위와 마스터 (실측 역 수)

혼잡도 원본이 서울교통공사 관할만 담고 있어, **역번호 구간 필터만으로 §4 범위와 일치**한다.

| 호선 | 구간 | 역번호 | 역 수 | 방향 표기 |
|---|---|---|---|---|
| 1 | 서울역~청량리 | 150–159 | 10 | 상선/하선 |
| 2 | 본선+성수지선+신정지선 | 201–260, 9001–9003 | 54 | **내선/외선** |
| 3 | 지축~오금 | 309–342 | 34 | 상선/하선 |
| 4 | 불암산~남태령 | 409–434 | 26 | 상선/하선 |
| 5 | 방화~하남검단산 + 마천지선 | 2511–2566, 9005 | 57 | 상선/하선 |
| 6 | 응암~신내 | 2611–2653, 9006 | 40 | 상선/하선 |
| 7 | 장암~온수 | 2711–2752 | 42 | 상선/하선 |
| 8 | 암사역사공원~모란 | 2810–2828 | 19 | 상선/하선 |

### 3-1. 분기·순환 계통 (4종 — v1의 3종에서 확장)

원본이 9000번대 코드로 이미 계통을 분리해 두었다.

| 코드 | 표기 | branch_code | 그래프 처리 |
|---|---|---|---|
| 2_9001 | 성수E | `seongsu_branch_east` | 성수지선 계통 |
| 2_9002 | 성수 | `seongsu_branch` | 본선/지선 분기 |
| 2_9003 | 신도림 | `sinjeong_branch` | 신정지선 분기 |
| 5_9005 | 강동(마천) | `macheon_branch` | 마천 방면 (하선만) |
| 6_9006 | 응암S | `eungam_loop` | **응암순환 진입 (신규)** |

5호선 강동은 원본이 방향별로 분리 기록한다: `2549 강동`=상선(통합), `2549 강동(하남검단산)`=하선,
`9005 강동(마천)`=하선. **분기 방향만 계통을 쪼갠 구조**라 branch edge를 데이터 그대로 구현 가능하다.

### 3-2. 단방향 역 9개 (그래프 비대칭 edge 필수)

`6호선: 역촌·불광·독바위·연신내·구산·응암S` (응암순환), `5호선: 강동 계열 3`

대칭 edge로 만들면 **존재하지 않는 경로를 추천**한다.

### 3-3. 승하차 귀속 예외 (모델 학습 시 별도 처리)

| station_uid | 현상 | 처리 |
|---|---|---|
| 6_2649 신내 | 승하차 전 기간 0 | 학습 제외 |
| 6_2615 연신내 | 6호선 귀속 0 (3호선에 전량) | 역 총량 피처로 대체 |
| 3_321 충무로 | 3호선 총합 136 (4호선에 전량) | 역 총량 피처로 대체 |
| 2_260 까치산 | 2호선 승하차 없음 (5호선에 전량) | 그래프 노드 유지, 수요 피처는 역 총량 |

---

## 4. 데이터 레이어 (확정 스키마)

```
data/raw/        원본 그대로
data/staging/    stg_ridership_monthly, stg_congestion            ← 표준화만
data/master/     station(283) / alias(69) / line(11) / transfer(35)
                 / branch(4) / event_calendar(16) / university(71)
data/marts/      ridership_hourly_mart, congestion_30min_mart,
                 congestion_station_profile, congestion_edge_lookup,
                 event_spike_mart, congestion_training_mart,
                 route_edge_mart, transfer_edge_mart
```

**조인 키는 `station_uid = line_id + "_" + station_code` 하나로 통일한다.**
역명 문자열 조인은 금지( `잠실` 접두 매칭이 `잠실나루`·`잠실새내`를 잡는다).
혼잡도와 승하차의 역번호 체계는 동일함을 확인했다 — **조인 성공률 100%, 역명 불일치 0**.

### congestion_30min_mart 확정 스키마

```
snapshot_id, snapshot_date, split, day_type, is_holiday_labeled,
line_id, station_code, station_uid, station_name,
branch_code, is_branch_row, is_transfer_station, attribution_flag,
direction, time_bin_index, time_bin, bin_start_min, is_am_peak, is_pm_peak,
congestion_rate, is_operating, p95_train,
label_high, label_extreme, perceived_multiplier
```

---

## 5. 파이프라인 (실행 순서 및 현재 상태)

| # | 스크립트 | 산출물 | 상태 |
|---|---|---|---|
| 00 | `bootstrap_yeoyuro_seoul.py` | 폴더 + 마스터 8종 + config | ✅ 완료 |
| 01 | `01_build_ridership_mart.py` | station_master, alias_master, ridership_hourly_mart | ✅ 완료 |
| 03 | `03_build_stg_congestion.py` | stg_congestion (715,299행) + 검증 리포트 4종 | ✅ 완료 |
| 04 | `04_build_congestion_mart.py` | congestion_30min_mart / station_profile / edge_lookup | ✅ 완료 |
| 05 | `05_build_event_spike_mart.py` | event_spike_mart, event_effect_report | ⬜ 다음 |
| 06 | `06_build_route_graph.py` | route_edge_mart, transfer_edge_mart | ⬜ |
| 07 | `07_build_training_mart.py` | congestion_training_mart | ⬜ |
| 08 | `08_train_congestion_model.py` | baseline / LightGBM / 성능 리포트 | ⬜ |
| 09 | `09_route_scoring.py` | top-k 추천 + 설명 | ⬜ |

---

## 6. 모델 설계 v2

### Model A — 승하차 spike 검증 (통계 baseline)

```
baseline    = 동일 역 · 동일 요일 · ±8주 중앙값
spike_ratio = actual / baseline
robust_z    = (actual - median) / (1.4826 * MAD)
```
표본이 작아 std 기반 z는 이벤트 자체에 오염되므로 **MAD 사용**.

**48개월 전수 파일럿 결과 (이미 검증 완료)**

| 이벤트 | 배율 | event_strength 확정 |
|---|---|---|
| 불꽃축제 2023/24/25 (여의나루) | 5.15× / 5.41× / 5.79× | `very_strong` |
| 제야의 종 (종각, **21시~익일**) | 7.22× | `night_strong` (시간대 한정) |
| 제야의 종 (종각, 일 총량) | 1.08× | 일 총량 피처 **사용 금지** |
| 봄꽃축제 2023 **직전 주말** | 4.16× | `strong` (impact window 별도) |
| 봄꽃축제 2023 **공식 기간** | 0.93× | 공식 기간 단독 사용 금지 |
| 한양대 논술 | 1.88× | `strong` |
| 할로윈 2025 (이태원) | 1.18× | `weak` (낮은 가중치 proxy) |

`official_period` / `actual_impact_window` 분리 설계가 **데이터로 정당화되었다.**

### Model B — 기대 혼잡도 예측

**타깃**: `congestion_rate` (분기·요일유형별 평균 패턴)

**분할 (확정)**: 스냅샷 단위 시간축 고정. random split 금지.

| split | 스냅샷 | 셀 수 |
|---|---|---|
| train | 2022-12 ~ 2025-03 (6) | 389,220 |
| valid | 2025-06 ~ 2025-11 (3) | 195,507 |
| test | 2026-03, 2026-06 (2) | 130,338 |

**라벨 (확정)**

| 라벨 | 정의 | 실측 비율 |
|---|---|---|
| `label_high` | 역×방향 train p95 초과 | 5.25% |
| `label_extreme` | ≥ 100% | 0.61% |

p95 임계는 **train 구간에서만 산출**해 valid/test 누수를 차단한다.

**통과 조건 (명문화)**

> LightGBM 이 `역 × 방향 × 요일유형 × 시간대 평균` baseline 을 **valid 에서 이기지 못하면
> 모델을 폐기하고 baseline 을 서비스에 사용한다.**

이 조건을 미리 적어두는 것 자체가 방어선이다. 모델의 실제 존재 이유는 셋뿐이다.

1. 미관측 조합 보간 (신설역·결측 시간대)
2. 승하차 flow 피처로 혼잡도를 **설명** (feature importance)
3. Model C 의 base 제공

**평가 지표**: 주 지표 `label_high` 의 PR-AUC / Recall@Precision0.5, 보조 MAE·RMSE.
단일 MAE 로 우수하다고 주장하지 않는다.

### Model C — 이벤트 위험 보정 (규칙 기반)

```
final_congestion_risk = base_congestion × (1 + λ · (spike_ratio − 1) · event_confidence)
```
ML 이 아니라 **명시적 규칙**임을 강조한다. 검증된 spike 만 반영하고,
`verified_effect ∈ {weak, not_verified, negative_observed}` 인 이벤트는 **가중치 0**.

---

## 7. 그래프 설계 v2

### 7-1. 노드 / 엣지

```
Node       = station_uid (+ branch_code 접미)
Ride edge  = 같은 호선 인접역           ← 역간거리·소요시간
Transfer   = 환승역 내 호선 간 이동      ← 수도권 환승 데이터(호차/문·소요시간)
Branch     = 성수·신도림·강동·응암순환    ← 9000번대 코드
```

`route_edge_mart` 에 **`is_bidirectional`** 컬럼을 둔다. 단방향 역 9개는 한 방향만 생성한다.

### 7-2. edge cost — 전부 '분'으로 환산 (v1 수정)

```
perceived_time = travel_time_min × (1 + κ · max(0, congestion − C0) / 100)
                 C0 = 80, κ = 0.5   (혼잡 180% → 체감 1.5배)

edge_cost = perceived_time
          + transfer_penalty_min          (도보시간 + 환승인원 기반 가산)
          + event_risk_min                (이벤트 영향 역·시간대 가산)
          − seat_bonus_min                (착석 가능성 보너스)
```

`congestion_edge_lookup` 에 `perceived_multiplier` 를 미리 계산해 두었다(65,866행).

> 면접 대응: "혼잡도(%)와 시간(분)을 그대로 더하면 가중치 튜닝이 의미를 잃습니다.
> 모든 비용을 체감 이동시간(분)으로 환산해 스케일 문제를 제거했습니다."

### 7-3. 탐색

Dijkstra → Yen's K-shortest(K=5) → 6축 평가(시간·평균혼잡·**최대구간혼잡**·환승·이벤트·착석)
→ 모드별 가중합 → Top 3 + 사유 문장.

**최대구간혼잡을 별도 축**으로 두어 "평균은 낮은데 한 구간이 지옥"인 경로를 걸러낸다.

---

## 8. 착석 가능성 / 호차 추천 (표현 규정 유지)

```
seat_chance_score = w1·alighting_spike + w2·congestion_drop + w3·terminal_proximity
                  − w4·boarding_spike  − w5·current_congestion
```
`congestion_drop` 은 `congestion_station_profile` 의 인접역 간 차분으로 계산한다.

호차/문은 **"덜 붐비는 칸"이 아니라 "환승 동선상 유리한 칸"**으로만 표현한다.
근거 데이터가 375행뿐이므로 커버되지 않는 환승쌍은 **안내하지 않는다**(추정 금지).

---

## 9. 리스크 등록부

| # | 리스크 | 영향 | 완화 |
|---|---|---|---|
| R1 | Model B 가 baseline 을 못 이김 | 서사 붕괴 | 통과 조건 사전 명문화. 실패도 리포트에 기재 |
| R2 | 2호선 지선 `내선/외선` 방향 미해석 | 그래프 방향 오류 | Phase 4 전 승하차와 교차검증 (**미해결**) |
| R3 | 스냅샷 날짜가 발행일인지 관측 종료일인지 불명 | 이벤트 결합 시점 오차 | 공사 데이터 설명서 확인 (**미해결**) |
| R4 | 환승역 호선 귀속 비대칭 | 수요 피처 왜곡 | 역 총량 + 호선 귀속량 **둘 다** 피처로 |
| R5 | 호차/문 커버리지 42% | 기능 공백 | 미커버 환승쌍은 기능 비활성 |
| R6 | 이벤트 날짜 충돌(불꽃축제↔홍익대 논술 3년 연속) | spike 오귀인 | `confounding_event_ids` 로 분리, 충돌 row 제외 |
| R7 | 혼잡도 값 산식 미확인 | 해석 오류 | 데이터 설명서 확인 (**미해결**) |

---

## 10. 로드맵 (2026-08-31 기준, 납기 10월 말)

| 주차 | 작업 | 산출물 |
|---|---|---|
| 9/1 – 9/7 | Phase 2 착수: event_spike_mart, 교란요인 분리 | `05_build_event_spike_mart.py`, event_effect_report |
| 9/8 – 9/14 | 그래프 골격: route_edge / transfer_edge | `06_build_route_graph.py` |
| 9/15 – 9/28 | congestion_training_mart + baseline | `07`, baseline 성능표 |
| 9/29 – 10/12 | LightGBM + 통과 조건 판정 | `08`, congestion_model_report.md |
| 10/13 – 10/22 | route scoring + top-k 추천 | `09`, route_scoring_design.md |
| 10/23 – 10/31 | Streamlit / FastAPI / README / Docker | 데모 + 문서 |

R2·R3·R7 은 **9/7 이전에 해소**해야 Phase 4 일정이 밀리지 않는다.

---

## 11. 면접 방어 메시지 v2 (수치 포함)

> 혼잡도 원본은 분기·요일유형별 평균 패턴이라 특정 날짜의 실측값이 아닙니다.
> 그래서 이벤트 당일 혼잡도를 예측한다고 말하지 않고, 48개월 승하차로 이벤트 배율을 먼저 검증했습니다.
> 불꽃축제는 여의나루 하차가 5.2~5.8배, 제야의 종은 일 총량은 1.08배인데 21시 이후만 7.2배였습니다.
> 그래서 제야의 종은 일 총량 피처에서 빼고 시간대 피처로만 반영했습니다.

> 고혼잡 라벨은 절대 임계 대신 분위수를 썼습니다. 130% 이상이 전체의 0.087%,
> 150% 이상은 38셀뿐이라 절대 임계로는 Recall/Precision 이 통계적으로 무의미해집니다.

> 원본 파일에서 5호선 둔촌동·올림픽공원이 2호선으로 잘못 기재된 156행을 찾았습니다.
> 역번호 체계가 노선별로 배타적이라는 점을 이용해 코드 기준으로 재판정하고 교정 플래그를 남겼습니다.

> 경로 추천은 혼잡도(%)와 시간(분)을 더하는 대신, 모든 비용을 체감 이동시간으로 환산해
> 단위를 통일했습니다. 6호선 응암순환처럼 단방향 구간이 9개 있어 비대칭 엣지로 구현했습니다.

---

## 12. 금지 표현 (유지 + 추가)

**금지**: 실시간 혼잡도 예측 / 실제 객차별 혼잡도 예측 / 착석 확률 / 무정차 통과 확률 /
범위 밖 노선 포함 추천 / "이 칸이 덜 붐빈다" / **"혼잡도를 정확히 예측했다"**(baseline 대비 우위 미검증 시)

**사용**: 과거 패턴 기반 기대 혼잡도 / 혼잡 위험도 점수 / 착석 가능성 proxy /
환승 동선 기반 추천 호차·문 / 프로젝트 범위 내 대안 경로 / 이벤트성 승하차 spike 기반 보정
