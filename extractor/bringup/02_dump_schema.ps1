# Dump the Transbase schema. RUN ELEVATED, after 01_boot_transbase.ps1.
#
# Why elevated: the databases' shared memory is in the Global\ namespace, so
# only an elevated process can attach to it (sm_attach_glob). tbserv32 is not
# binding a TCP port, so linked-in mode (LINKED_IN=ON) is the way in.
#
# Previous version hung: tbi32 is interactive and does NOT exit on stdin EOF,
# so every query sat until the timeout and its buffered output was lost
# (that is why the first run produced only 0-byte files). Fix: terminate the
# input with an explicit quit. We try "quit;" and fall back to "exit;".
#
# Native stderr is redirected to files, never piped - PowerShell 5.1 turns
# native stderr into NativeCommandError.
#
# Output lands in .\schema\

$ErrorActionPreference = 'Continue'
$Root   = (Join-Path $env:USERPROFILE "Desktop\Mercedes Documents\ewa_extract")
$DbRoot = "$Root\Program Files\EWA\database"
$OutDir = Join-Path $PSScriptRoot "schema"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$elevated = ([Security.Principal.WindowsPrincipal] `
             [Security.Principal.WindowsIdentity]::GetCurrent()
            ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Write-Host "elevated: $elevated"
if (-not $elevated) { Write-Warning "NOT ELEVATED - queries will fail at sm_attach()." }

function Try-Sql {
    param([string]$EngineDir, [string]$Db, [string]$Sql,
          [string]$Terminator, [string]$OutFile, [int]$Seconds = 90)
    $in = Join-Path $env:TEMP ("ar_" + [Guid]::NewGuid().ToString('N').Substring(0,8) + ".sql")
    Set-Content -Path $in -Value ($Sql + "`r`n" + $Terminator) -Encoding ascii
    $p = Start-Process -FilePath (Join-Path $EngineDir "tbi32.exe") `
                       -ArgumentList $Db -NoNewWindow -PassThru `
                       -WorkingDirectory $EngineDir `
                       -RedirectStandardInput $in `
                       -RedirectStandardOutput $OutFile `
                       -RedirectStandardError "$OutFile.err"
    $exited = $p.WaitForExit($Seconds * 1000)
    if (-not $exited) { Stop-Process -Id $p.Id -Force; return $false }
    return $true
}

function Invoke-Sql {
    param([string]$EngineDir, [string]$Db, [string]$Label, [string]$Sql)
    $env:TRANSBASE       = $EngineDir
    $env:TRANSBASE_LOCAL = $EngineDir
    $out = Join-Path $OutDir "$Db.$Label.txt"
    foreach ($term in @("quit;", "exit;", "")) {
        $ok = Try-Sql $EngineDir $Db $Sql $term $out
        $len = 0
        if (Test-Path $out) { $len = (Get-Item $out).Length }
        $how = if ($term) { "terminator '$term'" } else { "no terminator" }
        if ($ok -and $len -gt 0) {
            Write-Host ("  {0,-10} OK   {1,7} bytes  ({2})" -f $Label, $len, $how)
            return
        }
        if (-not $ok) { Write-Host ("  {0,-10} hung with {1}" -f $Label, $how) -ForegroundColor Yellow }
    }
    Write-Host ("  {0,-10} FAILED - see {1}.err" -f $Label, $out) -ForegroundColor Red
    Get-Content "$out.err" -ErrorAction SilentlyContinue |
        Where-Object { $_ -notmatch 'Version|^\s*$' } | Select-Object -First 4 |
        ForEach-Object { Write-Host "     ! $_" -ForegroundColor Yellow }
}

function Dump-Db {
    param([string]$EngineDir, [string]$Db)
    Write-Host "`n=== $Db ===" -ForegroundColor Cyan
    $ini = Join-Path $EngineDir "tbwin.ini"
    (Get-Content $ini -Raw) -replace 'LINKED_IN=OFF', 'LINKED_IN=ON' |
        Set-Content $ini -Encoding ascii

    Invoke-Sql $EngineDir $Db "tables"  "select tname, ttype from systable order by tname;"
    Invoke-Sql $EngineDir $Db "columns" "select tname, cname, ctype, clength from syscolumn order by tname, cpos;"
    Invoke-Sql $EngineDir $Db "indexes" "select * from sysindex;"
}

Dump-Db "$DbRoot\TransBase WIS" "wisnet"
Dump-Db "$DbRoot\TransBase EPC" "SPECIALFILES"

Write-Host "`nDONE. Send me this folder:" -ForegroundColor Green
Write-Host "   $OutDir" -ForegroundColor Green
Get-ChildItem $OutDir -File | Where-Object { $_.Length -gt 0 } |
    Select-Object Name, Length | Format-Table -AutoSize
