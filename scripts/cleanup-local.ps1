. (Join-Path $PSScriptRoot 'local-common.ps1')
Push-Location -LiteralPath $kitcherRoot
try {
    Set-KitcherEnvironment
    Invoke-KitcherCommand @('cleanup_imports')
    & $kitcherPython (Join-Path $PSScriptRoot 'cleanup-trials.py')
    if ($LASTEXITCODE -ne 0) { throw 'Isolated trial-source cleanup failed; inspect var/trial-cleanup-status.json.' }
}
finally { Pop-Location }
