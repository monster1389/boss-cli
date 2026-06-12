param(
  [string]$JdFile = "",
  [string]$JdText = "",
  [string]$JobKeyword = "",
  [string]$BossBin = "",
  [string]$ResumeRoot = "$HOME\.boss-cli\resumes",
  [string]$RunsRoot = "$HOME\.boss-cli\runs",
  [int]$CommandTimeoutSeconds = 900,
  [switch]$IncludeSearch,
  [string]$SearchKeyword = "",
  [string]$SearchJob = "",
  [string]$SearchCity = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$UsableStatuses = @("downloaded", "skipped_existing")
$RequestedSources = @("chat", "recommend")
if ($IncludeSearch) { $RequestedSources = @("chat", "recommend", "search") }

function Get-SafeSegment([string]$Value) {
  $cleaned = $Value -replace '[<>:"/\\|?*\x00-\x1f]', "_" -replace '\s+', "_"
  $cleaned = $cleaned.Trim()
  if (-not $cleaned) { return "jd" }
  if ($cleaned.Length -gt 80) { return $cleaned.Substring(0, 80) }
  return $cleaned
}

function Read-Jd {
  if ($JdFile) {
    return @{ text = Get-Content -Raw -Encoding UTF8 -LiteralPath $JdFile; source = (Resolve-Path -LiteralPath $JdFile).Path }
  }
  if ($JdText) {
    return @{ text = $JdText; source = "inline" }
  }
  if (-not [Console]::IsInputRedirected) {
    throw "Missing JD. Pass -JdFile, -JdText, or pipe JD text through stdin."
  }
  $stdin = [Console]::In.ReadToEnd()
  if (-not $stdin.Trim()) { throw "stdin does not contain JD text." }
  return @{ text = $stdin; source = "stdin" }
}

function Infer-JobKeyword([string]$Text) {
  $lines = $Text -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ }
  $head = ($lines | Select-Object -First 6) -join " "
  $m = [regex]::Match($head, '(Java|Python|Golang|Go|Android|iOS|UI)[^\n,;|]{0,24}', 'IgnoreCase')
  if ($m.Success) {
    $value = (($m.Value -replace '\s+', ' ').Trim(" :-"))
    if ($value.Length -gt 40) { return $value.Substring(0, 40) }
    return $value
  }
  return ""
}

function Resolve-BossBin {
  $attempts = @()
  $candidates = @()
  if ($BossBin) { $candidates += @{ source = "argument"; value = $BossBin } }
  if ($env:BOSS_BIN) { $candidates += @{ source = "environment"; value = $env:BOSS_BIN } }
  $manifestPath = Join-Path $HOME ".boss-cli\toolchain\boss-command.json"
  if (Test-Path $manifestPath) {
    $manifest = Get-Content -Raw -Encoding UTF8 -LiteralPath $manifestPath | ConvertFrom-Json
    if ($manifest.boss_bin) { $candidates += @{ source = "skill_toolchain_manifest"; value = [string]$manifest.boss_bin } }
  }
  $managed = Join-Path $HOME ".boss-cli\bin\boss.cmd"
  if (Test-Path $managed) { $candidates += @{ source = "skill_managed_bin"; value = $managed } }
  $globalBoss = Join-Path $env:APPDATA "npm\boss.cmd"
  if (Test-Path $globalBoss) { $candidates += @{ source = "windows_npm_global"; value = $globalBoss } }
  $pathBossCmd = Get-Command boss.cmd -ErrorAction SilentlyContinue
  if ($pathBossCmd) { $candidates += @{ source = "path_cmd"; value = $pathBossCmd.Source } }
  $pathBoss = Get-Command boss -ErrorAction SilentlyContinue
  if ($pathBoss) { $candidates += @{ source = "path"; value = $pathBoss.Source } }

  $seen = @{}
  foreach ($candidate in $candidates) {
    if ($seen.ContainsKey($candidate.value)) { continue }
    $seen[$candidate.value] = $true
    $exists = Test-Path $candidate.value
    $attempts += @{ source = $candidate.source; candidate = $candidate.value; ok = $exists }
    if ($exists) {
      return @{ ok = $true; selected = $candidate.value; selected_source = $candidate.source; attempts = $attempts }
    }
  }
  return @{
    ok = $false
    selected = $null
    failure_kind = "boss_not_found"
    message = "boss executable was not found. Run scripts\bootstrap_boss_cli.ps1 first, or pass -BossBin."
    attempts = $attempts
  }
}

