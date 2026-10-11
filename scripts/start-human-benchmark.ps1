param([Parameter(Mandatory=$true)][string]$TrialDirectory)
. (Join-Path $PSScriptRoot 'local-common.ps1')
$lifecycle = Enter-KitcherLifecycle
$previousEnvironment = @{}
$serverStarted = $false
foreach ($key in @('KITCHER_DATA_DIR','KITCHER_DATABASE','KITCHER_DEBUG','KITCHER_HTTPS','KITCHER_ALLOWED_HOSTS','KITCHER_CSRF_ORIGINS','KITCHER_PHONE_URL')) { $previousEnvironment[$key] = [Environment]::GetEnvironmentVariable($key,'Process') }
Push-Location -LiteralPath $kitcherRoot
try {
    $trial = & $kitcherPython -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve(strict=True))' $TrialDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Trial path could not be resolved.' }
    $parent = Split-Path -Parent $trial
    $allowedParents = @((Join-Path $kitcherVar 'benchmarks'),(Join-Path $kitcherVar 'offline-checks'))
    if ($parent -notin $allowedParents -or (Split-Path -Leaf $trial) -notmatch '^20[0-9]{6}T[0-9]{6,13}Z$') { throw 'Select an isolated timestamped benchmark/offline trial directory.' }
    $database = Join-Path $trial 'kitcher.sqlite3'
    if (-not (Test-Path -LiteralPath $database)) { throw 'Benchmark database does not exist.' }
    $resolvedDatabase = & $kitcherPython -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve(strict=True))' $database
    if ($LASTEXITCODE -ne 0 -or $resolvedDatabase -ne $database) { throw 'The benchmark database must remain inside the selected trial.' }
    Assert-KitcherPort 8012 'human-benchmark'
    if (Get-KitcherProcess 'human-benchmark') { throw 'A benchmark browser session is already running. Stop it before changing trials.' }
    $env:KITCHER_DATA_DIR = $trial
    $env:KITCHER_DATABASE = $database
    $env:KITCHER_DEBUG = '1'
    $env:KITCHER_HTTPS = '0'
    $env:KITCHER_ALLOWED_HOSTS = 'localhost,127.0.0.1'
    $env:KITCHER_CSRF_ORIGINS = ''
    $env:KITCHER_PHONE_URL = 'http://127.0.0.1:8012/'
    Invoke-KitcherCommand @('check')
    & $kitcherPython (Join-Path $kitcherRoot 'manage.py') migrate --check
    if ($LASTEXITCODE -ne 0) { throw 'Trial schema needs review; this helper does not migrate or replace its data.' }
    $server = Start-KitcherProcess 'human-benchmark' $kitcherPython 'manage.py runserver 127.0.0.1:8012 --noreload'
    $serverStarted = $true
    $ready = $false
    for ($attempt=0; $attempt -lt 20; $attempt++) {
        Start-Sleep -Milliseconds 250
        $server.Refresh()
        if ($server.HasExited) { throw 'Benchmark server exited; inspect var/human-benchmark.stderr.log.' }
        try {
            $response = Invoke-WebRequest 'http://127.0.0.1:8012/login/' -UseBasicParsing -TimeoutSec 1
            if ($response.StatusCode -eq 200) { $ready=$true; break }
        } catch { }
    }
    if (-not $ready) { throw 'Benchmark login page did not become ready.' }
    @{directory=$trial; url='http://127.0.0.1:8012/'; scope='Isolated desktop-only human benchmark; regular HTTPS application uses its existing database.'} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $kitcherVar 'human-benchmark-config.json') -Encoding UTF8
    Write-Output 'Isolated desktop benchmark: http://127.0.0.1:8012/'
    Write-Output "Use credentials from $trial\initial-account.txt. Follow the prepared worksheet schedule and stopwatch protocol."
}
catch {
    if ($serverStarted) { Stop-KitcherProcess 'human-benchmark' }
    throw
}
finally {
    foreach ($key in $previousEnvironment.Keys) { [Environment]::SetEnvironmentVariable($key,$previousEnvironment[$key],'Process') }
    Pop-Location; $lifecycle.ReleaseMutex(); $lifecycle.Dispose()
}