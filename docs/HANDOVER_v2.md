# MetroCalm v2 인수인계 프롬프트

> 이 문서는 MetroCalm(현 여유로 서울) v1 시점 기록이다. 소급 수정하지 않는다.

> 새 대화 첫 메시지에 아래 전체를 붙여넣으면 된다.
> `---` 아래부터가 붙여넣을 내용이다.

---

# MetroCalm v2 작업 시작

서울 지하철 1~8호선 혼잡도 기반 다목적 경로 추천 시스템 **MetroCalm** 의 v2 를 진행합니다.
v1 은 완료되어 GitHub 에 올라가 있습니다: https://github.com/FlightChamp/metrocalm

## 0. 나에 대한 정보

- 개인 포트폴리오 프로젝트이며, 1금융권 데이터 직군 지원용입니다.
- 목표는 "화려한 모델"이 아니라 **데이터 엔지니어링 → 분석 → 모델링 → 서비스** 전 과정을
  정직하게 끝까지 돌린 것을 보여주는 것입니다.
- 저는 구현이 실제로 동작했는지를 출력값으로 확인하고 넘어갑니다.
  "적용했다"가 아니라 **"실행해서 이런 값이 나왔다"** 로 보고해 주세요.
- 작업 환경은 Windows / PowerShell / conda(base) / Python 3.14 입니다.
  프로젝트 경로는 `C:\Programming\MyProject\metro_calm_project` 입니다.

## 1. v1 에서 확정된 것 (바꾸지 말 것)

### 표현 규칙 — 이건 절대 어기면 안 됩니다

| 금지 | 대체 |
|---|---|
| 실시간 혼잡도 예측 | 과거 패턴 기반 **기대 혼잡도** |
| 객차별 혼잡도 예측 | 환승 동선상 유리한 호차/문 |
| 착석 확률 | 착석 가능성 **proxy 점수** |
| "이 칸이 덜 붐빈다" | (사용 금지) |

혼잡도 원본은 **분기별 요일유형 평균 패턴**이며 특정 날짜 실측값이 아닙니다.
격자 하나당 관측치가 최대 11개뿐입니다.

### 프로젝트 범위

```
1호선 서울역~청량리    2호선 전구간(성수·신정지선 포함)
3호선 지축~오금        4호선 불암산~남태령
5호선 방화~하남검단산 / 방화~마천
6호선 응암~신내        7호선 장암~온수        8호선 암사역사공원~모란
```
9호선·신분당선 등 서울교통공사 관할 밖은 제외합니다.

### 핵심 아키텍처

- 사용자 입력은 **역 단위**, 내부 그래프·혼잡도는 **호선별 station_uid** 단위.
  환승역에서 첫 승차 노선은 알고리즘이 후보로 비교하며, **최초 승차는 환승으로 세지 않습니다**
  (가상 노드 `V_ORIGIN_*` / `V_DEST_*`, origin_access·destination_exit 엣지).
- 노선도 좌표의 source of truth 는 `data/master/metrocalm_vector_map_coordinate_workbook.xlsx`
  (5120×2880). **자동 graph layout(spring/kamada_kawai) 사용 금지.**
- 방면 표기는 **경로상 다음 역** 기준입니다(예: "아차산 방면").
  종점 기준은 지선·순환에서 예외가 많아 폐기했습니다(`bound_label` 에 참고용으로만 보관).

### 그래프 모델링 — 과거에 깨진 지점들

| 문제 | 대응 |
|---|---|
| 분기역 단일 노드 → 지선 승객이 통과 | 성수·신도림·강동 본선/지선 노드 분리 |
| 응암순환 `구산 → 응암 → 역촌` 환승 0회 | `6_응암@eungam_loop` 분리 + 재승차 환승 엣지 |
| `구산 → 응암 → 새절 → 응암 → 역촌` U턴이 재승차 우회 | **역 재방문 금지** 제약 |
| 환승 호차/문이 진행 방향 무시 | 경로 앞뒤 역으로 방면 매칭 |

**U턴은 배차가 긴 낮 시간대에만 나타나 08:30 검증에서 놓쳤습니다.**
그래서 회귀 테스트는 5개 시간대(08:30/11:30/13:30/19:00/22:00)로 돌립니다.

