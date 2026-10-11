param([switch]$WithoutRecognition)
. (Join-Path $PSScriptRoot 'local-common.ps1')
$lifecycle = Enter-KitcherLifecycle
Push-Location -LiteralPath $kitcherRoot
try {
    Set-KitcherEnvironment
    if ($env:KITCHER_DEBUG -ne '0' -or $env:KITCHER_HTTPS -ne '1') { throw 'Regular startup requires DEBUG=0 and HTTPS=1 in var/local.env.' }
    if ($env:KITCHER_LAN_IP -notmatch '^\d{1,3}(\.\d{1,3}){3}$') { throw 'Set KITCHER_LAN_IP to the router-reserved private IPv4 address.' }
    if ($env:KITCHER_LAN_IP -notmatch '^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)') { throw 'Use a private LAN address.' }
    if (($env:KITCHER_ALLOWED_HOSTS -split ',') -notcontains $env:KITCHER_LAN_IP -or ($env:KITCHER_CSRF_ORIGINS -split ',') -notcontains "https://$($env:KITCHER_LAN_IP)") { throw 'Allowed hosts and CSRF origins must include the configured LAN address.' }
    $lanAddress = Get-NetIPAddress -AddressFamily IPv4 -IPAddress $env:KITCHER_LAN_IP -ErrorAction SilentlyContinue
    if (-not $lanAddress) { throw 'Configured LAN address is not assigned to this computer. Recheck the router reservation.' }
    if (-not (Test-Path -LiteralPath $kitcherCaddy)) { throw 'Run scripts/fetch_caddy.py first.' }
    $caddyConfig = Join-Path $kitcherRoot 'config\Caddyfile'
    if (-not (Test-Path -LiteralPath $caddyConfig)) { throw 'Copy config/Caddyfile.example to ignored config/Caddyfile.' }
    & $kitcherCaddy validate --config $caddyConfig --adapter caddyfile
    if ($LASTEXITCODE -ne 0) { throw 'Caddy configuration is invalid.' }
    if (Get-KitcherProcess 'development') {
        Assert-KitcherPort 8001 'development'
        Stop-KitcherProcess 'development'
    }
    Assert-KitcherPort 8001 'web' 
    Assert-KitcherPort 443 'caddy'
    Assert-KitcherPort 20191 'caddy'
    Invoke-KitcherCommand @('check')
    & $kitcherPython (Join-Path $kitcherRoot 'manage.py') migrate --check
    if ($LASTEXITCODE -ne 0) {
        if ((Get-KitcherProcess 'web') -or (Get-KitcherProcess 'worker')) { throw 'Stop Kitcher before applying pending migrations.' }
        $database = if ($env:KITCHER_DATABASE) { $env:KITCHER_DATABASE } else { Join-Path $kitcherVar 'kitcher.sqlite3' }
        if ((Test-Path -LiteralPath $database) -and (Get-Item -LiteralPath $database).Length -gt 0) { Invoke-KitcherCommand @('backup_pantry','--pre-migration') }
        Invoke-KitcherCommand @('migrate','--noinput')
    }
    Invoke-KitcherCommand @('cleanup_imports')
    & $kitcherPython (Join-Path $PSScriptRoot 'cleanup-trials.py')
    if ($LASTEXITCODE -ne 0) { Write-Warning 'Isolated trial-source cleanup needs attention; inspect var/trial-cleanup-status.json.' }
    Invoke-KitcherCommand @('collectstatic','--noinput')
    try { Invoke-KitcherCommand @('backup_pantry') } catch { Write-Warning $_.Exception.Message }
    $web = Start-KitcherProcess 'web' $kitcherPython '-m kitcher.server'
    $proxy = Start-KitcherProcess 'caddy' $kitcherCaddy "run --config `"$caddyConfig`" --adapter caddyfile"
    $webReady = $false
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 500
        $web.Refresh(); $proxy.Refresh()
        if ($web.HasExited -or $proxy.HasExited) { throw 'Web startup failed. Inspect var/web.stderr.log and var/caddy.stderr.log.' }
        try {
            $request = [Net.HttpWebRequest]::Create('http://127.0.0.1:8001/login/')
            $request.AllowAutoRedirect=$false
            $request.Timeout=1000
            $response=$request.GetResponse()
            try { if ([int]$response.StatusCode -in @(301,302)) { $webReady=$true; break } } finally { $response.Dispose() }
        } catch {
            if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -in @(301,302)) { $webReady = $true; break }
        }
    }
    if (-not $webReady) { throw 'Waitress did not respond with its HTTPS redirect.' }
    if (-not $WithoutRecognition) {
        try {
            & (Join-Path $PSScriptRoot 'start-recognition.ps1')
            if (-not (Test-Path -LiteralPath (Join-Path $kitcherVar 'tools\tesseract\tesseract.exe'))) { throw 'Tesseract is unavailable.' }
            [IO.File]::Delete((Join-Path $kitcherVar 'import-worker.stop'))
            $worker = Start-KitcherProcess 'worker' $kitcherPython 'manage.py process_imports'
            Start-Sleep -Milliseconds 500
            $worker.Refresh()
            if ($worker.HasExited) { throw 'Import worker exited; inspect its log.' }
        } catch { Write-Warning "Recognition unavailable: $($_.Exception.Message) Manual features are running." }
    }
    Write-Output "Desktop and iPhone: https://$($env:KITCHER_LAN_IP)/ (trust the public Kitcher root certificate)."
    Write-Output 'Run scripts/install-local-tasks.ps1 once for independent cleanup and daily backup scheduling.'
}
finally { Pop-Location; $lifecycle.ReleaseMutex(); $lifecycle.Dispose() }