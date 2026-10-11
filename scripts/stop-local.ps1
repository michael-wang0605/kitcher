. (Join-Path $PSScriptRoot 'local-common.ps1')
$lifecycle = Enter-KitcherLifecycle
Push-Location -LiteralPath $kitcherRoot
try {
    Set-KitcherEnvironment
    # Stop accepting new web confirmations first. Never stop an unrelated Caddy.
    $proxy = Get-KitcherProcess 'caddy'
    if ($proxy) {
        Assert-KitcherPort 20191 'caddy'
        & $kitcherCaddy stop --address 127.0.0.1:20191
        if ($LASTEXITCODE -ne 0) { Stop-KitcherProcess 'caddy' }
    }
    [IO.File]::WriteAllText((Join-Path $kitcherVar 'import-worker.stop'),'stop')
    $worker = Get-KitcherProcess 'worker'
    if ($worker) {
        # The worker checks the stop marker before its next claim. An active job
        # may finish within its 120-second budget; abandoned leases remain safe.
        $finished = $worker.WaitForExit(125000)
        if (-not $finished) { Stop-KitcherProcess 'worker' }
    }
    Stop-KitcherProcess 'web'
    Stop-KitcherProcess 'development'
    Stop-KitcherProcess 'ollama'
    Invoke-KitcherCommand @('cleanup_imports')
    Invoke-KitcherCommand @('backup_pantry')
    Write-Output 'Kitcher services stopped; consistent shutdown backup verified.'
}
finally { Pop-Location; $lifecycle.ReleaseMutex(); $lifecycle.Dispose() }