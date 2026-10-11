. (Join-Path $PSScriptRoot 'local-common.ps1')
Set-KitcherEnvironment
$phoneAddress = Get-NetIPAddress -AddressFamily IPv4 -IPAddress $env:KITCHER_LAN_IP -ErrorAction Stop
$phoneProfile = Get-NetConnectionProfile -InterfaceIndex $phoneAddress.InterfaceIndex
if ($phoneProfile.NetworkCategory -ne 'Private') { throw 'Phone access requires the trusted home network to be marked Private. This script does not change network trust.' }
$firewallArguments = @{
    Name='KitcherHttpsPrivate'; DisplayName='Kitcher HTTPS (private LAN)'; Direction='Inbound'; Action='Allow';
    Program=$kitcherCaddy; Protocol='TCP'; LocalPort=443; LocalAddress=$env:KITCHER_LAN_IP; RemoteAddress='LocalSubnet'; Profile='Private';
}
$existing = Get-NetFirewallRule -Name KitcherHttpsPrivate -ErrorAction SilentlyContinue
if ($existing) {
    $application = $existing | Get-NetFirewallApplicationFilter
    if ($application.Program -ne $kitcherCaddy) { throw 'Existing named firewall rule belongs to another application.' }
    $existing | Remove-NetFirewallRule
}
New-NetFirewallRule @firewallArguments | Out-Null
Write-Output "Private LAN HTTPS allowed for Caddy at $($env:KITCHER_LAN_IP):443. Waitress and Ollama remain on loopback."