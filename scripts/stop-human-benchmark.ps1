. (Join-Path $PSScriptRoot 'local-common.ps1')
$lifecycle = Enter-KitcherLifecycle
try { Stop-KitcherProcess 'human-benchmark'; Write-Output 'Only the recorded isolated benchmark server was stopped.' }
finally { $lifecycle.ReleaseMutex(); $lifecycle.Dispose() }