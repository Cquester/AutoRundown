# Boot the extracted Transbase databases.
#
# MUST BE RUN ELEVATED (Run as Administrator).
#
# Why: Transbase creates its shared memory in the Windows "Global\" object
# namespace (sm_create -> os_get_mem), which requires SeCreateGlobalPrivilege.
# Measured on this host: Global\ file mapping -> ACCESS_DENIED (err 5),
# Local\ -> OK. Unelevated, every boot fails with:
#     boot database wisnet: sm_create()
#     os_get_mem(key=0x3)
#
# NOTE: tbadm32/tbi32 write their normal output to stderr. Under Windows
# PowerShell 5.1, "2>&1" on a native exe wraps each line in a NativeCommandError
# and trips $ErrorActionPreference. So every invocation here goes through
# Start-Process with redirection to files instead.
#
# Everything is written to boot_log.txt next to this script - just send me
# that file, no copy-paste needed.

$ErrorActionPreference = 'Continue'
$Root    = (Join-Path $env:USERPROFILE "Desktop\Mercedes Documents\ewa_extract")
$DbRoot  = "$Root\Program Files\EWA\database"
$LogFile = Join-Path $PSScriptRoot "boot_log.txt"
"" | Set-Content $LogFile -Encoding utf8

function Log {
    param([string]$Text)
    Write-Host $Text
    Add-Content -Path $LogFile -Value $Text -Encoding utf8
}

$elevated = ([Security.Principal.WindowsPrincipal] `
             [Security.Principal.WindowsIdentity]::GetCurrent()
            ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Log "elevated : $elevated"
Log "host     : $env:COMPUTERNAME"
Log "when     : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
if (-not $elevated) {
    Log "WARNING: not elevated - the boot step is expected to fail."
}

function Invoke-Tb {
    param([string]$ExeDir, [string]$Exe, [string]$Arguments, [int]$Seconds = 120)
    $tag = [Guid]::NewGuid().ToString('N').Substring(0, 8)
    $out = Join-Path $env:TEMP "tb_$tag.out"
    $err = Join-Path $env:TEMP "tb_$tag.err"
    $p = Start-Process -FilePath (Join-Path $ExeDir $Exe) `
                       -ArgumentList $Arguments -NoNewWindow -PassThru `
                       -WorkingDirectory $ExeDir `
                       -RedirectStandardOutput $out -RedirectStandardError $err
    if (-not $p.WaitForExit($Seconds * 1000)) {
        Stop-Process -Id $p.Id -Force
        Log "   [TIMEOUT after ${Seconds}s]"
    }
    foreach ($f in @($out, $err)) {
        if (Test-Path $f) {
            Get-Content $f | Where-Object {
                $_ -notmatch 'Patent|Copyright|Compiler :|Project  :|BuildDate|BuildID|^\s*$'
            } | ForEach-Object { Log "   $_" }
        }
    }
}

function Boot-Db {
    param([string]$EngineDir, [string]$DbName, [int]$Port)
    Log ""
    Log "==================================================================="
    Log "=== $DbName   engine=$(Split-Path $EngineDir -Leaf)   port=$Port"
    Log "==================================================================="
    $env:TRANSBASE       = $EngineDir
    $env:TRANSBASE_LOCAL = $EngineDir
    $env:PATH            = "$EngineDir;$env:PATH"

    Log "-- tbadm32 -i (inspect) --"
    Invoke-Tb $EngineDir "tbadm32.exe" "-i $DbName" 60

    Log "-- tbadm32 -b (boot) --"
    Invoke-Tb $EngineDir "tbadm32.exe" "-b $DbName" 180

    Log "-- starting tbserv32 listener --"
    Start-Process -FilePath (Join-Path $EngineDir "tbserv32.exe") `
                  -WorkingDirectory $EngineDir -WindowStyle Hidden
    Start-Sleep -Seconds 4
    $l = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($l) { Log "   OK - listening on $Port" } else { Log "   no listener on $Port" }
}

Boot-Db "$DbRoot\TransBase WIS" "wisnet"       2054
Boot-Db "$DbRoot\TransBase EPC" "SPECIALFILES" 2034

Log ""
Log "=== summary ==="
$procs = Get-Process -ErrorAction SilentlyContinue |
         Where-Object { $_.ProcessName -like 'tb*' }
if ($procs) {
    foreach ($p in $procs) { Log "   process $($p.ProcessName) pid=$($p.Id)" }
} else {
    Log "   no tb* processes running"
}
$ports = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
         Where-Object { $_.LocalPort -in 2034,2035,2044,2045,2054,2055 }
if ($ports) {
    foreach ($p in $ports) { Log "   listening $($p.LocalAddress):$($p.LocalPort)" }
} else {
    Log "   no Transbase ports listening"
}

Log ""
Log "Log written to: $LogFile"
Write-Host ""
Write-Host "DONE. Send me this file:" -ForegroundColor Green
Write-Host "   $LogFile" -ForegroundColor Green