function Quote-ProcessArgument([string]$Value) {
  if ($Value -notmatch '[\s"]') { return $Value }
  return '"' + ($Value -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"'
}

function Run-CommandJson([string]$FilePath, [string[]]$CommandArgs, [int]$TimeoutSeconds) {
  $process = [System.Diagnostics.Process]::new()
  $commandLine = ((@($FilePath) + $CommandArgs) | ForEach-Object { Quote-ProcessArgument $_ }) -join " "
  if ([IO.Path]::GetExtension($FilePath).ToLowerInvariant() -in @(".cmd", ".bat")) {
    $process.StartInfo.FileName = $env:ComSpec
    $process.StartInfo.Arguments = "/d /s /c " + (Quote-ProcessArgument $commandLine)
  } else {
    $process.StartInfo.FileName = $FilePath
    $process.StartInfo.Arguments = (($CommandArgs | ForEach-Object { Quote-ProcessArgument $_ }) -join " ")
  }
  $process.StartInfo.UseShellExecute = $false
  $process.StartInfo.RedirectStandardOutput = $true
  $process.StartInfo.RedirectStandardError = $true
  $process.StartInfo.CreateNoWindow = $true
  try {
    [void]$process.Start()
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
      $process.Kill()
      $process.WaitForExit()
      return @{
        ok = $false
        command = @($FilePath) + $CommandArgs
        exit_code = $null
        stdout = $stdoutTask.Result
        stderr = $stderrTask.Result
        timed_out = $true
        failure_kind = "command_timeout"
        message = "command timed out after $TimeoutSeconds seconds"
      }
    }
    return @{
      ok = ($process.ExitCode -eq 0)
      command = @($FilePath) + $CommandArgs
      exit_code = $process.ExitCode
      stdout = $stdoutTask.Result
      stderr = $stderrTask.Result
      timed_out = $false
    }
  } finally {
    $process.Dispose()
  }
}

function Classify-Failure([string]$Text, [int]$UsableCount = -1) {
  $lowered = $Text.ToLowerInvariant()
  if ($lowered.Contains("not recognized") -or $lowered.Contains("no such file")) { return "boss_not_found" }
  foreach ($token in @("login", "captcha", "forbidden", "risk", "session")) {
    if ($lowered.Contains($token)) { return "login_or_session" }
  }
  foreach ($token in @("permission", "paid", "deep-search", "page", "unavailable", "not found")) {
    if ($lowered.Contains($token)) { return "permission_or_page_unavailable" }
  }
  if ($UsableCount -ge 0 -and $UsableCount -ne 3) { return "insufficient_data" }
  if ($lowered.Contains("download_failed") -or $lowered.Contains("missing_identifiers")) { return "candidate_download_failed" }
  return "unknown"
}

function Run-Preflight([string]$Boss, [string]$Keyword, [int]$TimeoutSeconds) {
  $checks = @(
    @{ name = "boss_help"; args = @("help") },
    @{ name = "recommend_page"; args = @("recommend", $Keyword) },
    @{ name = "chat_resume_probe"; args = @("resumes", "--from", "chat", "--limit", "1", "--json") }
  )
  $results = @()
  foreach ($check in $checks) {
    $result = Run-CommandJson $Boss $check["args"] $TimeoutSeconds
    $combined = "$($result.stdout)`n$($result.stderr)"
    $result.name = $check["name"]
    if (-not $result.ok) {
      $result.failure_kind = if ($result.failure_kind) { $result.failure_kind } else { Classify-Failure $combined }
      $result.message = if ($result.message) { $result.message } elseif ($result.stderr) { $result.stderr } elseif ($result.stdout) { $result.stdout } else { "$($check["name"]) failed" }
    } else {
      $result.failure_kind = $null
      $result.message = ""
    }
    $results += $result
  }
  $failed = @($results | Where-Object { -not $_.ok })
  return @{
    ok = ($failed.Count -eq 0)
    checks = $results
    failure_kind = if ($failed.Count) { $failed[0].failure_kind } else { $null }
    message = if ($failed.Count) { $failed[0].message } else { "" }
  }
}

