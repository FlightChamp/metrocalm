# =====================================================================
#  여유로 서울 - setup_and_run.ps1
#  프로젝트 폴더 생성 -> 스크립트 배치 -> 원본 데이터 분류 -> 파이프라인 실행
#
#  사용법 (PowerShell):
#     cd C:\Programming\MyProject\metro_calm_project
#     .\setup_and_run.ps1 -RawDir "원본데이터가_있는_폴더"
#
#  예시:
#     .\setup_and_run.ps1 -RawDir "C:\Programming\MyProject\metro_calm_project 자료실"
#
#  -RawDir 은 하위 폴더까지 재귀 탐색한다. 종류별로 나눠져 있어도 최상위 폴더만 주면 된다.
#
#  실행 정책 오류가 나면 한 번만:
#     Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
# =====================================================================

param(
    [string]$ProjDir = "C:\Programming\MyProject\metro_calm_project",
    [string]$ScriptSrc = "$env:USERPROFILE\Downloads",
    [string]$RawDir = "",
    [switch]$SkipCopy      # 원본 데이터를 이미 배치했다면 지정
)

$ErrorActionPreference = "Stop"

function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "  [!]  $msg" -ForegroundColor Yellow }

# ---------------------------------------------------------------------
Step "0. 환경 확인"

if (-not (Test-Path $ProjDir)) {
    New-Item -ItemType Directory -Path $ProjDir -Force | Out-Null
    Ok "프로젝트 폴더 생성: $ProjDir"
} else {
    Ok "프로젝트 폴더 확인: $ProjDir"
}
Set-Location $ProjDir

$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { throw "python 을 찾을 수 없습니다. conda 환경을 활성화하세요." }
Ok "python: $($py.Source)"

$needed = @(
    "bootstrap_yeoyuro_seoul.py",
    "01_build_ridership_mart.py",
    "03_build_stg_congestion.py",
    "04_build_congestion_mart.py"
)
$missing = @()
foreach ($f in $needed) {
    if (-not (Test-Path (Join-Path $ScriptSrc $f))) { $missing += $f }
}
if ($missing.Count -gt 0) {
    throw "다음 파일을 $ScriptSrc 에서 찾을 수 없습니다:`n  " + ($missing -join "`n  ")
}
Ok "스크립트 4개 확인 ($ScriptSrc)"

# ---------------------------------------------------------------------
Step "1. bootstrap 실행 (폴더 트리 + master 8종)"

Copy-Item (Join-Path $ScriptSrc "bootstrap_yeoyuro_seoul.py") $ProjDir -Force
python bootstrap_yeoyuro_seoul.py --root .
if ($LASTEXITCODE -ne 0) { throw "bootstrap 실패" }

# ---------------------------------------------------------------------
Step "2. 파이프라인 스크립트 배치"

foreach ($f in $needed[1..3]) {
    Copy-Item (Join-Path $ScriptSrc $f) (Join-Path $ProjDir "scripts") -Force
    Ok $f
}

# ---------------------------------------------------------------------
Step "3. 원본 데이터 분류 배치"

if ($SkipCopy) {
    Warn "-SkipCopy 지정됨. 원본 복사를 건너뜁니다."
}
elseif ([string]::IsNullOrWhiteSpace($RawDir)) {
    Warn "-RawDir 미지정. 원본 복사를 건너뜁니다."
    Warn "원본 폴더를 지정해 다시 실행하거나, data\raw\* 아래에 직접 넣으세요."
}
elseif (-not (Test-Path $RawDir)) {
    throw "원본 폴더가 없습니다: $RawDir"
}
else {
    # 파일명 패턴 -> 목적지 폴더  (공백/언더스코어 표기 모두 매칭)
    $routes = @(
        @{ Pattern = "*이용인원*.xlsx";        Dest = "data\raw\ridership_monthly" },
        @{ Pattern = "*혼잡도정보*";           Dest = "data\raw\congestion_quarterly" },
        @{ Pattern = "*환승역*환승인원*";      Dest = "data\raw\transfer_volume" },
        @{ Pattern = "*도시철도*환승*데이터*"; Dest = "data\raw\transfer_detail" },
        @{ Pattern = "*역간거리*";             Dest = "data\raw\route_distance_time" },
        @{ Pattern = "*열차운행현황*";         Dest = "data\raw\train_operation" },
        @{ Pattern = "*승객유형별*";           Dest = "data\raw\train_operation" }
    )
    foreach ($r in $routes) {
        $dest = Join-Path $ProjDir $r.Dest
        New-Item -ItemType Directory -Path $dest -Force | Out-Null
        # 하위 폴더까지 재귀 탐색. 단, 프로젝트 내부(data\raw 등)는 제외해 중복 복사를 막는다.
        $files = Get-ChildItem -Path $RawDir -Filter $r.Pattern -File -Recurse -ErrorAction SilentlyContinue |
                 Where-Object { $_.FullName -notlike (Join-Path $ProjDir "data\raw\*") }
        if ($files.Count -gt 0) {
            $files | Copy-Item -Destination $dest -Force
            Ok "$($r.Pattern) -> $($r.Dest)  ($($files.Count)개)"
        } else {
            Warn "$($r.Pattern) : 해당 파일 없음"
        }
    }
}