### 사전 등록한 모델 판정 (뒤집지 말 것)

> LightGBM 이 `역×방향×요일×시간대 평균` baseline 을 valid 에서 이기지 못하면
> 모델을 폐기하고 baseline 을 서비스에 사용한다. → **판정: baseline 채택**

Skill Score: Regime A −0.464 / Regime B −0.012.
모델(Model-2, 역 식별자 제외)은 **미관측 역 보간용**으로만 유지합니다.

## 2. v1 확인값 (재현 시 이 값이 나와야 정상)

| 항목 | 값 |
|---|---|
| 승하차 원본 / mart | 797,946행 / 15,957,680행 |
| 혼잡도 long / 범위 내 | 715,299 / 715,065 (11 스냅샷, 스키마 3종) |
| 호선 오기입 교정 | 156행 (5호선 둔촌동·올림픽공원이 2호선으로 기재) |
| 그래프 | 281 노드 / 승차 536 / 환승 82 / 강연결 True |
| smoke test | 9/9 PASS |
| 학습 마트 | 706,678행, label_high 5.25% |
| baseline L2 | MAE 2.364 / R² 0.947 / PR-AUC 0.857 |
| Ablation B→C | R² 0.450 → 0.734 (승하차 흐름 피처 기여) |
| 고혼잡 130%+ 구간 MAE | 9.227 (전체 평균의 약 4배) |
| DiD | 처치역 28 / 대조역 248 / 평행추세 위반 28행 |
| 불꽃축제 | 전후비교 3.674 → DiD 순효과 3.403 |
| 경로 평가 | 08:30 대안 3/13, 18:00 대안 0/13 |
| 스키마 검증 | 테이블 PASS 7 / 교차 PASS 10 |
| pytest | **41 passed** |

## 3. 평가 체계 (v1 에서 확정)

| 레이어 | 문제 유형 | 지표 |
|---|---|---|
| A 데이터 품질 | 스키마 검증 | pandera + 교차검증 |
| B 이벤트 효과 | **인과 추정** | spike_ratio, control_ratio, DiD, absolute_lift, 평행추세 |
| C 혼잡도 예측 | 회귀 + **불균형 분류** | Baseline ladder, Skill Score, PR-AUC, Recall@P≥0.5, Ablation, SHAP |
| D 경로 추천 | **다목적 최적화** | 3축 Pareto, Stretch Factor, directed-edge Jaccard, 임계 민감도 |
| E 착석 가능성 | **검증 불가** | 정답 데이터 없음 — 한계로 명시 |

**지표는 표준을 쓰되 설계 파라미터(κ=0.5, C0=80, 임계 15분/15%p/5분, λ=0.3 등)는
우리 판단이며, 문서에 "근거 있는 상수가 아니라 초기값"이라고 명시돼 있습니다.**
이 구분을 유지해 주세요.

### 기각한 기법과 이유 (다시 제안하지 말 것)

| 후보 | 기각 사유 |
|---|---|
| LSTM / Transformer / GNN | 혼잡도 스냅샷이 11개. 격자당 관측치 11개에 딥러닝은 과적합 |
| NDCG / MAP | 정답 선호도 라벨(사용자 피드백)이 없음 |
| MASE | 시계열 naive forecast 를 분모로 쓰는 지표라 격자 회귀에 정의 불일치 → Skill Score |
| MLflow | 학습이 사실상 1회 |
| Great Expectations | pandera 로 충분 |
| A/B 테스트 | 사용자 없음 |

## 4. 파일 구조

