param(
  [string]$JdFile = "",
  [string]$JdText = "",
  [string]$JobKeyword = "",
  [string]$BossBin = "",
  [string]$ResumeRoot = "$HOME\.boss-cli\resumes",
  [string]$RunsRoot = "$HOME\.boss-cli\runs",
  [int]$CommandTimeoutSeconds = 900,
  [string]$SearchKeyword = "",
  [string]$SearchJob = "",
  [string]$SearchCity = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Resolve-PythonCommand {
  $candidates = @()
  if ($env:PYTHON) {
    $candidates += @{ exe = $env:PYTHON; prefix = @(); source = "PYTHON" }
  }
  $python = Get-Command python -ErrorAction SilentlyContinue
  if ($python) {
    $candidates += @{ exe = $python.Source; prefix = @(); source = "PATH python" }
  }
  $py = Get-Command py -ErrorAction SilentlyContinue
  if ($py) {
    $candidates += @{ exe = $py.Source; prefix = @("-3"); source = "PATH py launcher" }
  }

  foreach ($candidate in $candidates) {
    $versionArgs = @($candidate.prefix) + @("--version")
    $output = & $candidate.exe @versionArgs 2>&1
    if ($LASTEXITCODE -eq 0) {
      return $candidate
    }
  }

  throw "Python executable was not found. Install Python 3, set PYTHON, or add python.exe to PATH."
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonScript = Join-Path $scriptDir "collect_boss_resumes.py"
if (-not (Test-Path -LiteralPath $pythonScript)) {
  throw "Missing Python collector script: $pythonScript"
}

$python = Resolve-PythonCommand
$argsList = @($python.prefix) + @($pythonScript)

if ($JdFile) {
  $argsList += @("--jd-file", $JdFile)
} elseif ($JdText) {
  $argsList += @("--jd-text", $JdText)
} elseif ([Console]::IsInputRedirected) {
  $stdin = [Console]::In.ReadToEnd()
  if ($stdin.Trim()) {
    $argsList += @("--jd-text", $stdin)
  }
}

if ($JobKeyword) { $argsList += @("--job-keyword", $JobKeyword) }
if ($BossBin) { $argsList += @("--boss-bin", $BossBin) }
if ($ResumeRoot) { $argsList += @("--resume-root", $ResumeRoot) }
if ($RunsRoot) { $argsList += @("--runs-root", $RunsRoot) }
if ($SearchKeyword) { $argsList += @("--search-keyword", $SearchKeyword) }
if ($SearchJob) { $argsList += @("--search-job", $SearchJob) }
if ($SearchCity) { $argsList += @("--search-city", $SearchCity) }
$argsList += @("--command-timeout-seconds", [string]$CommandTimeoutSeconds)

& $python.exe @argsList
exit $LASTEXITCODE
