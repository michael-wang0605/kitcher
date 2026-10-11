. (Join-Path $PSScriptRoot 'local-common.ps1')
Push-Location -LiteralPath $kitcherRoot
try {
    Set-KitcherEnvironment
    Invoke-KitcherCommand @('backup_pantry')
}
finally { Pop-Location }
