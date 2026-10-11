param([string]$Manifest='var\recognition-corpus-corrected-20261009.json')
. (Join-Path $PSScriptRoot 'local-common.ps1')
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run this orchestrator as your normal user. Only its firewall helper will request elevation.' }
$lifecycle = Enter-KitcherLifecycle
$previousEnvironment = @{}
foreach ($key in @('KITCHER_DATA_DIR','KITCHER_DATABASE','PYTHONHOME','PYTHONPATH','KITCHER_RECOGNITION_PROFILE')) { $previousEnvironment[$key] = [Environment]::GetEnvironmentVariable($key,'Process') }
$workerWasRunning = $false
$auditPath = $null
$trialSucceeded = $false
$firewallHelper = $null
$stopMarker = Join-Path $kitcherVar 'import-worker.stop'
Push-Location -LiteralPath $kitcherRoot
function Invoke-OfflinePython([string[]]$Arguments) {
    & $offlinePython @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Offline check failed: $($Arguments[0])." }
}
function Read-FirewallState {
    if (-not (Test-Path -LiteralPath $statePath)) { return $null }
    try { return (Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json) } catch { return $null }
}
function Save-OfflineAudit { [IO.File]::WriteAllText($auditPath,($audit | ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false)) }
try {
    Set-KitcherEnvironment
    $manifestPath = (Resolve-Path -LiteralPath $Manifest).Path
    if (-not (Get-KitcherProcess 'ollama')) { throw 'Start the recorded local recognition service first.' }
    $runtimeRoot = Join-Path $kitcherVar 'offline-runtime'
    $runtimeManifest = Get-Content -LiteralPath (Join-Path $runtimeRoot 'runtime-manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($file in $runtimeManifest.files) {
        if ($file.name -notmatch '^[a-z0-9_]+\.(exe|dll)$') { throw 'Invalid dedicated runtime file name.' }
        if ((Get-FileHash -LiteralPath (Join-Path $runtimeRoot $file.name) -Algorithm SHA256).Hash -ne $file.sha256) { throw 'Dedicated runtime checksum mismatch.' }
    }
    $offlinePython = Join-Path $runtimeRoot 'python.exe'
    $env:PYTHONHOME = $runtimeManifest.base
    $env:PYTHONPATH = $kitcherRoot + ';' + (Join-Path $kitcherRoot '.venv\Lib\site-packages')
    $env:KITCHER_RECOGNITION_PROFILE = 'baseline'
    $profiles = @(Get-NetFirewallProfile | Select-Object Name,Enabled)
    if (@($profiles | Where-Object { -not $_.Enabled }).Count) { throw 'All firewall profiles must already be enabled; this check does not enable them.' }
    $service = Get-Service MpsSvc
    if ($service.Status -ne 'Running') { throw 'Windows firewall service must already be running.' }
    $worker = Get-KitcherProcess 'worker'
    $workerWasRunning = $null -ne $worker
    if ($worker) {
        [IO.File]::WriteAllText($stopMarker,'Pause for isolated offline recognition check.',[Text.UTF8Encoding]::new($false))
        if (-not $worker.WaitForExit(125000)) { throw 'Worker did not finish its bounded job; check cancelled without terminating it.' }
    }
    $trialRoot = Join-Path $kitcherVar ('offline-checks\' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ'))
    New-Item -ItemType Directory -Path $trialRoot -ErrorAction Stop | Out-Null
    $env:KITCHER_DATA_DIR = $trialRoot
    $env:KITCHER_DATABASE = Join-Path $trialRoot 'kitcher.sqlite3'
    Invoke-OfflinePython @('manage.py','migrate','--noinput')
    Invoke-OfflinePython @('manage.py','setup_account')
    Invoke-OfflinePython @('scripts\offline-probe.py','--expect','allowed','--report',(Join-Path $trialRoot 'before.json'))
    # Block all non-loopback IPv4/IPv6 destinations for these owned programs only.
    $programs = @($offlinePython,(Join-Path $kitcherVar 'tools\ollama\ollama.exe'),(Join-Path $kitcherVar 'tools\ollama\lib\ollama\llama-server.exe'),(Join-Path $kitcherVar 'tools\tesseract\tesseract.exe'))
    $addresses = @('0.0.0.0-126.255.255.255','128.0.0.0-255.255.255.255','::','::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff')
    $group = 'KitcherOffline-' + [Guid]::NewGuid().ToString('N')
    $auditPath = Join-Path $trialRoot 'firewall-audit.json'
    $audit = [ordered]@{schema=1; started=[DateTime]::UtcNow.ToString('o'); group=$group; scope='Dedicated test Python and Kitcher Ollama/llama-server/Tesseract executables; other apps/network remain available'; remote_addresses=$addresses; profiles=$profiles; rules=@(); state='preparing'}
    Save-OfflineAudit
    for ($index=0; $index -lt $programs.Count; $index++) {
        $program = [IO.Path]::GetFullPath($programs[$index])
        if (-not $program.StartsWith($kitcherVar + '\',[StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path -LiteralPath $program)) { throw 'Unexpected or missing recognition executable.' }
        $name = "$group-$index"
        $audit.rules += @{name=$name; program=$program; sha256=(Get-FileHash -LiteralPath $program -Algorithm SHA256).Hash}
        Save-OfflineAudit
    }
    $statePath = Join-Path $trialRoot 'firewall-state.json'
    $helperScript = Join-Path $PSScriptRoot 'offline-firewall-helper.ps1'
    $helperArguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $helperScript + '" -AuditFile "' + $auditPath + '"'
    $firewallHelper = Start-Process -FilePath 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -ArgumentList $helperArguments -WorkingDirectory $kitcherRoot -Verb RunAs -WindowStyle Hidden -PassThru
    $ready = [Diagnostics.Stopwatch]::StartNew()
    do {
        $firewallState = Read-FirewallState
        if ($firewallState -and $firewallState.state -eq 'enforced') { break }
        if ($firewallHelper.HasExited -or $ready.Elapsed.TotalSeconds -gt 60) { throw 'Offline firewall helper did not establish the verified rules.' }
        Start-Sleep -Milliseconds 250
    } while ($true)
    $audit.effective = $firewallState.effective
    $audit.state = 'enforced'
    Save-OfflineAudit
    Invoke-OfflinePython @('scripts\offline-probe.py','--expect','blocked','--report',(Join-Path $trialRoot 'blocked-before.json'))
    Invoke-OfflinePython @('manage.py','benchmark_recognition','--manifest',$manifestPath,'--output',(Join-Path $trialRoot 'results.json'),'--measure-memory','--ollama-record',(Join-Path $kitcherVar 'ollama-process.json'))
    Invoke-OfflinePython @('scripts\offline-probe.py','--expect','blocked','--report',(Join-Path $trialRoot 'blocked-after.json'))
    $finalFirewallState = Read-FirewallState
    if (-not $finalFirewallState -or $finalFirewallState.state -ne 'enforced') { throw 'Offline enforcement did not remain active through the trial.' }
    $audit.state = 'trial-complete'
    $trialSucceeded = $true
    Save-OfflineAudit
}
catch {
    if ($auditPath) { $audit.error = $_.Exception.Message; Save-OfflineAudit }
    throw
}
finally {
    try {
        if ($auditPath -and $firewallHelper) {
            [IO.File]::WriteAllText((Join-Path $trialRoot 'release-rules'),'Trial ended; remove only recorded rules.',[Text.UTF8Encoding]::new($false))
            if (-not $firewallHelper.WaitForExit(30000)) { throw 'Firewall cleanup is still running; inspect the helper before claiming restoration.' }
            $removed = Read-FirewallState
            if (-not $removed -or $removed.state -ne 'rules-removed' -or -not $removed.cleanup_verified) { throw 'Temporary firewall cleanup was not verified. Use clear-offline-rules.ps1 with this audit file.' }
            $audit.state = 'rules-removed'
            $audit.rules_removed_at = $removed.removed_at
            Save-OfflineAudit
            Invoke-OfflinePython @('scripts\offline-probe.py','--expect','allowed','--report',(Join-Path $trialRoot 'restored.json'))
            $audit.restored_probe_passed = $true
            $audit.trial_passed = $trialSucceeded
            Save-OfflineAudit
            Write-Output "Offline check evidence: $trialRoot"
        }
    }
    finally {
        foreach ($key in $previousEnvironment.Keys) { [Environment]::SetEnvironmentVariable($key,$previousEnvironment[$key],'Process') }
        try {
            if ($workerWasRunning) {
                [IO.File]::Delete($stopMarker)
                Set-KitcherEnvironment
                $resumed = Start-KitcherProcess 'worker' $kitcherPython 'manage.py process_imports'
                Start-Sleep -Milliseconds 500
                $resumed.Refresh()
                if ($resumed.HasExited -or -not (Get-KitcherProcess 'worker')) { throw 'Primary worker resume/normal-user identity verification failed; inspect var/worker.stderr.log.' }
                Write-Output 'Primary worker resumed; manual HTTPS access stayed available.'
            }
        }
        finally { Pop-Location; $lifecycle.ReleaseMutex(); $lifecycle.Dispose() }
    }
}