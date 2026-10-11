. (Join-Path $PSScriptRoot 'local-common.ps1')
$taskUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$taskPowerShell = Join-Path $PSHOME 'powershell.exe'
if (-not (Test-Path -LiteralPath $taskPowerShell)) { $taskPowerShell = (Get-Command powershell.exe).Source }
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $taskUser -LogonType Interactive -RunLevel Limited
$taskSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$taskDefinitions = @(
    @{Name='KitcherCleanup'; Script='cleanup-local.ps1'; Trigger=(New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 15))},
    @{Name='KitcherBackup'; Script='backup-local.ps1'; Trigger=(New-ScheduledTaskTrigger -Daily -At '20:00')},
    @{Name='KitcherStart'; Script='start-local.ps1'; Trigger=(New-ScheduledTaskTrigger -AtLogOn -User $taskUser)}
)
foreach ($definition in $taskDefinitions) {
    $script = Join-Path $PSScriptRoot $definition.Script
    $arguments = "-NoProfile -File `"$script`""
    $existing = Get-ScheduledTask -TaskName $definition.Name -ErrorAction SilentlyContinue
    if ($existing -and ($existing.Actions.Arguments -ne $arguments -or $existing.Principal.UserId -ne $taskUser)) { throw "Task $($definition.Name) belongs to different settings; it was not replaced." }
    $action = New-ScheduledTaskAction -Execute $taskPowerShell -Argument $arguments -WorkingDirectory $kitcherRoot
    Register-ScheduledTask -TaskName $definition.Name -Action $action -Trigger $definition.Trigger -Principal $taskPrincipal -Settings $taskSettings -Description 'Kitcher local app maintenance; no account password stored.' -Force | Out-Null
    Write-Output "Installed $($definition.Name) for $taskUser."
}
Write-Output 'Tasks run in this signed-in user session. Startup handles work missed while the computer was off or the user was signed out.'