# 배치 결과 점검
$counts = [ordered]@{
    "ridership_monthly (기대 48)"    = "data\raw\ridership_monthly"
    "congestion_quarterly (기대 11)" = "data\raw\congestion_quarterly"
    "transfer_volume (기대 9)"       = "data\raw\transfer_volume"
    "route_distance_time (기대 3)"   = "data\raw\route_distance_time"
}
Write-Host ""
foreach ($k in $counts.Keys) {
    $n = (Get-ChildItem -Path (Join-Path $ProjDir $counts[$k]) -File -ErrorAction SilentlyContinue).Count
    Write-Host ("  {0,-34} {1}개" -f $k, $n)
}

# ---------------------------------------------------------------------
Step "4. pyarrow 확인 (parquet 저장용)"

$prev = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& python -c "import pyarrow" 2>&1 | Out-Null
$paOk = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $prev
if (-not $paOk) {
    Warn "pyarrow 미설치 -> parquet 대신 csv.gz 로 저장됩니다. 설치를 권장합니다."
    Warn "설치: pip install pyarrow"
} else {
    Ok "pyarrow 사용 가능"
}

# ---------------------------------------------------------------------
Step "5. 파이프라인 실행"

$nRide = (Get-ChildItem "data\raw\ridership_monthly" -File -ErrorAction SilentlyContinue).Count
$nCong = (Get-ChildItem "data\raw\congestion_quarterly" -File -ErrorAction SilentlyContinue).Count

if ($nRide -eq 0) {
    Warn "승하차 원본이 없어 01 단계를 건너뜁니다."
} else {
    Write-Host "`n-- 01_build_ridership_mart (48개월, 수 분 소요) --" -ForegroundColor Cyan
    python scripts\01_build_ridership_mart.py --raw data\raw\ridership_monthly --root .
    if ($LASTEXITCODE -ne 0) { throw "01 단계 실패" }
}

if ($nCong -eq 0) {
    Warn "혼잡도 원본이 없어 03/04 단계를 건너뜁니다."
} else {
    Write-Host "`n-- 03_build_stg_congestion --" -ForegroundColor Cyan
    python scripts\03_build_stg_congestion.py --raw-dir data\raw\congestion_quarterly --out-root .
    if ($LASTEXITCODE -ne 0) { throw "03 단계 실패" }

    Write-Host "`n-- 04_build_congestion_mart --" -ForegroundColor Cyan
    python scripts\04_build_congestion_mart.py --root .
    if ($LASTEXITCODE -ne 0) { throw "04 단계 실패" }
}

# ---------------------------------------------------------------------
Step "완료 - 생성물 확인"

$outs = @(
    "data\master\station_master.csv",
    "data\master\transfer_station_master.csv",
    "data\marts\ridership_hourly_mart.parquet",
    "data\marts\ridership_hourly_mart.csv.gz",
    "data\staging\stg_congestion.parquet",
    "data\staging\stg_congestion.csv.gz",
    "data\marts\congestion_30min_mart.parquet",
    "data\marts\congestion_30min_mart.csv.gz",
    "data\marts\congestion_station_profile.csv",
    "reports\data_quality\congestion_mart_report.md"
)
foreach ($o in $outs) {
    if (Test-Path $o) {
        $sz = [math]::Round((Get-Item $o).Length / 1MB, 2)
        Write-Host ("  [OK] {0,-50} {1} MB" -f $o, $sz) -ForegroundColor Green
    }
}
Write-Host "`n리포트: reports\data_quality\congestion_mart_report.md" -ForegroundColor Cyan