```
metro_calm_project/
├── README.md, LICENSE, requirements.txt
├── .streamlit/config.toml          # primaryColor #8D7150 (BOM 없이 저장할 것)
├── bootstrap_metrocalm.py
├── station_routing.py              # app/streamlit 와 동일본 유지
├── scripts/
│   ├── 01_build_ridership_mart.py      03_build_stg_congestion.py
│   ├── 04_build_congestion_mart.py     05_build_event_spike_mart.py
│   ├── 05b_did_event_effect.py         06_build_route_graph.py
│   ├── 06b_smoke_test_graph.py         07_build_training_mart.py
│   ├── 08_train_congestion_model.py    08b_evaluate_congestion_model.py
│   ├── 09_route_scoring_prototype.py   10_evaluate_routes.py
│   ├── 11_validate_schemas.py          12_build_display_masters.py
│   ├── 14_import_map_workbook.py       15_build_headway_mart.py
├── app/streamlit/metrocalm_app.py, station_routing.py
├── tests/test_station_routing.py   # 41 tests
├── data/master/**                  # 커밋됨 (프로젝트 정의)
├── data/{raw,staging,interim,marts}/  # .gitignore (스크립트로 재생성)
├── models/                         # .gitignore
├── docs/{DATA,evaluation_strategy,model_card_congestion,PROJECT_PLAN_v2}.md
├── docs/images/*.png               # README 스크린샷 7장
└── reports/{data_quality,model,route,figures}/
```

`13_build_map_layout.py` 는 좌표 워크북으로 대체되어 삭제했습니다. 되살리지 마세요.

## 5. v1 의 알려진 한계 (v2 후보)

1. **고혼잡 구간 오차** — ~30% 구간 MAE 1.474 vs 130%+ 구간 9.227.
   현재는 절대값 대신 상대 순위(p95 초과)로 우회.
2. **`label_high` 기준 불일치** — station holdout 역은 train 에 없어 전역 p95 로 대체.
   Regime B 분류 지표에 편향이 있음.
3. **Pareto front 는 K개 후보 안에서의 front** — Yen's K-shortest 집합에 한정.
4. **출발 시각의 time_bin 을 경로 전체에 고정** — 시간 전진 미반영.
5. **환승 대기시간이 30분 bin 평균** — 특정 시각의 실제 다음 열차가 아님.
   균등 도착 가정(기댓값 = 배차/2), 최초 승차 전 대기는 미반영.
6. **미해결** — 혼잡도 산식(정원 대비 %인지), 스냅샷 날짜의 의미(관측 종료일/발행일),
   8호선 오차가 큰 원인(MAE 3.600, p95 오차 13.9).
7. **노선도 도심부 라벨 밀집** — 서울역·시청·충정로 주변.
8. **Plotly 최대 확대 배율 제한 불가** — JS 커스텀 컴포넌트 필요.

## 6. v2 후보 (우선순위는 함께 정하고 싶습니다)

1. **정밀 시각표 기반 next departure** — v1 은 30분 bin 평균.
   원본 `서울교통공사_서울 도시철도 열차운행시각표_20260616.csv` 가 `data/raw/train_operation/` 에 있음.
2. **고혼잡 전용 분류 모델** — 회귀 대신 `label_high` 직접 학습.
3. **경로 탐색 시 시간 전진 반영** — 구간마다 도착 예상 시각의 time_bin 사용.
4. **역 유사도 기반 보간** — 미관측 역에 노선 평균 대신 유사 역 가중 평균.
5. FastAPI / Dockerfile.
6. SVG 커스텀 노선도 컴포넌트.

## 7. 작업 방식 요청

- 스크립트를 수정하면 **실제로 실행해서 출력값을 보여주세요.** 확인값과 대조하겠습니다.
- 파일을 여러 곳 고칠 때는 **한 번에 묶어 패치하지 말고** 단계별로 저장·검증해 주세요.
  (v1 에서 패치 스크립트가 중간에 실패해 수정분이 통째로 날아간 적이 여러 번 있었습니다.)
- 파일을 주실 때는 Downloads 에 남지 않도록 `Move-Item` 기준 명령으로 안내해 주세요.
- `data/master/station_master.csv` 등 프로젝트가 생성한 실데이터를 덮어쓰지 마세요.
- 그래프·라우팅을 건드리면 반드시 `pytest tests/ -q` 로 41개 통과를 확인해 주세요.
- 새 기능을 추가하면 회귀 테스트도 함께 추가해 주세요.
  **테스트가 실제로 버그를 잡는지** 일시적으로 수정을 되돌려 확인하는 것까지 포함합니다.

먼저 위 내용을 이해했는지 확인하고, v2 후보 중 무엇부터 할지 의견을 주세요.
