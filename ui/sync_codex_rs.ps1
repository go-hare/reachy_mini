param(
    [string]$SourcePath = "C:\Users\Administrator\Downloads\codex-main (1)\codex-main\codex-rs",
    [switch]$Force
)

$destination = Join-Path $PSScriptRoot "codex-rs"

if (-not (Test-Path $SourcePath)) {
    throw "找不到源目录: $SourcePath"
}

if ((Test-Path $destination) -and -not $Force) {
    throw "目标目录已存在: $destination。确认覆盖后再加 -Force。"
}

if ((Test-Path $destination) -and $Force) {
    Remove-Item -Recurse -Force $destination
}

$null = New-Item -ItemType Directory -Force $destination

robocopy $SourcePath $destination /E /XD target .git

if ($LASTEXITCODE -gt 7) {
    throw "robocopy 失败，退出码: $LASTEXITCODE"
}

Write-Host "已同步 codex-rs 到 $destination"
