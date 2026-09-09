# =====================================================================
#  triage_downloads.ps1 — Downloads 잔여 파일 정리
#
#  Downloads 의 여유로 서울 관련 파일을 3가지로 분류한다.
#    [중복]  프로젝트 파일과 내용이 같다        → 삭제
#    [이동]  프로젝트에 없는 파일이다            → 지정 위치로 이동
#    [확인]  프로젝트 파일과 내용이 다르다       → 사람이 판단
#    [보호]  옮기면 안 되는 파일이다             → 손대지 않음
#
#  사용법
#    cd C:\Programming\MyProject\metro_calm_project
#    .\triage_downloads.ps1            # 분류만 (dry-run)
#    .\triage_downloads.ps1 -Apply     # 실제 삭제/이동 수행
# =====================================================================

param(
    [string]$Downloads = "$env:USERPROFILE\Downloads",
    [switch]$Apply
)
$ErrorActionPreference = "Stop"

# ---- 파일명 → 프로젝트 내 목적지 -------------------------------------
$Dest = @{
    # 루트
    "bootstrap_yeoyuro_seoul.py"           = "."
    "setup_and_run.ps1"                = "."
    "setup_github.ps1"                 = "."
    "triage_downloads.ps1"             = "."
    "station_routing.py"               = "."
    "README.md"                        = "."
    "LICENSE"                          = "."
    "requirements.txt"                 = "."
    # 파이프라인 스크립트
    "01_build_ridership_mart.py"       = "scripts"
    "03_build_stg_congestion.py"       = "scripts"
    "04_build_congestion_mart.py"      = "scripts"
    "05_build_event_spike_mart.py"     = "scripts"
    "05b_did_event_effect.py"          = "scripts"
    "06_build_route_graph.py"          = "scripts"
    "06b_smoke_test_graph.py"          = "scripts"
    "07_build_training_mart.py"        = "scripts"
    "08_train_congestion_model.py"     = "scripts"
    "08b_evaluate_congestion_model.py" = "scripts"
    "09_route_scoring_prototype.py"    = "scripts"
    "10_evaluate_routes.py"            = "scripts"
    "11_validate_schemas.py"           = "scripts"
    "12_build_display_masters.py"      = "scripts"
    "14_import_map_workbook.py"        = "scripts"
    # 앱
    "yeoyuro_seoul_app.py"                 = "app\streamlit"
    # 테스트
    "test_station_routing.py"          = "tests"
    # 문서
    "PROJECT_PLAN_v2.md"               = "docs"
    "evaluation_strategy.md"           = "docs"
    "model_card_congestion.md"         = "docs"
    "DATA.md"                          = "docs"
    "data_audit_report.md"             = "reports\data_quality"
    "congestion_audit_report.md"       = "reports\data_quality"
    "ridership_quality_report.md"      = "reports\data_quality"
    # 좌표 워크북
    "yeoyuro_seoul_vector_map_coordinate_workbook.xlsx" = "data\master"
}

# ---- 원본 데이터: 파일명 패턴으로 data/raw 하위에 매핑 ----------------
$RawPatterns = @(
    @{ Pattern = "*이용인원*";        Dest = "data\raw\ridership_monthly" },
    @{ Pattern = "*혼잡도정보*";       Dest = "data\raw\congestion_quarterly" },
    @{ Pattern = "*환승역*환승인원*";   Dest = "data\raw\transfer_volume" },
    @{ Pattern = "*도시철도*환승*데이터*"; Dest = "data\raw\transfer_detail" },
    @{ Pattern = "*역간거리*";         Dest = "data\raw\route_distance_time" },
    @{ Pattern = "*열차운행*";         Dest = "data\raw\train_operation" },
    @{ Pattern = "*승객유형별*";        Dest = "data\raw\train_operation" }
)

# ---- 옮기면 안 되는 파일 --------------------------------------------
#  이 둘은 01_build_ridership_mart.py 가 원본 데이터로 생성하는 실데이터다.
#  다운로드본은 개발 중 만든 사본이므로 덮어쓰면 안 된다.
$Protected = @("station_master.csv", "station_alias_master.csv",
               "event_strength_verified.csv")

# ---- 이름 정규화: "07_build (1).py" → "07_build.py" -------------------
function BaseName($n) { return ($n -replace ' \(\d+\)(?=\.[^.]+$)', '') }

