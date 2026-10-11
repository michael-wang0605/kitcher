param([switch]$ConfirmedHomeNetwork)
. (Join-Path $PSScriptRoot 'local-common.ps1')
$setupLog = Join-Path $kitcherVar 'phone-network-setup.log'
try {
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Windows administrator elevation is required for network/firewall setup.' }
    Set-KitcherEnvironment
    $homeAddress = Get-NetIPAddress -AddressFamily IPv4 -IPAddress $env:KITCHER_LAN_IP -ErrorAction Stop
    $homeProfile = Get-NetConnectionProfile -InterfaceIndex $homeAddress.InterfaceIndex
    if ($homeProfile.NetworkCategory -ne 'Private') {
        if (-not $ConfirmedHomeNetwork) { throw 'Confirm this is the trusted home network before changing its category.' }
        Set-NetConnectionProfile -InterfaceIndex $homeAddress.InterfaceIndex -NetworkCategory Private -ErrorAction Stop
    }
    & (Join-Path $PSScriptRoot 'setup-phone-access.ps1')
    "$(Get-Date -Format o): Private network and Caddy-only LAN HTTPS firewall setup completed." | Set-Content -LiteralPath $setupLog -Encoding UTF8
}
catch {
    "$(Get-Date -Format o): $($_.Exception.Message)" | Set-Content -LiteralPath $setupLog -Encoding UTF8
    exit 1
}