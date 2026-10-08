# Register Transbase the way the original EWA installer did. RUN ELEVATED.
#
# WHAT THIS CHANGES ON YOUR MACHINE (and how to undo it):
#   * Creates two Windows services, using Transbase's own installer command
#     (tbmux32.exe install) - not a hand-written sc create:
#         "EWA net DB WIS"   -> tbmux32.exe in TransBase WIS
#         "EWA net DB EPC"   -> tbmux32.exe in TransBase EPC
#   * Sets machine-scope TRANSBASE / TRANSBASE_LOCAL environment variables,
#     because services do not inherit a user session's variables.
#
#   TO UNDO EVERYTHING:
#       "<TransBase WIS>\tbmux32.exe" remove
#       "<TransBase EPC>\tbmux32.exe" remove
#       [Environment]::SetEnvironmentVariable('TRANSBASE',$null,'Machine')
#       [Environment]::SetEnvironmentVariable('TRANSBASE_LOCAL',$null,'Machine')
#   (The script prints these at the end.)
#
# WHY: every hand-started combination fails with
#     Transbase Client Error <11014> MU_LOCK
#     "Cannot login on Multiuser Database, Logserver is obviously not running"
# The multiplexer accepts the TCP connection but never spawns a logserver or
# kernel. The installer log shows the original ran tbmux32 as a service under
# LocalSystem in session 0, which is the one context we have not reproduced.

$ErrorActionPreference = 'Continue'
$Root    = (Join-Path $env:USERPROFILE "Desktop\Mercedes Documents\ewa_extract")
$DbRoot  = "$Root\Program Files\EWA\database"
$WIS     = "$DbRoot\TransBase WIS"
$EPC     = "$DbRoot\TransBase EPC"
$LogFile = Join-Path $PSScriptRoot "service_log.txt"
"" | Set-Content $LogFile -Encoding utf8
function Log { param([string]$t) Write-Host $t; Add-Content -Path $LogFile -Value $t -Encoding utf8 }

$elevated = ([Security.Principal.WindowsPrincipal] `
             [Security.Principal.WindowsIdentity]::GetCurrent()
            ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Log "elevated : $elevated"
if (-not $elevated) { Log "ABORT - must run elevated."; return }

function Run { param([string]$Exe,[string]$Args,[string]$Dir,[int]$Sec=60)
    $o = Join-Path $env:TEMP ("sv_" + [Guid]::NewGuid().ToString('N').Substring(0,6) + ".txt")
    $p = Start-Process -FilePath $Exe -ArgumentList $Args -WorkingDirectory $Dir -NoNewWindow -PassThru `
         -RedirectStandardOutput $o -RedirectStandardError "$o.err"
    if (-not $p.WaitForExit($Sec*1000)) { Stop-Process -Id $p.Id -Force -EA SilentlyContinue; Log "     [timeout]" }
    Get-Content $o,"$o.err" -EA SilentlyContinue |
        Where-Object { $_ -notmatch 'Patent|Copyright|Compiler|Project  :|BuildDate|BuildID|^\s*$' } |
        Select-Object -First 12 | ForEach-Object { Log "     $_" }
}

Log "`n-- stopping anything still running --"
for ($i=0; $i -lt 8; $i++) {
    $p = Get-CimInstance Win32_Process -Filter "Name like 'tb%'" -EA SilentlyContinue
    if (-not $p) { break }
    foreach ($x in $p) { Stop-Process -Id $x.ProcessId -Force -EA SilentlyContinue }
    Start-Sleep -Milliseconds 400
}
Log "   remaining: $((Get-CimInstance Win32_Process -Filter "Name like 'tb%'" -EA SilentlyContinue | Measure-Object).Count)"

# Services get no user environment, so set it machine-wide. WIS wins for the
# bare TRANSBASE var; each service still runs from its own directory.
Log "`n-- setting machine-scope TRANSBASE --"
[Environment]::SetEnvironmentVariable('TRANSBASE', $WIS, 'Machine')
[Environment]::SetEnvironmentVariable('TRANSBASE_LOCAL', $WIS, 'Machine')
Log "   TRANSBASE = $WIS"

foreach ($pair in @(@{d=$WIS; db='wisnet'; port=2054}, @{d=$EPC; db='SPECIALFILES'; port=2034})) {
    $d = $pair.d; $db = $pair.db; $port = $pair.port
    Log "`n==================== $db ===================="
    $env:TRANSBASE = $d; $env:TRANSBASE_LOCAL = $d; $env:PATH = "$d;$env:PATH"

    Log "-- boot database --"
    Run (Join-Path $d "tbadm32.exe") "-b $db" $d 150

    Log "-- remove any previous service registration --"
    Run (Join-Path $d "tbmux32.exe") "remove" $d 30

    Log "-- install service (tbmux32 install) --"
    Run (Join-Path $d "tbmux32.exe") "install" $d 30

    Log "-- start service --"
    Run (Join-Path $d "tbmux32.exe") "start" $d 60
    Start-Sleep -Seconds 3

    Log "-- query --"
    Run (Join-Path $d "tbmux32.exe") "query" $d 30

    $listening = Get-NetTCPConnection -LocalPort $port -State Listen -EA SilentlyContinue
    if ($listening) { Log "   listening on $port" } else { Log "   NOT listening on $port" }
}

Log "`n############ services ############"
Get-Service -EA SilentlyContinue | Where-Object { $_.DisplayName -like '*EWA net DB*' -or $_.Name -like '*EWA net DB*' } |
    ForEach-Object { Log "  $($_.Name) [$($_.Status)] - $($_.DisplayName)" }
Get-CimInstance Win32_Process -Filter "Name like 'tb%'" -EA SilentlyContinue |
    Group-Object Name | ForEach-Object { Log "  process $($_.Name): $($_.Count)" }
Get-NetTCPConnection -State Listen -EA SilentlyContinue |
    Where-Object { $_.LocalPort -in 2034,2035,2054,2055 } |
    ForEach-Object { Log "  listening $($_.LocalAddress):$($_.LocalPort)" }

Log "`n############ TO UNDO ############"
Log "  `"$WIS\tbmux32.exe`" remove"
Log "  `"$EPC\tbmux32.exe`" remove"
Log "  [Environment]::SetEnvironmentVariable('TRANSBASE',`$null,'Machine')"
Log "  [Environment]::SetEnvironmentVariable('TRANSBASE_LOCAL',`$null,'Machine')"
Write-Host "`nDONE - tell me and I will test JDBC." -ForegroundColor Green
