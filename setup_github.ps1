# =====================================================================
#  setup_github.ps1 — 여유로 서울 GitHub 업로드 준비
#
#  하는 일
#    1) 폴더 구조 정리 (docs/, tests/, app/streamlit/)
#    2) .gitignore / README.md / LICENSE / docs 배치
#    3) 커밋 대상 점검 (용량·민감파일)
#    4) git init 후 의미 단위로 나눈 커밋 생성
#    5) origin 연결 및 push 안내
#
#  사용법
#    cd C:\Programming\MyProject\metro_calm_project
#    .\setup_github.ps1                 # 점검만 (dry-run)
#    .\setup_github.ps1 -Commit         # 실제 커밋 생성
#    .\setup_github.ps1 -Commit -Push   # 커밋 후 push
# =====================================================================

param(
    [string]$Repo = "https://github.com/FlightChamp/yeoyuro-seoul.git",
    [switch]$Commit,
    [switch]$Push
)
$ErrorActionPreference = "Stop"
function Step($m) { Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  [OK] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  [!]  $m" -ForegroundColor Yellow }

# ---------------------------------------------------------------- 0. 사전 점검
Step "0. 사전 점검"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "git 이 설치되어 있지 않습니다." }
Ok "git $(git --version)"

$need = @(".gitignore", "README.md", "LICENSE", "requirements.txt")
$missing = $need | Where-Object { -not (Test-Path $_) }
if ($missing) { Warn "누락: $($missing -join ', ')  → 제공된 파일을 먼저 배치하세요." }
else { Ok "루트 필수 파일 4종 확인" }

# ---------------------------------------------------------------- 1. 폴더 정리
Step "1. 폴더 구조 정리"
foreach ($d in @("docs", "tests", "app\streamlit", "scripts",
                 "data\raw", "data\staging", "data\interim", "data\marts",
                 "data\master", "models", "reports")) {
    New-Item -ItemType Directory -Path $d -Force | Out-Null
}
# 빈 폴더도 클론 후 재현되도록 .gitkeep
foreach ($d in @("data\raw", "data\staging", "data\interim", "data\marts", "models")) {
    $k = Join-Path $d ".gitkeep"
    if (-not (Test-Path $k)) { New-Item -ItemType File -Path $k -Force | Out-Null }
}
Ok "폴더 및 .gitkeep 정리"

# ---------------------------------------------------------------- 2. 민감/대용량 점검
Step "2. 커밋 대상 점검"
git init -q 2>$null | Out-Null
$tracked = git ls-files --cached --others --exclude-standard
$big = @()
foreach ($f in $tracked) {
    if (Test-Path $f) {
        $sz = (Get-Item $f).Length
        if ($sz -gt 5MB) { $big += [pscustomobject]@{ MB = [math]::Round($sz/1MB,1); Path = $f } }
    }
}
if ($big) {
    Warn "5MB 초과 파일이 커밋 대상에 있습니다:"
    $big | Sort-Object MB -Descending | ForEach-Object { Write-Host ("     {0} MB  {1}" -f $_.MB, $_.Path) }
    Warn ".gitignore 를 확인하세요."
} else { Ok "5MB 초과 파일 없음" }

$secretPat = @("*.env", "*.pem", "*.key", "*secret*", "*credential*", "*token*")
$sec = @()
foreach ($p in $secretPat) { $sec += Get-ChildItem -Recurse -File -Filter $p -ErrorAction SilentlyContinue }
$sec = $sec | Where-Object { $_.FullName -notmatch "\\\.git\\|\\\.venv\\|node_modules" }
if ($sec) { Warn "민감 파일 후보: $($sec.Name -join ', ')" } else { Ok "민감 파일 없음" }

$total = ($tracked | Where-Object { Test-Path $_ } | ForEach-Object { (Get-Item $_).Length } | Measure-Object -Sum).Sum
Write-Host ("`n  커밋 예정 용량: {0:N1} MB / {1} 파일" -f ($total/1MB), $tracked.Count)

