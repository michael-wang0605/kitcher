param([Parameter(Mandatory=$true)][string]$Audit)
. (Join-Path $PSScriptRoot 'local-common.ps1')
$auditPath = [IO.Path]::GetFullPath($Audit)
$expectedRoot = [IO.Path]::GetFullPath((Join-Path $kitcherVar 'offline-checks')) + '\'
if (-not $auditPath.StartsWith($expectedRoot,[StringComparison]::OrdinalIgnoreCase)) { throw 'Use an audit file within var/offline-checks.' }
$record = Get-Content -LiteralPath $auditPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($record.group -notmatch '^KitcherOffline-[a-f0-9]{32}$') { throw 'Invalid offline rule group.' }
$installedRules = @(Get-NetFirewallRule -ErrorAction Stop)
foreach ($entry in $record.rules) {
    if ($entry.name -notmatch ('^' + [regex]::Escape($record.group) + '-[0-9]+$')) { throw 'Invalid recorded rule name.' }
    $rule = $installedRules | Where-Object Name -eq $entry.name
    if ($rule) {
        $application = $rule | Get-NetFirewallApplicationFilter -ErrorAction Stop
        if ($rule.Group -ne $record.group -or $application.Program -ne $entry.program -or $rule.Direction -ne 'Outbound' -or $rule.Action -ne 'Block') { throw 'Rule identity changed; refusing to remove it.' }
        $rule | Remove-NetFirewallRule -ErrorAction Stop
    }

}
$remainingRules = @(Get-NetFirewallRule -ErrorAction Stop | Where-Object { $_.Name -in @($record.rules.name) })
if ($remainingRules.Count) { throw 'Offline firewall rule removal failed.' }
Write-Output 'Only the recorded temporary offline rules were removed; successful firewall enumeration verified their absence.'