# Dump the Transbase schema. RUN ELEVATED.
#
# Everything learned so far, applied:
#
#  1. tbadm32 -b boots the database - needs elevation (Global\ shared memory).
#  2. tbkern32 -g is the serving kernel and opens the TCP port. tbserv32 is
#     NOT the listener; it starts, logs nothing and binds nothing.
#  3. tbkern32 -g means "no forks" - it serves ONE connection then exits.
#     So we start a FRESH kernel before every single query.
#  4. tbi32 prompts "Login: [..]" and "Password:" after connecting. Feeding it
#     bare SQL made it swallow the query as a username and block forever -
#     that was the 3-minute hang, not stdin buffering and not linked-in mode.
#     Credentials go first: user "tbadmin", empty password (from the shipped
#     qml_transbase.bat).
#  5. The kernel must be elevated to attach to the booted database; the client
#     need not be.
#
# For each query we try credentials-first, and fall back to bare SQL in case
# this build does not prompt.
#
# Output: schema\<db>.<label>.txt plus dump_log.txt

$ErrorActionPreference = 'Continue'
$Root    = (Join-Path $env:USERPROFILE "Desktop\Mercedes Documents\ewa_extract")
$DbRoot  = "$Root\Program Files\EWA\database"
$OutDir  = Join-Path $PSScriptRoot "schema"
$LogFile = Join-Path $PSScriptRoot "dump_log.txt"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
"" | Set-Content $LogFile -Encoding utf8

function Log { param([string]$t) Write-Host $t; Add-Content -Path $LogFile -Value $t -Encoding utf8 }

$elevated = ([Security.Principal.WindowsPrincipal] `
             [Security.Principal.WindowsIdentity]::GetCurrent()
            ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Log "elevated : $elevated"
Log "when     : $(Get-Date -Format 'HH:mm:ss')"
if (-not $elevated) { Log "WARNING: not elevated - the kernel cannot attach; everything will fail." }

function Kill-Tb {
    for ($i = 0; $i -lt 8; $i++) {
        $p = Get-CimInstance Win32_Process -Filter "Name like 'tb%'" -EA SilentlyContinue
        if (-not $p) { return }
        foreach ($x in $p) { Stop-Process -Id $x.ProcessId -Force -EA SilentlyContinue }
        Start-Sleep -Milliseconds 400
    }
}

function Start-Kernel {
    param([string]$Dir, [int]$Port)
    Start-Process -FilePath (Join-Path $Dir "tbkern32.exe") -ArgumentList "-g" `
                  -WorkingDirectory $Dir -WindowStyle Hidden
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 500
        if (Get-NetTCPConnection -LocalPort $Port -State Listen -EA SilentlyContinue) { return $true }
    }
    return $false
}

function Query {
    param([string]$Dir, [string]$Db, [int]$Port, [string]$Label, [string]$Sql)
    $outFile = Join-Path $OutDir "$Db.$Label.txt"
    $variants = @(
        @{ name = 'creds';   text = "tbadmin`r`n`r`n$Sql`r`nquit;" },
        @{ name = 'no-creds'; text = "$Sql`r`nquit;" }
    )
    foreach ($v in $variants) {
        Kill-Tb
        if (-not (Start-Kernel $Dir $Port)) { Log "   [$Label/$($v.name)] kernel did not open $Port"; continue }
        $in = Join-Path $env:TEMP "q_$Label.sql"
        Set-Content $in -Value $v.text -Encoding ascii
        $p = Start-Process -FilePath (Join-Path $Dir "tbi32.exe") -ArgumentList $Db `
             -NoNewWindow -PassThru -WorkingDirectory $Dir `
             -RedirectStandardInput $in -RedirectStandardOutput $outFile `
             -RedirectStandardError "$outFile.err"
        $ok = $p.WaitForExit(90000)
        if (-not $ok) { Stop-Process -Id $p.Id -Force -EA SilentlyContinue }
        $len = 0
        if (Test-Path $outFile) { $len = (Get-Item $outFile).Length }
        Log ("   {0,-8} {1,-9} exited={2,-5} {3} bytes" -f $Label, $v.name, $ok, $len)
        if ($len -gt 40) {
            Get-Content $outFile | Select-Object -First 3 | ForEach-Object { Log "        | $_" }
            return
        }
        Get-Content "$outFile.err" -EA SilentlyContinue |
            Where-Object { $_ -notmatch 'Version|^\s*$' } | Select-Object -First 3 |
            ForEach-Object { Log "        ! $_" }
    }
}

function Dump {
    param([string]$Dir, [string]$Db, [int]$Port)
    Log "`n===================================================================="
    Log "=== $Db   port=$Port"
    Log "===================================================================="
    $env:TRANSBASE = $Dir; $env:TRANSBASE_LOCAL = $Dir; $env:PATH = "$Dir;$env:PATH"
    $ini = Join-Path $Dir "tbwin.ini"
    (Get-Content $ini -Raw) -replace 'LINKED_IN=ON','LINKED_IN=OFF' | Set-Content $ini -Encoding ascii

    Kill-Tb
    Log "-- boot --"
    $b = Start-Process -FilePath (Join-Path $Dir "tbadm32.exe") -ArgumentList "-b $Db" `
         -NoNewWindow -PassThru -WorkingDirectory $Dir `
         -RedirectStandardOutput "$env:TEMP\boot.out" -RedirectStandardError "$env:TEMP\boot.err"
    $b.WaitForExit(120000) | Out-Null
    Get-Content "$env:TEMP\boot.out","$env:TEMP\boot.err" -EA SilentlyContinue |
        Where-Object { $_ -match 'boot database|error|denied' } | Select-Object -First 4 |
        ForEach-Object { Log "     $_" }

    Query $Dir $Db $Port "tables"  "select tname, ttype from systable order by tname;"
    Query $Dir $Db $Port "columns" "select tname, cname, ctype, clength from syscolumn order by tname, cpos;"
    Query $Dir $Db $Port "indexes" "select * from sysindex;"
}

Dump "$DbRoot\TransBase WIS" "wisnet"       2054
Dump "$DbRoot\TransBase EPC" "SPECIALFILES" 2034

Log "`n############ summary ############"
Get-ChildItem $OutDir -File -EA SilentlyContinue |
    Where-Object { $_.Name -notlike '*.err' } |
    ForEach-Object { Log ("  {0,-34} {1} bytes" -f $_.Name, $_.Length) }

Kill-Tb
Log "`nDone. Kernels stopped."
Write-Host "`nDONE. Send me:" -ForegroundColor Green
Write-Host "   $LogFile" -ForegroundColor Green
