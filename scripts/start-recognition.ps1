param()
$ErrorActionPreference = 'Stop'
$recognitionRoot = Split-Path -Parent $PSScriptRoot
$recognitionVar = Join-Path $recognitionRoot 'var'
$recognitionExe = Join-Path $recognitionVar 'tools\ollama\ollama.exe'
$recognitionRecord = Join-Path $recognitionVar 'ollama-process.json'
if (-not (Test-Path -LiteralPath $recognitionExe)) { throw 'Run scripts/fetch_ollama.py first.' }
$listener = Get-NetTCPConnection -LocalPort 11435 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    if (-not (Test-Path -LiteralPath $recognitionRecord)) { throw 'Port 11435 is occupied by an unrecorded process.' }
    $record = Get-Content -LiteralPath $recognitionRecord -Raw | ConvertFrom-Json
    $existing = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
    if (-not $existing -or $existing.StartTime.ToUniversalTime().Ticks -ne $record.startedTicks -or $listener.OwningProcess -ne $record.pid) {
        throw 'Port 11435 is occupied by a different process.'
    }
    Write-Output 'The recorded Kitcher recognition service is already running.'
    exit 0
}
$env:OLLAMA_HOST = '127.0.0.1:11435'
$env:OLLAMA_NO_CLOUD = '1'
$env:OLLAMA_MODELS = Join-Path $recognitionVar 'models'
$env:OLLAMA_MAX_LOADED_MODELS = '1'
$env:OLLAMA_NUM_PARALLEL = '1'
$recognitionProcess = Start-Process -FilePath $recognitionExe -ArgumentList 'serve' -WorkingDirectory $recognitionRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $recognitionVar 'ollama.stdout.log') -RedirectStandardError (Join-Path $recognitionVar 'ollama.stderr.log')
@{pid=$recognitionProcess.Id; startedTicks=$recognitionProcess.StartTime.ToUniversalTime().Ticks; executable=$recognitionExe} | ConvertTo-Json | Set-Content -LiteralPath $recognitionRecord -Encoding UTF8
for ($recognitionTry = 0; $recognitionTry -lt 20; $recognitionTry++) {
    Start-Sleep -Milliseconds 500
    $recognitionProcess.Refresh()
    if ($recognitionProcess.HasExited) { throw 'Ollama exited. Inspect var/ollama.stderr.log.' }
    try {
        $version = Invoke-RestMethod 'http://127.0.0.1:11435/api/version' -TimeoutSec 1
        if ($version.version) { Write-Output "Kitcher Ollama $($version.version) is running on 127.0.0.1:11435 with cloud disabled."; exit 0 }
    } catch { }
}
throw 'Ollama did not become ready. Inspect its recorded process and log before retrying.'