if (-not $Commit) {
    Write-Host "`n  (dry-run) 실제 커밋하려면 -Commit 을 붙여 실행하세요." -ForegroundColor Yellow
    exit 0
}

# ---------------------------------------------------------------- 3. 커밋
Step "3. 의미 단위 커밋 생성"
git symbolic-ref HEAD refs/heads/main 2>$null | Out-Null

function Commit($msg, [string[]]$paths) {
    $exists = $paths | Where-Object { Test-Path $_ }
    if (-not $exists) { Warn "건너뜀 (대상 없음): $msg"; return }
    git add -- $exists 2>$null
    $staged = git diff --cached --name-only
    if (-not $staged) { Warn "건너뜀 (변경 없음): $msg"; return }
    git commit -q -m $msg
    Ok $msg
}

Commit "chore: 저장소 초기 설정(.gitignore, LICENSE, requirements)" `
       @(".gitignore", "LICENSE", "requirements.txt")
Commit "docs: README 및 프로젝트 문서" `
       @("README.md", "docs")
Commit "feat(master): 프로젝트 범위·환승역·이벤트 캘린더 마스터" `
       @("data\master", "bootstrap_yeoyuro_seoul.py", "config")
Commit "feat(pipeline): 승하차·혼잡도 마트 구축 (01, 03, 04)" `
       @("scripts\01_build_ridership_mart.py", "scripts\03_build_stg_congestion.py",
         "scripts\04_build_congestion_mart.py")
Commit "feat(event): 이벤트 spike 검증과 이중차분 순효과 (05, 05b)" `
       @("scripts\05_build_event_spike_mart.py", "scripts\05b_did_event_effect.py")
Commit "feat(graph): 경로 그래프 구축과 구조 검증 (06, 06b)" `
       @("scripts\06_build_route_graph.py", "scripts\06b_smoke_test_graph.py")
Commit "feat(model): 학습 마트·모델 학습·표준 지표 평가 (07, 08, 08b)" `
       @("scripts\07_build_training_mart.py", "scripts\08_train_congestion_model.py",
         "scripts\08b_evaluate_congestion_model.py")
Commit "feat(routing): 경로 스코어링과 3축 Pareto 평가 (09, 10)" `
       @("scripts\09_route_scoring_prototype.py", "scripts\10_evaluate_routes.py")
Commit "feat(validation): pandera 스키마 검증 (11)" `
       @("scripts\11_validate_schemas.py")
Commit "feat(map): 역 단위 마스터와 벡터 노선도 좌표 (12, 14)" `
       @("scripts\12_build_display_masters.py", "scripts\14_import_map_workbook.py")
Commit "feat(app): Streamlit 서비스와 역 단위 라우팅" `
       @("app", "station_routing.py")
Commit "test: 서울역→강남 regression test" `
       @("tests")
Commit "docs(reports): 파이프라인 실행 리포트와 검증 결과" `
       @("reports")
Commit "chore: 실행 스크립트" `
       @("setup_and_run.ps1", "setup_github.ps1")

$rest = git status --porcelain
if ($rest) { Commit "chore: 잔여 파일 정리" @(".") }

Write-Host "`n[커밋 로그]" -ForegroundColor Cyan
git --no-pager log --oneline

# ---------------------------------------------------------------- 4. push
Step "4. 원격 연결"
$hasOrigin = git remote | Where-Object { $_ -eq "origin" }
if (-not $hasOrigin) { git remote add origin $Repo; Ok "origin 추가: $Repo" }
else { Ok "origin 이미 설정됨" }

if ($Push) {
    git branch -M main
    git push -u origin main
    Ok "push 완료"
} else {
    Write-Host "`n  push 하려면:" -ForegroundColor Yellow
    Write-Host "    git branch -M main"
    Write-Host "    git push -u origin main"
}