Write-Host "`n=== Downloads 정리 ===" -ForegroundColor Cyan
Write-Host "  대상: $Downloads"
Write-Host "  모드: $(if ($Apply) {'실행'} else {'분류만 (dry-run)'})`n"

$files = Get-ChildItem $Downloads -File |
         Where-Object { $_.Extension -in ".py", ".ps1", ".md", ".csv", ".xlsx", ".txt" -or $_.Name -eq "LICENSE" }

$result = @()
foreach ($f in $files) {
    $base = BaseName $f.Name

    if ($Protected -contains $base) {
        $result += [pscustomobject]@{ 분류="보호"; 파일=$f.Name; 목적지="-"; 비고="프로젝트 실데이터. 덮어쓰기 금지" }
        continue
    }
    # 이름 매핑에 없으면 원본 데이터 패턴으로 한 번 더 본다
    $destDir = if ($Dest.ContainsKey($base)) { $Dest[$base] } else {
        ($RawPatterns | Where-Object { $base -like $_.Pattern } | Select-Object -First 1).Dest
    }
    if (-not $destDir) {
        $result += [pscustomobject]@{ 분류="무관"; 파일=$f.Name; 목적지="-"; 비고="여유로 서울 관련 파일 아님" }
        continue
    }

    $target = Join-Path $destDir $base
    if (-not (Test-Path $target)) {
        $result += [pscustomobject]@{ 분류="이동"; 파일=$f.Name; 목적지=$target; 비고="프로젝트에 없음" }
        continue
    }
    # Excel 등이 파일을 열고 있으면 해시를 못 읽는다. 그때는 판정을 보류한다.
    $a = try { (Get-FileHash $f.FullName -ErrorAction Stop).Hash } catch { $null }
    $b = try { (Get-FileHash $target -ErrorAction Stop).Hash } catch { $null }
    if (-not $a) {
        $result += [pscustomobject]@{ 분류="잠김"; 파일=$f.Name; 목적지=$target
                                      비고="다른 프로그램이 사용 중. Excel 등을 닫고 다시 실행" }
        continue
    }
    if ($a -eq $b) {
        $result += [pscustomobject]@{ 분류="중복"; 파일=$f.Name; 목적지=$target; 비고="내용 동일" }
    } else {
        $newer = if ($f.LastWriteTime -gt (Get-Item $target).LastWriteTime) { "다운로드본이 최신" } else { "프로젝트본이 최신" }
        $result += [pscustomobject]@{ 분류="확인"; 파일=$f.Name; 목적지=$target; 비고=$newer }
    }
}

foreach ($g in @("잠김","보호","확인","이동","중복","무관")) {
    $rows = $result | Where-Object { $_.분류 -eq $g }
    if (-not $rows) { continue }
    $color = switch ($g) { "잠김"{"Red"} "보호"{"Magenta"} "확인"{"Yellow"} "이동"{"Cyan"} "중복"{"DarkGray"} default {"DarkGray"} }
    Write-Host "[$g] $($rows.Count)건" -ForegroundColor $color
    $rows | ForEach-Object { Write-Host ("   {0,-46} {1,-28} {2}" -f $_.파일, $_.목적지, $_.비고) }
    Write-Host ""
}

if (-not $Apply) {
    Write-Host "  실제로 정리하려면 -Apply 를 붙여 다시 실행하세요." -ForegroundColor Yellow
    Write-Host "  [확인] 항목은 자동 처리하지 않습니다. 내용을 비교한 뒤 직접 결정하세요.`n" -ForegroundColor Yellow
    exit 0
}

Write-Host "=== 실행 ===" -ForegroundColor Cyan
foreach ($r in $result) {
    $src = Join-Path $Downloads $r.파일
    switch ($r.분류) {
        "중복" {
            Remove-Item $src -Force
            Write-Host "  [삭제] $($r.파일)" -ForegroundColor DarkGray
        }
        "이동" {
            $dir = Split-Path $r.목적지 -Parent
            if ($dir) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
            Move-Item $src $r.목적지 -Force
            Write-Host "  [이동] $($r.파일) -> $($r.목적지)" -ForegroundColor Cyan
        }
        default { }
    }
}
Write-Host "`n  [확인]/[보호]/[잠김]/[무관] 항목은 그대로 두었습니다." -ForegroundColor Yellow
