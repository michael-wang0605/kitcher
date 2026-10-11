$ErrorActionPreference = 'Stop'
$kitcherRoot = Split-Path -Parent $PSScriptRoot
$kitcherVar = Join-Path $kitcherRoot 'var'
$kitcherPython = Join-Path $kitcherRoot '.venv\Scripts\python.exe'
$kitcherCaddy = Join-Path $kitcherVar 'tools\caddy\caddy.exe'

function Get-KitcherProcess([string]$Name) {
    $recordPath = Join-Path $kitcherVar "$Name-process.json"
    if (-not (Test-Path -LiteralPath $recordPath)) { return $null }
    $record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
    $existing = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
    if ($existing -and $existing.StartTime.ToUniversalTime().Ticks -eq $record.startedTicks -and $existing.Path -eq $record.executable) { return $existing }
    return $null
}

function Start-KitcherProcess([string]$Name, [string]$Executable, [string]$Arguments) {
    $existing = Get-KitcherProcess $Name
    if ($existing) { return $existing }
    $process = Start-Process -FilePath $Executable -ArgumentList $Arguments -WorkingDirectory $kitcherRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $kitcherVar "$Name.stdout.log") -RedirectStandardError (Join-Path $kitcherVar "$Name.stderr.log")
    @{pid=$process.Id; startedTicks=$process.StartTime.ToUniversalTime().Ticks; executable=$Executable} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $kitcherVar "$Name-process.json") -Encoding UTF8
    return $process
}

function Get-KitcherProcessTree([Diagnostics.Process]$RootProcess) {
    $RootProcess
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$($RootProcess.Id)" -ErrorAction SilentlyContinue
    foreach ($child in $children) {
        if ($child.CreationDate -ge $RootProcess.StartTime) {
            $childProcess = Get-Process -Id $child.ProcessId -ErrorAction SilentlyContinue
            if ($childProcess -and $childProcess.StartTime -ge $RootProcess.StartTime) { Get-KitcherProcessTree $childProcess }
        }
    }
}

function Stop-KitcherProcess([string]$Name) {
    $process = Get-KitcherProcess $Name
    if ($process) {
        $tree = @(Get-KitcherProcessTree $process)
        [Array]::Reverse($tree)
        foreach ($owned in $tree) {
            $current = Get-Process -Id $owned.Id -ErrorAction SilentlyContinue
            if ($current -and $current.StartTime.ToUniversalTime().Ticks -eq $owned.StartTime.ToUniversalTime().Ticks -and $current.Path -eq $owned.Path) { Stop-Process -Id $current.Id -ErrorAction Stop }
        }
    }
}

function Enter-KitcherLifecycle {
    $hash = [Security.Cryptography.SHA256]::Create()
    try { $suffix = [BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($kitcherRoot))).Replace('-','').Substring(0,16) } finally { $hash.Dispose() }
    $mutex = [Threading.Mutex]::new($false,"Local\Kitcher-$suffix")
    try { $acquired = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $acquired=$true }
    if (-not $acquired) { $mutex.Dispose(); throw 'Another Kitcher startup/shutdown is running.' }
    return $mutex
}

function Invoke-KitcherCommand([string[]]$CommandArguments) {
    & $kitcherPython (Join-Path $kitcherRoot 'manage.py') @CommandArguments
    if ($LASTEXITCODE -ne 0) { throw "Kitcher command failed: $($CommandArguments[0])." }
}

function Set-KitcherEnvironment {
    $localConfig = Join-Path $kitcherVar 'local.env'
    if (-not (Test-Path -LiteralPath $localConfig)) { throw 'Configure var/local.env first.' }
    foreach ($line in Get-Content -LiteralPath $localConfig) {
        if ($line.Trim() -and -not $line.TrimStart().StartsWith('#') -and $line.Contains('=')) {
            $parts = $line.Split('=',2)
            [Environment]::SetEnvironmentVariable($parts[0].Trim(),$parts[1].Trim(),'Process')
        }
    }
    if ($env:KITCHER_DATA_DIR -and [IO.Path]::GetFullPath($env:KITCHER_DATA_DIR) -ne [IO.Path]::GetFullPath($kitcherVar)) { throw 'These machine startup scripts operate only on this repository var directory.' }
    if ($env:KITCHER_DATABASE -and [IO.Path]::GetFullPath($env:KITCHER_DATABASE) -ne [IO.Path]::GetFullPath((Join-Path $kitcherVar 'kitcher.sqlite3'))) { throw 'Machine startup/maintenance scripts use only var/kitcher.sqlite3. Use separate command sessions for recovery copies.' }
    $env:KITCHER_CADDY_DATA = (Join-Path $kitcherVar 'caddy-data').Replace('\','/')
    $env:KITCHER_STATIC_ROOT = (Join-Path $kitcherVar 'static').Replace('\','/')
}

function Assert-KitcherPort([int]$Port, [string]$Name) {
    $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($listeners) {
        $owned = Get-KitcherProcess $Name
        if (-not $owned) { throw "Port $Port is occupied by another process; it was not stopped." }
        $ownedIds = @(Get-KitcherProcessTree $owned | ForEach-Object Id)
        if (@($listeners | Where-Object { $_.OwningProcess -notin $ownedIds }).Count) { throw "Port $Port is occupied by another process; it was not stopped." }
    }
}