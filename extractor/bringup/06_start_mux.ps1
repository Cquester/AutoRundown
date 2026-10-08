# Start the Transbase server properly. RUN ELEVATED.
#
# THE ACTUAL ARCHITECTURE (finally established from the installer's own log,
# Program Files\EWA\install\log\log.00\wisnet.log):
#
#   TRANSBASE_InstallMultiplexorService
#     Multiplexor : tbmux32.exe        <- THE SERVER
#     Service     : "EWA net DB WIS"
#     Ports       : 2054:2055
#
#   tbmux32.exe is the front end. It owns the TCP port and spawns tbserv32
#   (logserver) and tbkern32 (per-connection kernels) itself. Starting those
#   two by hand - which is what every earlier attempt did - gives a listening
#   socket with no logserver behind it, hence:
#       Transbase Client Error <11014> MU_LOCK
#       "Cannot login on Multiuser Database, Logserver is obviously not running"
#
#   tbmux32 supports "noservice", so it can run in the foreground without
#   registering a Windows service. That is what this script uses.
#
# Sequence: kill strays -> boot database -> start tbmux32 noservice -> verify.
# Leaves the multiplexer RUNNING. Stop later with: taskkill /IM tbmux32.exe /F

$ErrorActionPreference = 'Continue'
$Root    = (Join-Path $env:USERPROFILE "Desktop\Mercedes Documents\ewa_extract")
$DbRoot  = "$Root\Program Files\EWA\database"
$LogFile = Join-Path $PSScriptRoot "mux_log.txt"
"" | Set-Content $LogFile -Encoding utf8
function Log { param([string]$t) Write-Host $t; Add-Content -Path $LogFile -Value $t -Encoding utf8 }

$elevated = ([Security.Principal.WindowsPrincipal] `
             [Security.Principal.WindowsIdentity]::GetCurrent()
            ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Log "elevated : $elevated"
Log "when     : $(Get-Date -Format 'HH:mm:ss')"
if (-not $elevated) { Log "WARNING: not elevated - boot and shared-memory attach will fail." }

function Kill-Tb {
    for ($i = 0; $i -lt 10; $i++) {
        $p = Get-CimInstance Win32_Process -Filter "Name like 'tb%'" -EA SilentlyContinue
        if (-not $p) { return $true }
        foreach ($x in $p) { Stop-Process -Id $x.ProcessId -Force -EA SilentlyContinue }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Start-Db {
    param([string]$Dir, [string]$Db, [int]$Port)
    Log "`n==================== $Db  (port $Port) ===================="
    $env:TRANSBASE = $Dir; $env:TRANSBASE_LOCAL = $Dir; $env:PATH = "$Dir;$env:PATH"
    $ini = Join-Path $Dir "tbwin.ini"
    (Get-Content $ini -Raw) -replace 'LINKED_IN=ON','LINKED_IN=OFF' | Set-Content $ini -Encoding ascii

    Log "-- boot database --"
    $b = Start-Process -FilePath (Join-Path $Dir "tbadm32.exe") -ArgumentList "-b $Db" `
         -NoNewWindow -PassThru -WorkingDirectory $Dir `
         -RedirectStandardOutput "$env:TEMP\m.out" -RedirectStandardError "$env:TEMP\m.err"
    $b.WaitForExit(150000) | Out-Null
    Get-Content "$env:TEMP\m.out","$env:TEMP\m.err" -EA SilentlyContinue |
        Where-Object { $_ -match 'boot database|denied|error|sm_create' } | Select-Object -First 4 |
        ForEach-Object { Log "     $_" }

    Log "-- start tbmux32 noservice (the server) --"
    $mo = Join-Path $env:TEMP "mux_$Db.out"
    Start-Process -FilePath (Join-Path $Dir "tbmux32.exe") -ArgumentList "noservice" `
                  -WorkingDirectory $Dir -WindowStyle Hidden `
                  -RedirectStandardOutput $mo -RedirectStandardError "$mo.err"
    $ok = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        if (Get-NetTCPConnection -LocalPort $Port -State Listen -EA SilentlyContinue) { $ok = $true; break }
    }
    if ($ok) { Log "     OK - listening on $Port" }
    else {
        Log "     NOT listening on $Port - multiplexer output:"
        Get-Content $mo,"$mo.err" -EA SilentlyContinue |
            Where-Object { $_ -notmatch 'Patent|Copyright|Compiler|Project  :|BuildDate|BuildID|^\s*$' } |
            Select-Object -First 12 | ForEach-Object { Log "        $_" }
    }
}

Log "-- clearing all Transbase processes --"
if (Kill-Tb) { Log "   clean" } else { Log "   some survived" }

Start-Db "$DbRoot\TransBase WIS" "wisnet"       2054
Start-Db "$DbRoot\TransBase EPC" "SPECIALFILES" 2034

Log "`n############ final state ############"
Get-CimInstance Win32_Process -Filter "Name like 'tb%'" -EA SilentlyContinue |
    Group-Object Name | ForEach-Object { Log "  $($_.Name): $($_.Count)" }
$any = Get-NetTCPConnection -State Listen -EA SilentlyContinue |
       Where-Object { $_.LocalPort -in 2034,2035,2054,2055 }
if ($any) { foreach ($c in $any) { Log "  listening $($c.LocalAddress):$($c.LocalPort)" } }
else { Log "  nothing listening" }

Log "`nMultiplexer left RUNNING. Stop later with: taskkill /IM tbmux32.exe /F"
Write-Host "`nDONE - tell me and I will connect over JDBC." -ForegroundColor Green
