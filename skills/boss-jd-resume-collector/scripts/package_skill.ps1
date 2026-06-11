param(
  [string]$Output = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$skillDir = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
if (-not $Output) {
  $distDir = Join-Path (Split-Path -Parent (Split-Path -Parent $skillDir)) "dist\skills"
  New-Item -ItemType Directory -Force -Path $distDir | Out-Null
  $Output = Join-Path $distDir "boss-jd-resume-collector.zip"
}

$outputPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Output)
$tempRoot = Join-Path ([IO.Path]::GetTempPath()) "boss-skill-package-$([guid]::NewGuid())"
$staging = Join-Path $tempRoot "boss-jd-resume-collector"

try {
  New-Item -ItemType Directory -Force -Path $staging | Out-Null
  Get-ChildItem -LiteralPath $skillDir -Recurse -File |
    Where-Object {
      $_.FullName -notmatch '\\__pycache__\\' -and
      $_.Name -notlike '*.pyc' -and
      $_.Name -ne '.DS_Store'
    } |
    ForEach-Object {
      $relative = $_.FullName.Substring($skillDir.Length).TrimStart('\', '/')
      $target = Join-Path $staging $relative
      New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
      Copy-Item -LiteralPath $_.FullName -Destination $target
    }
  New-Item -ItemType Directory -Force -Path (Split-Path $outputPath) | Out-Null
  if (Test-Path $outputPath) { Remove-Item -LiteralPath $outputPath -Force }
  Compress-Archive -LiteralPath $staging -DestinationPath $outputPath
  Write-Output $outputPath
} finally {
  Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
