param([Parameter(Mandatory=$true)][string]$AuditFile)
. (Join-Path $PSScriptRoot 'local-common.ps1')
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Only the firewall helper requires administrator elevation.' }
$auditPath = [IO.Path]::GetFullPath($AuditFile)
$expectedRoot = [IO.Path]::GetFullPath((Join-Path $kitcherVar 'offline-checks')) + '\'
if (-not $auditPath.StartsWith($expectedRoot,[StringComparison]::OrdinalIgnoreCase) -or (Get-Item -LiteralPath $auditPath).Length -gt 65536) { throw 'Use a small audit file within var/offline-checks.' }
$audit = Get-Content -LiteralPath $auditPath -Raw -Encoding UTF8 | ConvertFrom-Json
$addresses = @('0.0.0.0-126.255.255.255','128.0.0.0-255.255.255.255','::','::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff')
$programs = @((Join-Path $kitcherVar 'offline-runtime\python.exe'),(Join-Path $kitcherVar 'tools\ollama\ollama.exe'),(Join-Path $kitcherVar 'tools\ollama\lib\ollama\llama-server.exe'),(Join-Path $kitcherVar 'tools\tesseract\tesseract.exe'))
if ($audit.schema -ne 1 -or $audit.group -notmatch '^KitcherOffline-[a-f0-9]{32}$' -or $audit.rules.Count -ne $programs.Count) { throw 'Invalid offline rule request.' }
for ($index=0; $index -lt $programs.Count; $index++) {
    $entry = $audit.rules[$index]
    if ($entry.name -ne "$($audit.group)-$index" -or $entry.program -ne $programs[$index] -or (Get-FileHash -LiteralPath $entry.program -Algorithm SHA256).Hash -ne $entry.sha256) { throw 'Offline executable/name/checksum validation failed.' }
    if (Get-NetFirewallRule -Name $entry.name -ErrorAction SilentlyContinue) { throw 'Refusing to replace an existing firewall rule.' }
}
$statePath = Join-Path (Split-Path -Parent $auditPath) 'firewall-state.json'
$releasePath = Join-Path (Split-Path -Parent $auditPath) 'release-rules'
$state = [ordered]@{state='preparing'; created=[DateTime]::UtcNow.ToString('o'); effective=@(); cleanup_verified=$false}
function Save-OfflineState { [IO.File]::WriteAllText($statePath,($state | ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false)) }
try {
    $profiles = @(Get-NetFirewallProfile)
    if (@($profiles | Where-Object { -not $_.Enabled }).Count -or (Get-Service MpsSvc).Status -ne 'Running') { throw 'Firewall service and all profiles must already be enabled.' }
    Save-OfflineState
    foreach ($entry in $audit.rules) {
        New-NetFirewallRule -Name $entry.name -DisplayName 'Kitcher temporary offline acceptance check' -Group $audit.group -Direction Outbound -Action Block -Program $entry.program -Protocol Any -RemoteAddress $addresses -Profile Any -Enabled True -ErrorAction Stop | Out-Null
    }
    foreach ($entry in $audit.rules) {
        $rule = Get-NetFirewallRule -PolicyStore ActiveStore -Name $entry.name -ErrorAction Stop
        $application = $rule | Get-NetFirewallApplicationFilter
        $address = $rule | Get-NetFirewallAddressFilter
        if ($rule.Enabled -ne 'True' -or $rule.Direction -ne 'Outbound' -or $rule.Action -ne 'Block' -or $application.Program -ne $entry.program -or $rule.Profile -ne 'Any' -or @(Compare-Object -ReferenceObject $addresses -DifferenceObject @($address.RemoteAddress)).Count) { throw 'Effective offline rule validation failed.' }
        $state.effective += @{name=$rule.Name; enabled=[string]$rule.Enabled; direction=[string]$rule.Direction; action=[string]$rule.Action; profile=[string]$rule.Profile; program=$application.Program; remote_addresses=@($address.RemoteAddress); primary_status=[string]$rule.PrimaryStatus; enforcement_status=[string]$rule.EnforcementStatus}
    }
    $state.state = 'enforced'
    Save-OfflineState
    $deadline = [Diagnostics.Stopwatch]::StartNew()
    while (-not (Test-Path -LiteralPath $releasePath)) {
        if ($deadline.Elapsed.TotalSeconds -gt 2400) { throw 'Offline window expired; temporary rules will be removed.' }
        Start-Sleep -Milliseconds 250
    }
}
catch { $state.error = $_.Exception.Message; Save-OfflineState; throw }
finally {
    & (Join-Path $PSScriptRoot 'clear-offline-rules.ps1') -Audit $auditPath
    $state.state = 'rules-removed'
    $state.cleanup_verified = $true
    $state.removed_at = [DateTime]::UtcNow.ToString('o')
    Save-OfflineState
}