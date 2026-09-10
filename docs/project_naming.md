# Project Naming

배포 전 프로젝트명을 MetroCalm에서 **여유로 서울 (Yeoyuro Seoul)** 로 변경했다.

## 변경 내용

| 항목 | 값 |
|---|---|
| 프로젝트명 | 여유로 서울 (Yeoyuro Seoul) |
| 부제 | 서울 지하철 1~8호선 혼잡도 기반 쾌적 경로 추천 시스템 |
| 슬로건 | 최단시간을 넘어, 최적의 쾌적함을 제안합니다. |
| 저장소 | `FlightChamp/yeoyuro-seoul` |
| 배포 URL 슬러그 | `yeoyuro-seoul` |
| 변경 시점 | 최초 public 배포 직전 |

## 변경 이유

**`여유로`는 이 서비스의 가치를 두 겹으로 담는다.** 여유로운 이동이라는 뜻과,
길(路)이라는 뜻이 함께 읽힌다. 경로 추천 시스템에 두 의미가 모두 들어맞는다.
`서울`은 프로젝트의 공간적 범위가 서울교통공사 관할 1~8호선임을 이름에서 밝힌다.

**MetroCalm은 영어권에서 오인될 소지가 있었다.** "Calm"은 동명의 명상 앱이
사실상 선점한 단어라 "출퇴근길용 명상 앱"으로 읽히기 쉽다. 또한 calm은 심리 상태를
가리키는 반면 이 시스템이 다루는 것은 물리적 혼잡도이므로, 은유가 한 단계 멀었다.

**주 독자가 한국어 화자다.** 공공데이터 기반 시민 편의 서비스라는 성격과도
한국어 이름이 더 맞는다.

## 표기 체계

용도별로 세 가지 표기를 쓴다.

| 용도 | 표기 |
|---|---|
| 화면·문서 표시명 | 여유로 서울 |
| 영문 기술 설명 | Yeoyuro Seoul |
| 저장소·URL·파일명 | `yeoyuro-seoul` / `yeoyuro_seoul_*` |

## 변경한 파일명

```
app/streamlit/metrocalm_app.py
  -> app/streamlit/yeoyuro_seoul_app.py

bootstrap_metrocalm.py
  -> bootstrap_yeoyuro_seoul.py

data/master/metrocalm_vector_map_coordinate_workbook.xlsx
  -> data/master/yeoyuro_seoul_vector_map_coordinate_workbook.xlsx
```

GitHub 파일 트리는 저장소 첫 화면에서 바로 보이므로, 진입점 파일명도 함께 정리했다.
`git mv` 를 사용해 변경 이력이 추적되도록 했다.

## 의도적으로 남긴 것

아래는 리브랜딩 대상이 아니다. 남아 있는 것이 정상이다.

**`docs/HANDOVER_v2.md`** — v1 시점의 인수인계 기록이다. 소급 수정하면
"그 시점에 무엇을 알고 있었는가"라는 기록으로서의 가치가 사라진다.
문서 상단에 현재 프로젝트명을 밝히는 한 줄만 추가했다.

**`14_import_map_workbook.py` 의 `WORKBOOK_LEGACY`** — 이전 파일명으로 저장된
좌표 워크북도 계속 읽을 수 있도록 남긴 fallback 이다. 신규 파일명을 먼저 찾고,
없을 때만 이전 이름으로 되짚으며 경고를 출력한다.

**`docs/dwell_time_adjustment_report.md` 의 conda 환경명 `metrocalm`** —
실제로 사용 중인 환경 이름이므로 사실 기록이다.
