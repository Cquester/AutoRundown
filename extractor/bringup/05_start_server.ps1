# Boot the databases and leave a complete Transbase server stack running.
# RUN ELEVATED. This is the only elevated step needed.
#
# What we now know (JDBC gave us a real error code at last):
#
#   Transbase Client Error <11014> = MU_LOCK, defined in the shipped TBERROR.H as
#       "Cannot login on Multiuser Database, Logserver is obviously not running"
#
# So TWO server processes are required and we had only ever run one at a time:
#
#   tbserv32.exe  - the LOGSERVER / supervisor. Binds no port, prints nothing.
#                   That is why it looked broken; it was doing its job silently.
#   tbkern32.exe  - the connection kernel. THIS is what opens the TCP port
#                   (2054 for wisnet, 2034 for SPECIALFILES). Without "-g" it
#                   forks per connection and stays up; with "-g" it serves one
#                   connection and exits.
#
# Both must be elevated, because both attach to the database's shared memory
# in the Global\ namespace. JDBC clients afterwards do NOT need elevation -
# they only talk to the TCP port.
#
# Order: boot -> logserver -> kernel.
#
# Leaves everything RUNNING. To stop:
#     taskkill /IM tbkern32.exe /F
#     taskkill /IM tbserv32.exe /F

$ErrorActionPreference = 'Continue'
$Root    = (Join-Path $env:USERPROFILE "Desktop\Mercedes Documents\ewa_extract")
$DbRoot  = "$Root\Program Files\EWA\database"
$LogFile = Join-Path $PSScriptRoot "server_log.txt"
"" | Set-Content $LogFile -Encoding utf8
function Log { param([string]$t) Write-Host $t; Add-Content -Path $LogFile -Value $t -Encoding utf8 }

$elevated = ([Security.Principal.WindowsPrincipal] `
             [Security.Principal.WindowsIdentity]::GetCurrent()
            ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Log "elevated : $elevated"
Log "when     : $(Get-Date -Format 'HH:mm:ss')"
if (-not $elevated) { Log "WARNING: not elevated - servers cannot attach to the database." }

function Kill-Tb {
    for ($i = 0; $i -lt 8; $i++) {
        $p = Get-CimInstance Win32_Process -Filter "Name like 'tb%'" -EA SilentlyContinue
        if (-not $p) { return }
        foreach ($x in $p) { Stop-Process -Id $x.ProcessId -Force -EA SilentlyContinue }
        Start-Sleep -Milliseconds 400
    }
}

function Wait-Listen {
    param([int]$Port, [int]$Seconds = 15)
    for ($i = 0; $i -lt ($Seconds * 2); $i++) {
        Start-Sleep -Milliseconds 500
        if (Get-NetTCPConnection -LocalPort $Port -State Listen -EA SilentlyContinue) { return $true }
    }
    return $false
}

function Serve {
    param([string]$Dir, [string]$Db, [int]$Port)
    Log "`n==================== $Db  (port $Port) ===================="
    $env:TRANSBASE = $Dir; $env:TRANSBASE_LOCAL = $Dir; $env:PATH = "$Dir;$env:PATH"
    $ini = Join-Path $Dir "tbwin.ini"
    (Get-Content $ini -Raw) -replace 'LINKED_IN=ON','LINKED_IN=OFF' | Set-Content $ini -Encoding ascii

    Log "-- 1. boot --"
    $b = Start-Process -FilePath (Join-Path $Dir "tbadm32.exe") -ArgumentList "-b $Db" `
         -NoNewWindow -PassThru -WorkingDirectory $Dir `
         -RedirectStandardOutput "$env:TEMP\sv.out" -RedirectStandardError "$env:TEMP\sv.err"
    $b.WaitForExit(120000) | Out-Null
    Get-Content "$env:TEMP\sv.out","$env:TEMP\sv.err" -EA SilentlyContinue |
        Where-Object { $_ -match 'boot database|denied|error' } | Select-Object -First 3 |
        ForEach-Object { Log "     $_" }

    Log "-- 2. logserver (tbserv32) --"
    Start-Process -FilePath (Join-Path $Dir "tbserv32.exe") -WorkingDirectory $Dir -WindowStyle Hidden
    Start-Sleep -Seconds 5
    $n = (Get-CimInstance Win32_Process -Filter "Name='tbserv32.exe'" -EA SilentlyContinue | Measure-Object).Count
    Log "     tbserv32 processes running: $n"

    Log "-- 3. connection kernel (tbkern32, forking) --"
    Start-Process -FilePath (Join-Path $Dir "tbkern32.exe") -WorkingDirectory $Dir -WindowStyle Hidden
    if (Wait-Listen $Port 15) { Log "     OK - listening on $Port" }
    else { Log "     no listener on $Port" }
}

Kill-Tb
Serve "$DbRoot\TransBase WIS" "wisnet"       2054
Serve "$DbRoot\TransBase EPC" "SPECIALFILES" 2034

Log "`n############ final state ############"
Get-CimInstance Win32_Process -Filter "Name like 'tb%'" -EA SilentlyContinue |
    Group-Object Name | ForEach-Object { Log "  $($_.Name): $($_.Count) running" }
$any = Get-NetTCPConnection -State Listen -EA SilentlyContinue |
       Where-Object { $_.LocalPort -in 2034,2054 }
if ($any) { foreach ($c in $any) { Log "  listening $($c.LocalAddress):$($c.LocalPort)" } }
else { Log "  nothing listening" }

Log "`nLeft RUNNING. Stop later with:"
Log "  taskkill /IM tbkern32.exe /F"
Log "  taskkill /IM tbserv32.exe /F"
Write-Host "`nDONE - tell me and I will connect over JDBC." -ForegroundColor Green
