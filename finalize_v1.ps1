# =====================================================================
#  finalize_v1.ps1 — MetroCalm v1 마무리
#    1) 사용하지 않는 파일 정리
#    2) 문서 배치 확인
#    3) 테스트 실행
#    4) 커밋
#  사용법
#    .\finalize_v1.ps1            # 점검만
#    .\finalize_v1.ps1 -Apply     # 정리 + 커밋
# =====================================================================
param([switch]$Apply)
function Step($m){ Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Ok($m){ Write-Host "  [OK] $m" -ForegroundColor Green }
function Warn($m){ Write-Host "  [!]  $m" -ForegroundColor Yellow }

Step "1. 사용하지 않는 파일"
$stale = @(
  "scripts\13_build_map_layout.py",
  "scripts\10_evaluate_routes_v1_backup.py",
  "data\master\station_map_layout.csv",
  "data\master\map_edge_layout.csv",
  "data\master\metrocalm_project_station_click_points.xlsx",
  "reports\figures\subway_map_preview.png",
  "reports\figures\pareto_routes_0830.png",
  "reports\route\route_eval_cases.csv",
  "reports\route\route_eval_summary.csv",
  "reports\route\route_eval_detail.csv",
  "reports\route\route_recommendation_eval.md",
  "reports\route\pareto_front_scatter.png",
  "data\marts\route_edge_mart.csv.gz",
  "data\marts\transfer_edge_mart.csv.gz",
  "data\marts\transfer_tip_mart.csv.gz"
)
$found = $stale | Where-Object { Test-Path $_ }
if ($found) { $found | ForEach-Object { Write-Host "     $_" } }
else { Ok "정리할 파일 없음" }

if (Test-Path "notebooks") {
  $n = (Get-ChildItem notebooks -File -Recurse | Where-Object { $_.Name -ne ".gitkeep" }).Count
  if ($n -eq 0) { Write-Host "     notebooks\  (비어 있음)" }
}

Step "2. 문서"
foreach ($f in @("README.md","LICENSE","requirements.txt","docs\DATA.md",
                 "docs\evaluation_strategy.md","docs\model_card_congestion.md",
                 ".streamlit\config.toml")) {
  if (Test-Path $f) { Ok $f } else { Warn "누락: $f" }
}

Step "3. 테스트"
if (-not $Apply) {
  Write-Host "  (dry-run) -Apply 를 붙이면 정리 후 테스트와 커밋을 실행합니다." -ForegroundColor Yellow
  exit 0
}

foreach ($f in $found) { Remove-Item $f -Force -ErrorAction SilentlyContinue; Ok "삭제 $f" }
if ((Test-Path "notebooks") -and
    ((Get-ChildItem notebooks -File -Recurse | Where-Object { $_.Name -ne ".gitkeep" }).Count -eq 0)) {
  git rm -r --cached notebooks -q 2>$null
  Remove-Item -Recurse -Force notebooks
  Ok "notebooks 제거"
}

python -m pytest tests\ -q
if ($LASTEXITCODE -ne 0) { Warn "테스트 실패. 커밋을 중단합니다."; exit 1 }
Ok "테스트 통과"

Step "4. 커밋"
git add -A
git commit -m @"
chore(v1): 문서 갱신과 잔재 정리로 v1 마무리

- README: 15_build_headway_mart 재현 절차, 그래프 모델링 결함 사례 추가
- evaluation_strategy: D-6 그래프 결함/회귀테스트, D-7 환승 대기시간
- model_card: 경로 스코어링 사용처 추가
- 회귀 테스트 15 -> 41 (응암 U턴, 역 재방문, 환승 대기, 방면 매칭)
- 사용하지 않는 자동 레이아웃 산출물과 구버전 리포트 제거
- requirements: networkx 제거(더 이상 사용하지 않음)
"@
git --no-pager log --oneline -3
Write-Host "`n  push: git push" -ForegroundColor Yellow
