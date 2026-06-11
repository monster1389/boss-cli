param(
  [string]$RepoUrl = "https://github.com/monster1389/boss-cli",
  [string]$RepoRef = "codex-resume-sync",
  [string]$NodeVersion = "22.21.0",
  [string]$Root = "$HOME\.boss-cli"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Info([string]$Message) {
  Write-Host "[boss-skill-bootstrap] $Message"
}

function Invoke-Checked {
  param([string]$FilePath, [string[]]$ArgumentList, [string]$WorkingDirectory = "")
  $display = "$FilePath $($ArgumentList -join ' ')"
  Write-Info $display
  if ($WorkingDirectory) {
    & $FilePath @ArgumentList | Write-Host
  } else {
    & $FilePath @ArgumentList | Write-Host
  }
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed with exit code ${LASTEXITCODE}: $display"
  }
}

function Get-Arch {
  if ([Environment]::Is64BitOperatingSystem) { return "x64" }
  throw "Only 64-bit Windows is supported by the zero-environment bootstrap."
}

function Install-Node {
  param([string]$RuntimeDir, [string]$Version)
  $arch = Get-Arch
  $installDir = Join-Path $RuntimeDir "node-v$Version-win-$arch"
  $node = Join-Path $installDir "node.exe"
  $npm = Join-Path $installDir "npm.cmd"
  $marker = Join-Path $installDir ".boss-cli-bootstrap-complete"
  if (-not (Test-Path $marker)) {
    $artifact = "node-v$Version-win-$arch.zip"
    $url = "https://nodejs.org/dist/v$Version/$artifact"
    $tmp = Join-Path ([IO.Path]::GetTempPath()) "boss-cli-node-$([guid]::NewGuid())"
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    try {
      $archive = Join-Path $tmp $artifact
      Write-Info "Downloading Node.js $Version"
      Invoke-WebRequest -Uri $url -OutFile $archive
      if (Test-Path $installDir) { Remove-Item -LiteralPath $installDir -Recurse -Force }
      Expand-Archive -LiteralPath $archive -DestinationPath $tmp
      $expanded = Get-ChildItem -LiteralPath $tmp -Directory | Where-Object { $_.Name -like "node-v*" } | Select-Object -First 1
      if (-not $expanded) { throw "Unexpected Node archive layout: $archive" }
      New-Item -ItemType Directory -Force -Path (Split-Path $installDir) | Out-Null
      Move-Item -LiteralPath $expanded.FullName -Destination $installDir
      Set-Content -LiteralPath $marker -Value (Get-Date -Format s) -Encoding UTF8
    } finally {
      Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
  }
  if (-not (Test-Path $node) -or -not (Test-Path $npm)) {
    throw "Managed Node install is incomplete: $installDir"
  }
  return @{ node = $node; npm = $npm; install_dir = $installDir }
}

function Install-MinGit {
  param([string]$RuntimeDir)
  $installRoot = Join-Path $RuntimeDir "mingit"
  $existing = Get-ChildItem -LiteralPath $installRoot -Recurse -Filter git.exe -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -like "*\cmd\git.exe" } |
    Select-Object -First 1
  if ($existing) { return $existing.FullName }

  Write-Info "Downloading MinGit"
  $release = Invoke-RestMethod -Uri "https://api.github.com/repos/git-for-windows/git/releases/latest" -Headers @{ Accept = "application/vnd.github+json" }
  $asset = $release.assets | Where-Object { $_.name -like "MinGit-*-64-bit.zip" } | Select-Object -First 1
  if (-not $asset) { throw "Could not find a MinGit 64-bit zip asset in the latest git-for-windows release." }
  $tmp = Join-Path ([IO.Path]::GetTempPath()) "boss-cli-mingit-$([guid]::NewGuid())"
  New-Item -ItemType Directory -Force -Path $tmp | Out-Null
  try {
    $archive = Join-Path $tmp $asset.name
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $archive
    if (Test-Path $installRoot) { Remove-Item -LiteralPath $installRoot -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $installRoot | Out-Null
    Expand-Archive -LiteralPath $archive -DestinationPath $installRoot
  } finally {
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
  }
  $git = Get-ChildItem -LiteralPath $installRoot -Recurse -Filter git.exe |
    Where-Object { $_.FullName -like "*\cmd\git.exe" } |
    Select-Object -First 1
  if (-not $git) { throw "MinGit install is incomplete: $installRoot" }
  return $git.FullName
}

function Resolve-Git {
  param([string]$RuntimeDir)
  $cmd = Get-Command git -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  return Install-MinGit -RuntimeDir $RuntimeDir
}

function Sync-Repo {
  param([string]$Git, [string]$Url, [string]$Ref, [string]$RepoDir)
  if (-not (Test-Path $RepoDir)) {
    New-Item -ItemType Directory -Force -Path (Split-Path $RepoDir) | Out-Null
    Invoke-Checked $Git @("clone", "--branch", $Ref, "--single-branch", $Url, $RepoDir)
    return
  }
  if (-not (Test-Path (Join-Path $RepoDir ".git"))) {
    throw "Repo directory exists but is not a git repo: $RepoDir"
  }
  Push-Location $RepoDir
  try {
    $status = & $Git status --porcelain
    if ($status) { throw "Repo has local changes; refusing to update managed checkout: $RepoDir" }
    Invoke-Checked $Git @("fetch", "origin", $Ref)
    $branch = & $Git branch --list $Ref
    if ($branch) {
      Invoke-Checked $Git @("checkout", $Ref)
    } else {
      Invoke-Checked $Git @("checkout", "-b", $Ref, "origin/$Ref")
    }
    Invoke-Checked $Git @("pull", "--ff-only", "origin", $Ref)
  } finally {
    Pop-Location
  }
}

$runtimeDir = Join-Path $Root "runtime"
$repoDir = Join-Path $Root "src\boss-cli"
$binDir = Join-Path $Root "bin"
$toolchainDir = Join-Path $Root "toolchain"
$manifestPath = Join-Path $toolchainDir "boss-command.json"

$node = Install-Node -RuntimeDir $runtimeDir -Version $NodeVersion
$git = Resolve-Git -RuntimeDir $runtimeDir
Sync-Repo -Git $git -Url $RepoUrl -Ref $RepoRef -RepoDir $repoDir

Push-Location $repoDir
try {
  Invoke-Checked $node.npm @("ci")
  Invoke-Checked $node.npm @("run", "build")
} finally {
  Pop-Location
}

$cliJs = Join-Path $repoDir "dist\cli\index.js"
if (-not (Test-Path $cliJs)) { throw "boss-cli build output was not found: $cliJs" }
New-Item -ItemType Directory -Force -Path $binDir | Out-Null
$bossCmd = Join-Path $binDir "boss.cmd"
Set-Content -LiteralPath $bossCmd -Value "@echo off`r`n`"$($node.node)`" `"$cliJs`" %*`r`n" -Encoding ASCII

$npmBin = Join-Path $env:APPDATA "npm"
New-Item -ItemType Directory -Force -Path $npmBin | Out-Null
Set-Content -LiteralPath (Join-Path $npmBin "boss.cmd") -Value "@echo off`r`n`"$($node.node)`" `"$cliJs`" %*`r`n" -Encoding ASCII

New-Item -ItemType Directory -Force -Path $toolchainDir | Out-Null
$manifest = [ordered]@{
  ok = $true
  created_at = (Get-Date -Format s)
  repo_url = $RepoUrl
  repo_ref = $RepoRef
  repo_dir = $repoDir
  runtime_dir = $runtimeDir
  node = $node.node
  npm = $node.npm
  git = $git
  boss_bin = $bossCmd
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
$manifest | ConvertTo-Json -Depth 5
