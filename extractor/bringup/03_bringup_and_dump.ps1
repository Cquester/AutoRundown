# Bring Transbase up properly and dump the schema. RUN ELEVATED.
#
# The key discovery: tbserv32.exe is NOT the listener - it starts, logs
# nothing and opens no port. tbkern32.exe -g IS the listener; it opened
# port 2054 on the very first try. Transbase's design is that tbserv32
# forks tbkern32 per connection, but tbkern32 -g ("no forks") serves directly.
#
# So the working sequence is:
#     1. tbadm32 -b <db>      boot (needs elevation: Global\ shared memory)
#     2. tbkern32 -g          serving kernel, listens on TRANSBASE_SERVICENAMES
#     3. tbi32 <db>           client, LINKED_IN=OFF so it goes over TCP
#
# Linked-in mode is deliberately NOT used: attaching in-process to an
# already-booted database deadlocks (that was the earlier 3-minute hang).
#
# This script leaves tbkern32 RUNNING on purpose, so further queries can be
# made over TCP without elevation.
#
# Output: schema\*.txt and bringup_log.txt

$ErrorActionPreference = 'Continue'
$Root    = (Join-Path $env:USERPROFILE "Desktop\Mercedes Documents\ewa_extract")
$DbRoot  = "$Root\Program Files\EWA\database"
$OutDir  = Join-Path $PSScriptRoot "schema"
$LogFile = Join-Path $PSScriptRoot "bringup_log.txt"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
"" | Set-Content $LogFile -Encoding utf8

function Log { param([string]$t) Write-Host $t; Add-Content -Path $LogFile -Value $t -Encoding utf8 }