function Run-BossResumes([string]$Boss, [string]$Source, [string]$Root, [string]$Keyword, [string]$SearchKw, [string]$SearchJobKeyword, [string]$SearchCityName, [int]$TimeoutSeconds) {
  $resumeArgs = @("resumes", "--from", $Source, "--limit", "3", "--root", $Root, "--json")
  if ($Source -eq "recommend" -and $Keyword) { $resumeArgs += @("--job", $Keyword) }
  if ($Source -eq "search") {
    if (-not $SearchKw) { throw "Search keyword is unclear. Pass -SearchKeyword or -JobKeyword before using -IncludeSearch." }
    $resumeArgs += @("--keyword", $SearchKw)
    if ($SearchJobKeyword) { $resumeArgs += @("--job", $SearchJobKeyword) }
    if ($SearchCityName) { $resumeArgs += @("--city", $SearchCityName) }
  }
  $completed = Run-CommandJson $Boss $resumeArgs $TimeoutSeconds
  if (-not $completed.ok) {
    $combined = "$($completed.stdout)`n$($completed.stderr)"
    return @{
      ok = $false
      source = $Source
      command = $completed.command
      exit_code = $completed.exit_code
      stdout = $completed.stdout
      stderr = $completed.stderr
      results = @()
      counts = @{}
      usable_count = 0
      failure_kind = if ($completed.failure_kind) { $completed.failure_kind } else { Classify-Failure $combined }
      errors = @($(if ($completed.message) { $completed.message } elseif ($completed.stderr.Trim()) { $completed.stderr.Trim() } else { $completed.stdout.Trim() }))
    }
  }
  try {
    $parsed = ConvertFrom-Json -InputObject $completed["stdout"]
  } catch {
    return @{
      ok = $false
      source = $Source
      command = $completed.command
      exit_code = $completed.exit_code
      stdout = $completed.stdout
      stderr = $completed.stderr
      results = @()
      counts = @{}
      usable_count = 0
      failure_kind = "json_parse_failed"
      errors = @("boss resumes did not return valid JSON: $($_.Exception.Message)")
    }
  }
  $hash = @{}
  $parsed.PSObject.Properties | ForEach-Object { $hash[$_.Name] = $_.Value }
  $hash.command = $completed.command
  $hash.exit_code = $completed.exit_code
  $hash.stderr = $completed.stderr
  $hash.failure_kind = $null
  return $hash
}

function Validate-Source([hashtable]$Result) {
  $errors = @()
  $usable = 0
  foreach ($item in @($Result["results"])) {
    $status = [string]$item.status
    $artifacts = $item.artifacts
    $resumeMd = if ($artifacts) { [string]$artifacts.resumeMarkdownPath } else { "" }
    $resumeJson = if ($artifacts) { [string]$artifacts.resumeJsonPath } else { "" }
    if ($UsableStatuses -contains $status -and $resumeMd -and $resumeJson -and (Test-Path $resumeMd) -and (Test-Path $resumeJson)) {
      $usable += 1
    } else {
      $name = if ($item.candidateName) { $item.candidateName } else { "unknown candidate" }
      $message = if ($item.message) { $item.message } else { "missing resume artifact files" }
      $errors += "$name`: $status - $message"
    }
  }
  if ($usable -ne 3) { $errors += "$($Result["source"]): expected 3 usable resumes, got $usable" }
  $failureKind = if ($Result["failure_kind"]) { $Result["failure_kind"] } elseif ($usable -ne 3) { "insufficient_data" } elseif ($errors.Count) { "candidate_download_failed" } else { $null }
  return @{ usable_count = $usable; errors = $errors; failure_kind = $failureKind }
}

$jd = Read-Jd
$keyword = $JobKeyword.Trim()
if (-not $keyword) { $keyword = Infer-JobKeyword $jd.text }
if (-not $keyword) { throw "Job keyword is unclear. Pass -JobKeyword before collecting resumes." }
$effectiveSearchKeyword = $SearchKeyword.Trim()
if (-not $effectiveSearchKeyword) { $effectiveSearchKeyword = $keyword }
$effectiveSearchJob = $SearchJob.Trim()
if (-not $effectiveSearchJob) { $effectiveSearchJob = $keyword }
$effectiveSearchCity = $SearchCity.Trim()

$createdAt = Get-Date -Format "yyyyMMdd_HHmmss"
$runDir = Join-Path $RunsRoot "$createdAt`_$(Get-SafeSegment $keyword)"
New-Item -ItemType Directory -Path $runDir -Force:$false | Out-Null
$jdPath = Join-Path $runDir "jd.md"
Set-Content -LiteralPath $jdPath -Value ($jd.text.Trim() + "`n") -Encoding UTF8

