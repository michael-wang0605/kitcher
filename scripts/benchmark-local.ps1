param([Parameter(Mandatory=$true)][string]$Manifest, [switch]$DetailedFridge)
# Measured trial in a fresh database. Only the primary import worker is paused.
. (Join-Path $PSScriptRoot 'local-common.ps1')
$lifecycle = Enter-KitcherLifecycle
$previousData = $env:KITCHER_DATA_DIR
$previousDatabase = $env:KITCHER_DATABASE
$previousProfile = $env:KITCHER_RECOGNITION_PROFILE
$workerWasRunning = $false
$stopMarker = Join-Path $kitcherVar 'import-worker.stop'
Push-Location -LiteralPath $kitcherRoot
try {
    Set-KitcherEnvironment
    $manifestPath = (Resolve-Path -LiteralPath $Manifest).Path
    if (-not (Get-KitcherProcess 'ollama')) { throw 'Start the recorded local recognition service first.' }
    $worker = Get-KitcherProcess 'worker'
    $workerWasRunning = $null -ne $worker
    if ($worker) {
        [IO.File]::WriteAllText($stopMarker, 'Pause for isolated recognition benchmark.', [Text.UTF8Encoding]::new($false))
        if (-not $worker.WaitForExit(125000)) { throw 'Worker did not finish its bounded job; benchmark cancelled without terminating it.' }
    }
    $trialRoot = Join-Path $kitcherVar ('benchmarks\' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ'))
    New-Item -ItemType Directory -Path $trialRoot -ErrorAction Stop | Out-Null
    $env:KITCHER_DATA_DIR = $trialRoot
    $env:KITCHER_DATABASE = Join-Path $trialRoot 'kitcher.sqlite3'
    $env:KITCHER_RECOGNITION_PROFILE = if ($DetailedFridge) { 'fridge-detail-trial' } else { 'baseline' }
    Invoke-KitcherCommand @('migrate','--noinput')
    Invoke-KitcherCommand @('setup_account')
    Invoke-KitcherCommand @('benchmark_recognition','--manifest',$manifestPath,'--output',(Join-Path $trialRoot 'results.json'),'--measure-memory','--ollama-record',(Join-Path $kitcherVar 'ollama-process.json'))
    Write-Output "Isolated benchmark evidence: $trialRoot"
}
finally {
    [Environment]::SetEnvironmentVariable('KITCHER_DATA_DIR',$previousData,'Process')
    [Environment]::SetEnvironmentVariable('KITCHER_DATABASE',$previousDatabase,'Process')
    [Environment]::SetEnvironmentVariable('KITCHER_RECOGNITION_PROFILE',$previousProfile,'Process')
    try {
        if ($workerWasRunning) {
            [IO.File]::Delete($stopMarker)
            Set-KitcherEnvironment
            $resumedWorker = Start-KitcherProcess 'worker' $kitcherPython 'manage.py process_imports'
            Start-Sleep -Milliseconds 500
            $resumedWorker.Refresh()
            if ($resumedWorker.HasExited) { throw 'Import worker restart failed; inspect var/worker.stderr.log.' }
            Write-Output 'Primary import worker resumed; web access stayed available.'
        }
    }
    finally { Pop-Location; $lifecycle.ReleaseMutex(); $lifecycle.Dispose() }
}