$elevated = ([Security.Principal.WindowsPrincipal] `
             [Security.Principal.WindowsIdentity]::GetCurrent()
            ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Log "elevated : $elevated"
Log "when     : $(Get-Date -Format 'HH:mm:ss')"
if (-not $elevated) { Log "WARNING: not elevated - boot will fail." }

function Run-Exe {
    param([string]$Dir, [string]$Exe, [string]$Arguments,
          [int]$Seconds = 60, [string]$StdIn = $null, [string]$OutFile = $null)
    $tag = [Guid]::NewGuid().ToString('N').Substring(0,8)
    $out = if ($OutFile) { $OutFile } else { Join-Path $env:TEMP "b_$tag.out" }
    $err = "$out.err"
    $sp = @{ FilePath = (Join-Path $Dir $Exe); ArgumentList = $Arguments;
             NoNewWindow = $true; PassThru = $true; WorkingDirectory = $Dir;
             RedirectStandardOutput = $out; RedirectStandardError = $err }
    if ($StdIn) { $sp['RedirectStandardInput'] = $StdIn }
    $p = Start-Process @sp
    $ok = $p.WaitForExit($Seconds * 1000)
    if (-not $ok) { Stop-Process -Id $p.Id -Force -EA SilentlyContinue; Log "     [HUNG - killed after ${Seconds}s]" }
    if (-not $OutFile) {
        foreach ($f in @($out,$err)) {
            if (Test-Path $f) {
                Get-Content $f | Where-Object { $_ -notmatch 'Patent|Copyright|Compiler :|Project  :|BuildDate|BuildID|^\s*$' } |
                    Select-Object -First 20 | ForEach-Object { Log "     $_" }
            }
        }
    }
    return $ok
}

function Kill-Tb {
    for ($i = 0; $i -lt 6; $i++) {
        $p = Get-CimInstance Win32_Process -Filter "Name like 'tb%'" -EA SilentlyContinue
        if (-not $p) { return }
        foreach ($x in $p) { Stop-Process -Id $x.ProcessId -Force -EA SilentlyContinue }
        Start-Sleep -Milliseconds 400
    }
}

function Bring-Up {
    param([string]$EngineDir, [string]$Db, [int]$Port)
    Log "`n===================================================================="
    Log "=== $Db   engine=$(Split-Path $EngineDir -Leaf)   port=$Port"
    Log "===================================================================="
    $env:TRANSBASE       = $EngineDir
    $env:TRANSBASE_LOCAL = $EngineDir
    $env:PATH            = "$EngineDir;$env:PATH"

    # client must use TCP, not linked-in (linked-in deadlocks on a booted db)
    $ini = Join-Path $EngineDir "tbwin.ini"
    (Get-Content $ini -Raw) -replace 'LINKED_IN=ON','LINKED_IN=OFF' | Set-Content $ini -Encoding ascii

    Log "-- shutdown (clean slate) --"
    Run-Exe $EngineDir "tbadm32.exe" "-s $Db" 60 | Out-Null
    Log "-- boot --"
    Run-Exe $EngineDir "tbadm32.exe" "-b $Db" 120 | Out-Null

    Log "-- start tbkern32 -g (serving kernel) --"
    $before = (Get-NetTCPConnection -State Listen -EA SilentlyContinue).LocalPort | Sort-Object -Unique
    Start-Process -FilePath (Join-Path $EngineDir "tbkern32.exe") -ArgumentList "-g" `
                  -WorkingDirectory $EngineDir -WindowStyle Hidden
    $found = $false
    for ($i = 0; $i -lt 25; $i++) {
        Start-Sleep -Seconds 1
        if (Get-NetTCPConnection -LocalPort $Port -State Listen -EA SilentlyContinue) { $found = $true; break }
    }
    if ($found) { Log "   OK - listening on $Port after ${i}s" }
    else {
        $new = (Get-NetTCPConnection -State Listen -EA SilentlyContinue).LocalPort |
               Sort-Object -Unique | Where-Object { $before -notcontains $_ }
        if ($new) { Log "   listening on unexpected port(s): $($new -join ',')" }
        else { Log "   NO PORT opened - queries below will fail" }
    }

    $sqls = @{
        'tables'  = "select tname, ttype from systable order by tname;"
        'columns' = "select tname, cname, ctype, clength from syscolumn order by tname, cpos;"
        'indexes' = "select * from sysindex;"
    }
    foreach ($label in @('tables','columns','indexes')) {
        $in  = Join-Path $env:TEMP "q_$label.sql"
        Set-Content $in -Value ($sqls[$label] + "`r`nquit;") -Encoding ascii
        $outFile = Join-Path $OutDir "$Db.$label.txt"
        Run-Exe $EngineDir "tbi32.exe" $Db 120 $in $outFile | Out-Null
        $len = 0
        if (Test-Path $outFile) { $len = (Get-Item $outFile).Length }
        Log ("   {0,-8} -> {1} bytes" -f $label, $len)
        if ($len -eq 0) {
            Get-Content "$outFile.err" -EA SilentlyContinue |
                Where-Object { $_ -notmatch 'Version|^\s*$' } | Select-Object -First 4 |
                ForEach-Object { Log "        ! $_" }
        }
    }
}

Log "`n-- clearing stray Transbase processes --"
Kill-Tb
Log "   remaining: $((Get-CimInstance Win32_Process -Filter "Name like 'tb%'" -EA SilentlyContinue | Measure-Object).Count)"

Bring-Up "$DbRoot\TransBase WIS" "wisnet"       2054
Bring-Up "$DbRoot\TransBase EPC" "SPECIALFILES" 2034

Log "`n############ summary ############"
Get-NetTCPConnection -State Listen -EA SilentlyContinue |
    Where-Object { $_.LocalPort -in 2034,2035,2044,2045,2054,2055 } |
    ForEach-Object { Log "  listening $($_.LocalAddress):$($_.LocalPort)" }
Get-ChildItem $OutDir -File -EA SilentlyContinue | Where-Object { $_.Length -gt 0 } |
    ForEach-Object { Log "  $($_.Name)  $($_.Length) bytes" }

Log "`nLEAVING tbkern32 RUNNING so further queries work over TCP without elevation."
Log "To stop later:  taskkill /IM tbkern32.exe /F"
Write-Host "`nDONE. Send me:" -ForegroundColor Green
Write-Host "   $LogFile" -ForegroundColor Green
