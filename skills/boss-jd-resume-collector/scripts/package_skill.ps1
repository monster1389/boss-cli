param(
  [string]$Output = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$skillDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath(
  (Split-Path -Parent (Split-Path -Parent $PSCommandPath))
)
if (-not $Output) {
  $distDir = Join-Path (Split-Path -Parent (Split-Path -Parent $skillDir)) "dist\skills"
  New-Item -ItemType Directory -Force -Path $distDir | Out-Null
  $Output = Join-Path $distDir "boss-jd-resume-collector.zip"
}

$outputPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Output)
$tempRoot = Join-Path ([IO.Path]::GetTempPath()) "boss-skill-package-$([guid]::NewGuid())"
$staging = Join-Path $tempRoot "boss-jd-resume-collector"

function Get-IncludedSkillFiles {
  param([string]$Root)
  Get-ChildItem -LiteralPath $Root -Recurse -File |
    Where-Object {
      $_.FullName -notmatch '\\__pycache__\\' -and
      $_.Name -notlike '*.pyc' -and
      $_.Name -ne '.DS_Store'
    }
}

function Get-RelativeZipPath {
  param([string]$Root, [string]$Path)
  $relative = $Path.Substring($Root.Length).TrimStart('\', '/')
  return ("boss-jd-resume-collector/" + ($relative -replace '\\', '/'))
}

function Test-ByteArrayEqual {
  param([byte[]]$Left, [byte[]]$Right)
  if ($Left.Length -ne $Right.Length) { return $false }
  for ($i = 0; $i -lt $Left.Length; $i++) {
    if ($Left[$i] -ne $Right[$i]) { return $false }
  }
  return $true
}

function Assert-ZipMatchesSource {
  param([string]$ZipPath, [string]$SourceRoot)
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $zip = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
  try {
    $entries = @{}
    $allEntries = @{}
    foreach ($entry in $zip.Entries) {
      if ($entry.FullName.EndsWith('/')) { continue }
      $normalizedEntryName = $entry.FullName -replace '\\', '/'
      if ($normalizedEntryName -match '(^|/)__pycache__/' -or $normalizedEntryName -like '*.pyc' -or $entry.Name -eq '.DS_Store') {
        throw "Unexpected excluded file in skill zip: $($entry.FullName)"
      }
      $entries[$normalizedEntryName] = $entry
      $allEntries[$normalizedEntryName] = $entry
    }

    $sourceFiles = @(Get-IncludedSkillFiles -Root $SourceRoot)
    foreach ($file in $sourceFiles) {
      $zipName = Get-RelativeZipPath -Root $SourceRoot -Path $file.FullName
      if (-not $entries.ContainsKey($zipName)) {
        throw "Skill zip is missing source file: $zipName"
      }
      $stream = $entries[$zipName].Open()
      $memory = $null
      try {
        $memory = New-Object System.IO.MemoryStream
        $stream.CopyTo($memory)
        $zipBytes = $memory.ToArray()
      } finally {
        $stream.Dispose()
        if ($memory) { $memory.Dispose() }
      }
      $sourceBytes = [System.IO.File]::ReadAllBytes($file.FullName)
      if (-not (Test-ByteArrayEqual -Left $zipBytes -Right $sourceBytes)) {
        throw "Skill zip content differs from source file: $zipName"
      }
      $entries.Remove($zipName) | Out-Null
    }

    if ($entries.Count -gt 0) {
      throw "Skill zip contains files not present in source: $($entries.Keys -join ', ')"
    }

    $requiredText = @{
      "boss-jd-resume-collector/scripts/collect_boss_resumes.py" = @(
        'encoding="utf-8-sig"',
        'boss resumes'
      )
      "boss-jd-resume-collector/scripts/bootstrap_boss_cli.ps1" = @(
        'System.Text.UTF8Encoding $false'
      )
      "boss-jd-resume-collector/SKILL.md" = @(
        'boss resumes --from search --keyword <keyword> --job <keyword> --limit 3 --json',
        'boss resumes --from recommend --job <keyword> --limit 1 --json'
      )
    }
    foreach ($name in $requiredText.Keys) {
      $entry = $allEntries[$name]
      if (-not $entry) { throw "Skill zip is missing required check file: $name" }
      $reader = New-Object System.IO.StreamReader($entry.Open(), [System.Text.Encoding]::UTF8)
      try {
        $content = $reader.ReadToEnd()
      } finally {
        $reader.Dispose()
      }
      foreach ($needle in $requiredText[$name]) {
        if (-not $content.Contains($needle)) {
          throw "Skill zip missing required marker in ${name}: $needle"
        }
      }
    }
  } finally {
    $zip.Dispose()
  }
}

try {
  New-Item -ItemType Directory -Force -Path $staging | Out-Null
  Get-IncludedSkillFiles -Root $skillDir |
    ForEach-Object {
      $relative = $_.FullName.Substring($skillDir.Length).TrimStart('\', '/')
      $target = Join-Path $staging $relative
      New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
      Copy-Item -LiteralPath $_.FullName -Destination $target
    }
  New-Item -ItemType Directory -Force -Path (Split-Path $outputPath) | Out-Null
  if (Test-Path $outputPath) { Remove-Item -LiteralPath $outputPath -Force }
  Compress-Archive -LiteralPath $staging -DestinationPath $outputPath
  Assert-ZipMatchesSource -ZipPath $outputPath -SourceRoot $skillDir
  Write-Output $outputPath
} finally {
  Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
