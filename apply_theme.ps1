# =====================================================================
#  apply_theme.ps1 — Streamlit 테마 설정 (BOM 없이 저장)
#
#  PowerShell 5.1 의 Set-Content -Encoding UTF8 은 BOM 을 붙인다.
#  TOML 파서가 BOM 을 만나면 파일 전체를 무시해 테마가 적용되지 않는다.
#  .NET 의 WriteAllText 는 BOM 없는 UTF-8 로 쓴다.
# =====================================================================
$dir = Join-Path (Get-Location) ".streamlit"
New-Item -ItemType Directory -Path $dir -Force | Out-Null
$path = Join-Path $dir "config.toml"

$toml = @"
# MetroCalm Streamlit 테마
# 탭 강조선, 라디오, 포커스 링은 CSS 가 아니라 primaryColor 가 그린다.
# 기본값 #FF4B4B(빨강)를 브랜드 골드로 바꾼다.
[theme]
primaryColor = "#8D7150"
backgroundColor = "#FFFFFF"
secondaryBackgroundColor = "#F3F4F6"
textColor = "#111827"
font = "sans serif"

[browser]
gatherUsageStats = false
"@

[System.IO.File]::WriteAllText($path, $toml, (New-Object System.Text.UTF8Encoding($false)))

# 검증: 첫 3바이트가 EF BB BF 면 BOM 이 붙은 것
$bytes = [System.IO.File]::ReadAllBytes($path)[0..2]
$hasBom = ($bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
if ($hasBom) { Write-Host "  [!] BOM 이 붙었습니다. 테마가 적용되지 않습니다." -ForegroundColor Red }
else { Write-Host "  [OK] BOM 없이 저장: $path" -ForegroundColor Green }
Get-Content $path | Select-Object -First 6
