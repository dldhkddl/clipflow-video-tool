$ErrorActionPreference = "Stop"
$taskName = "ClipFlow Daily Shorts"
$runner = Join-Path $PSScriptRoot "run_daily.bat"

if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) {
    throw "run_daily.bat 파일을 찾을 수 없습니다: $runner"
}

& schtasks.exe /Create /TN $taskName /SC DAILY /ST "08:00" /TR $runner /F
if ($LASTEXITCODE -ne 0) {
    throw "Windows 예약 작업을 등록하지 못했습니다."
}

& schtasks.exe /Query /TN $taskName /V /FO LIST