$bossInfo = Resolve-BossBin
$sources = @{}
if (-not $bossInfo.ok) {
  $preflight = @{ ok = $false; checks = @(); failure_kind = "boss_not_found"; message = $bossInfo.message }
  foreach ($source in $RequestedSources) {
    $sources[$source] = @{ ok = $false; source = $source; command = @(); exit_code = $null; stdout = ""; stderr = ""; results = @(); counts = @{}; usable_count = 0; failure_kind = "boss_not_found"; errors = @($bossInfo.message) }
  }
} else {
  $preflight = Run-Preflight $bossInfo.selected $keyword $CommandTimeoutSeconds
  if ($preflight.ok) {
    foreach ($source in $RequestedSources) {
      $result = Run-BossResumes $bossInfo.selected $source $ResumeRoot $keyword $effectiveSearchKeyword $effectiveSearchJob $effectiveSearchCity $CommandTimeoutSeconds
      $validated = Validate-Source $result
      $result["usable_count"] = $validated["usable_count"]
      $result["errors"] = $validated["errors"]
      $result["failure_kind"] = $validated["failure_kind"]
      $sources[$source] = $result
    }
  } else {
    foreach ($source in $RequestedSources) {
      $sources[$source] = @{ ok = $false; source = $source; command = @(); exit_code = $null; stdout = ""; stderr = ""; results = @(); counts = @{}; usable_count = 0; failure_kind = $preflight.failure_kind; errors = @($preflight.message) }
    }
  }
}

$items = @()
$sourceCounts = @{}
$failedSources = @()
foreach ($source in $RequestedSources) {
  $sourceCounts[$source] = $sources[$source]["usable_count"]
  if ($sources[$source]["usable_count"] -ne 3 -or @($sources[$source]["errors"]).Count) { $failedSources += $source }
  foreach ($item in @($sources[$source]["results"])) {
    $artifacts = $item.artifacts
    $items += [ordered]@{
      source = $source
      candidateName = $item.candidateName
      candidateId = $item.candidateId
      jobName = $item.jobName
      jobId = $item.jobId
      status = $item.status
      message = $item.message
      resumeMarkdownPath = if ($artifacts) { $artifacts.resumeMarkdownPath } else { $null }
      resumeJsonPath = if ($artifacts) { $artifacts.resumeJsonPath } else { $null }
      rawResponsePath = if ($artifacts) { $artifacts.rawResponsePath } else { $null }
    }
  }
}

$manifest = [ordered]@{
  ok = ($preflight.ok -and $failedSources.Count -eq 0)
  created_at = (Get-Date -Format s)
  requested_sources = $RequestedSources
  run_dir = $runDir
  resume_root = $ResumeRoot
  boss = $bossInfo
  preflight = $preflight
  jd = @{ source = $jd.source; path = $jdPath; job_keyword = $keyword }
  command_timeout_seconds = $CommandTimeoutSeconds
  sources = $sources
  items = $items
  unique_items = $items
  usable_total = (($sourceCounts.Values | Measure-Object -Sum).Sum)
  source_usable_counts = $sourceCounts
  failed_sources = $failedSources
}

$manifestPath = Join-Path $runDir "collection_manifest.json"
$summaryPath = Join-Path $runDir "collection_summary.md"
$manifest | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

$summary = @(
  "# BOSS JD Resume Collection",
  "",
  "- ok: $($manifest.ok)",
  "- requested_sources: $($RequestedSources -join ', ')",
  "- usable_total: $($manifest.usable_total)",
  "- failed_sources: $(if ($failedSources.Count) { $failedSources -join ', ' } else { 'none' })",
  "- job_keyword: $keyword",
  "- boss_bin: $($bossInfo.selected)",
  "",
  "## Preflight",
  "",
  "- ok: $($preflight.ok)",
  "- failure_kind: $($preflight.failure_kind)"
)
foreach ($source in $RequestedSources) {
  $summary += "- $source`: usable=$($sources[$source]["usable_count"])/3, failure_kind=$($sources[$source]["failure_kind"])"
}
Set-Content -LiteralPath $summaryPath -Value ($summary -join "`n") -Encoding UTF8

[ordered]@{
  ok = $manifest.ok
  run_dir = $runDir
  manifest = $manifestPath
  summary = $summaryPath
  job_keyword = $keyword
  usable_total = $manifest.usable_total
  source_usable_counts = $sourceCounts
  failed_sources = $failedSources
  preflight_ok = $preflight.ok
  preflight_failure_kind = $preflight.failure_kind
} | ConvertTo-Json -Depth 20

if (-not $manifest.ok) { exit 1 }
