# README 추가 문구

배포 URL 확정 후 아래를 README 에 반영한다.
`__배포URL__` 부분만 바꿔 넣으면 된다.

---

## (A) README 최상단, 프로젝트 소개 바로 아래

```markdown
## Live Demo

- App: [여유로 서울](__배포URL__)
- Source: [github.com/FlightChamp/yeoyuro-seoul](https://github.com/FlightChamp/yeoyuro-seoul)

> 표시되는 혼잡도는 실시간 측정값이 아니라 **과거 패턴 기반 기대 혼잡도**입니다.
> 원본은 분기별 요일유형 평균 패턴이며, 특정 날짜의 실측값이 아닙니다.
> 환승 대기시간은 30분 단위 **평균 배차 기반 추정**(균등 도착 가정, 기댓값 = 배차/2)이며,
> **최초 승차 전 대기시간은 포함하지 않습니다.**
> 착석 관련 표시는 확률이 아니라 **가능성 proxy 점수**이며, 검증할 정답 데이터가 없어 한계로 명시합니다.

무료 호스팅 특성상 접속이 없으면 앱이 대기 상태로 전환됩니다.
첫 접속 시 기동에 20~30초가 걸릴 수 있습니다.
```

---

## (B) README 하단, 재현 방법 섹션 근처

```markdown
## Deployment

### 플랫폼

Streamlit Community Cloud. 무료 티어이며 GitHub `main` 브랜치에 push 하면 자동 재배포됩니다.

- Main file path: `app/streamlit/yeoyuro_seoul_app.py`
- Python: 3.12
- Secrets: 없음 (외부 API·인증을 사용하지 않습니다)

Hugging Face Spaces 도 검토했으나, 현재 Spaces 는 Gradio / Docker / static HTML SDK 만
생성할 수 있고 컴퓨트 기반 Space 생성에 유료 플랜을 요구하여 제외했습니다.

### 배포용 데이터 구성

배포 환경에서는 파이프라인을 실행하지 않습니다. 앱이 런타임에 읽는 산출물만 저장소에 포함합니다.

| 포함 | 내용 |
|---|---|
| `data/master/` | 역·노선·노선도 좌표·이벤트 마스터 |
| `data/marts/` | 경로 그래프 엣지, 혼잡도 lookup·프로파일, 배차, 환승 팁, 이벤트 효과, 경로 평가 |
| `reports/` | 데이터 품질·모델·경로 평가 리포트 |

| 제외 | 이유 |
|---|---|
| `data/raw/` | 서울교통공사 원본. 재배포 제약이 있으며 앱이 읽지 않습니다 |
| `data/staging/`, `data/interim/` | 중간 산출물. 스크립트로 재생성됩니다 |
| 학습 마트(706,678행), 승하차 마트(15,957,680행) | 학습·분석 전용. 앱이 읽지 않습니다 |
| `models/` | 사전 등록한 판정에 따라 baseline 을 서비스에 사용하므로 런타임에 불필요합니다 |

### 실행

배포 환경과 로컬 모두 인자 없이 실행됩니다. 저장소 루트는 자동 인식합니다.

    streamlit run app/streamlit/yeoyuro_seoul_app.py

### 재현용 파이프라인과 배포용 앱의 차이

배포된 앱은 **로컬과 동일한 산출물을 읽으므로 기능과 결과가 같습니다.**
다만 산출물을 만드는 과정은 저장소에 포함되지 않습니다.

| | 필요 데이터 | 필요 패키지 |
|---|---|---|
| 배포용 앱 실행 | 커밋된 master / marts / reports | `requirements.txt` |
| 전체 파이프라인 재현 | `data/raw/` 에 원본을 직접 배치 | `requirements.txt` + `requirements-dev.txt` |

전체 재현은 원본 데이터를 출처에서 내려받아 `data/raw/` 에 둔 뒤 `scripts/` 를 순서대로 실행합니다.

### 한계

- 혼잡도는 과거 패턴 기반 기대값이며, 격자 하나당 관측치가 최대 11개입니다.
- 출발 시각의 time_bin 을 경로 전체에 고정합니다(시간 전진 미반영).
- 환승 대기시간은 30분 bin 평균이며 특정 시각의 다음 열차가 아닙니다.
- 착석 가능성은 검증할 정답 데이터가 없습니다.
- 서울교통공사 관할 1~8호선만 다루며 9호선·신분당선 등은 범위 밖입니다.
```
