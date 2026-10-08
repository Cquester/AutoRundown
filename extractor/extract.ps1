<#
  AutoRundown WIS extractor - ONE command to turn a Mercedes EWA/WIS VM into
  records.jsonl for AutoRundown.

  What it does, in order:
    1. Locate tools (VBoxManage, the bundled Java, tbjdbc.jar).
    2. Snapshot the VM (so nothing about the customer's disk is changed).
    3. Boot the VM if it isn't running.
    4. Switch its network to host-only so the host can reach the DB.
    5. Turn off the guest firewall (via guest-control, if you pass guest creds).
    6. Discover the DB and wait for it to answer - if the workshop app isn't
       up yet, it tells the user exactly what to click and waits.
    7. Pull every document body for the chosen language, then parse to records.jsonl.

  It is safe to re-run: the pull resumes from a watermark, the parse is idempotent.

  EXAMPLES
    # Fully automated (Guest Additions + guest login available):
    powershell -ExecutionPolicy Bypass -File extract.ps1 `
        -VM "Mercedes_Ultimate_207" -GuestUser "Admin" -GuestPassword "pw"

    # VM already booted and app already running (like a manual first run):
    powershell -ExecutionPolicy Bypass -File extract.ps1 -VM "Mercedes_Ultimate_207" -SkipVmSetup
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$VM,
    [string]$GuestUser,
    [string]$GuestPassword,
    [string]$OutDir,                           # default set below (see note)
    [int]   $Lang     = 2,                     # 2 = English
    [string]$Database = "wisnet",
    [int]   $Port     = 2054,                  # 2054 = WIS, 2034 = EPC
    [string]$NicName  = "VirtualBox Host-Only Ethernet Adapter",
    [string]$JavaExe,                          # auto-detected if omitted
    [string]$Jar,                              # tbjdbc.jar; auto-detected if omitted
    [string]$Subnet   = "192.168.56",          # host-only subnet to probe
    [int]   $WaitSeconds = 240,                # how long to wait for the DB
    [switch]$SkipVmSetup,                      # VM already up + app running
    [switch]$NoSnapshot,                       # skip the protective snapshot
    [switch]$ListLanguages                     # just print available languages, then exit
)

$ErrorActionPreference = "Stop"

# Windows PowerShell 5.1 leaves $PSScriptRoot empty while param() defaults are
# evaluated, so a default of (Split-Path $PSScriptRoot) failed before the
# script started. It is set by the time the body runs.
# Data (the EWA install, autorundown_out) lives outside the repo -- the same
# rule as wis_paths.py: AR_HOME, else this folder's parent when the extractor
# is not in a git repo, else the nearest "Mercedes Documents" beside the repo.
$extParent = Split-Path -Parent $PSScriptRoot
if ($env:AR_HOME) { $DataHome = $env:AR_HOME }
elseif (-not (Test-Path (Join-Path $extParent ".git"))) { $DataHome = $extParent }
else {
    $DataHome = $null; $p = Split-Path -Parent $extParent
    while ($p -and -not $DataHome) {
        if (Test-Path (Join-Path $p "Mercedes Documents")) { $DataHome = Join-Path $p "Mercedes Documents" }
        $p = Split-Path -Parent $p
    }
    if (-not $DataHome) { $DataHome = Join-Path (Split-Path -Parent $extParent) "Mercedes Documents" }
}
if (-not $OutDir) {
    $outRoot = if ($env:AR_OUT_ROOT) { $env:AR_OUT_ROOT } else { Join-Path $DataHome "autorundown_out" }
    $OutDir = Join-Path $outRoot "wis_extract"
}

function Step($m){ Write-Host "`n==== $m ====" -ForegroundColor Cyan }
function Info($m){ Write-Host "     $m" }
function Warn($m){ Write-Host "     ! $m" -ForegroundColor Yellow }
function Die ($m){ Write-Host "`nSTOP: $m" -ForegroundColor Red; exit 1 }

# ---- 1. locate tools -------------------------------------------------------
Step "Locating tools"
$vbox = (Get-Command VBoxManage -ErrorAction SilentlyContinue).Source
if (-not $vbox) { $vbox = "C:\Program Files\Oracle\VirtualBox\VBoxManage.exe" }
if (-not (Test-Path $vbox)) { Die "VBoxManage not found. Install VirtualBox or add it to PATH." }
Info "VBoxManage: $vbox"

if (-not $JavaExe) {
    $cand = Get-ChildItem -Path $DataHome -Recurse -Filter "java.exe" `
            -ErrorAction SilentlyContinue | Where-Object { $_.FullName -like "*sun_jre*bin*" } | Select-Object -First 1
    if ($cand) { $JavaExe = $cand.FullName }
}
if (-not $JavaExe -or -not (Test-Path $JavaExe)) {
    $JavaExe = (Get-Command java -ErrorAction SilentlyContinue).Source
}
if (-not $JavaExe) { Die "Java not found. Pass -JavaExe <path to the bundled sun_jre java.exe>." }
Info "Java: $JavaExe"

if (-not $Jar) {
    $cand = Get-ChildItem -Path $DataHome -Recurse -Filter "tbjdbc.jar" `
            -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cand) { $Jar = $cand.FullName }
}
if (-not $Jar -or -not (Test-Path $Jar)) { Die "tbjdbc.jar not found. Pass -Jar <path>." }
Info "JDBC jar: $Jar"

$py = (Get-Command py -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $py) { Die "Python 3 not found (need 'py' or 'python' on PATH)." }
Info "Python: $py"

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Info "Output: $OutDir"

# ---- helpers ---------------------------------------------------------------
function Test-Port([string]$ip,[int]$p,[int]$timeoutMs=400){
    $c = New-Object System.Net.Sockets.TcpClient
    try { $iar=$c.BeginConnect($ip,$p,$null,$null)
          if ($iar.AsyncWaitHandle.WaitOne($timeoutMs) -and $c.Connected){ $c.EndConnect($iar); return $true }
          return $false }
    catch { return $false } finally { $c.Close() }
}
function Guest-Run([string]$exe,[string[]]$gargs){
    if (-not $GuestUser) { return $false }
    $a = @("guestcontrol",$VM,"run","--username",$GuestUser)
    if ($GuestPassword) { $a += @("--password",$GuestPassword) }
    $a += @("--exe",$exe,"--") + $gargs
    try { & $vbox @a *>$null; return ($LASTEXITCODE -eq 0) } catch { return $false }
}
function Find-Db {
    # (a) ask Guest Additions for the guest IP(s), test the DB port on each
    foreach ($n in 0..3){
        try { $ip = (& $vbox guestproperty get $VM "/VirtualBox/GuestInfo/Net/$n/V4/IP") -replace '^Value:\s*','' }
        catch { $ip = $null }
        if ($ip -and $ip -match '^\d+\.\d+\.\d+\.\d+$' -and (Test-Port $ip $Port)) { return $ip }
    }
    # (b) probe the host-only subnet (DHCP usually hands out .100+)
    foreach ($h in (100..110) + (2..99)){
        $ip = "$Subnet.$h"
        if (Test-Port $ip $Port 250) { return $ip }
    }
    return $null
}

# ---- 2-5. VM setup ---------------------------------------------------------
if (-not $SkipVmSetup) {
    $exists = (& $vbox list vms) -match [regex]::Escape("`"$VM`"")
    if (-not $exists) { Die "VM '$VM' not found. `"$vbox list vms`" shows the available names." }

    if (-not $NoSnapshot) {
        Step "Snapshotting VM (protective)"
        $snap = "AutoRundown-{0}" -f (Get-Date -Format "yyyyMMdd-HHmmss")
        try { & $vbox snapshot $VM take $snap --description "AutoRundown extract" *>$null; Info "snapshot: $snap" }
        catch { Warn "snapshot failed (continuing): $_" }
    }

    Step "Ensuring VM is running"
    $running = (& $vbox list runningvms) -match [regex]::Escape("`"$VM`"")
    if ($running) { Info "already running" }
    # headless: --type gui hangs when there is no desktop session and leaves
    # the VM locked. Use "Show" in VirtualBox Manager if you need the screen.
    else { & $vbox startvm $VM --type headless *>$null; Info "started headless; giving the guest time to boot..."; Start-Sleep -Seconds 45 }

    Step "Switching network to host-only"
    try { & $vbox controlvm $VM nic1 hostonly $NicName *>$null; Info "nic1 -> host-only ($NicName)" }
    catch { Warn "could not switch nic1 (may already be host-only): $_" }

    Step "Turning off the guest firewall"
    if (Guest-Run "C:\Windows\System32\netsh.exe" @("advfirewall","set","allprofiles","state","off")) {
        Info "guest firewall disabled via guest-control"
    } else {
        Warn "could not disable the guest firewall automatically."
        Warn "If the connection times out below, turn Windows Firewall OFF inside the VM."
    }
} else {
    Info "SkipVmSetup: assuming the VM is up, on host-only, firewall off, app running."
}

# ---- 6. wait for the database ---------------------------------------------
Step "Finding the workshop database (port $Port)"
$deadline = (Get-Date).AddSeconds($WaitSeconds)
$ip = $null; $prompted = $false
while (-not $ip -and (Get-Date) -lt $deadline) {
    $ip = Find-Db
    if ($ip) { break }
    if (-not $prompted) {
        Warn "The database isn't answering yet. Inside the VM, make sure you have:"
        Warn "  1) logged in to Windows,"
        Warn "  2) launched 'Mercedes WSM EPC' from the desktop (starts the DB),"
        Warn "  3) turned Windows Firewall OFF."
        Read-Host "Press Enter once that's done (or wait - I'll keep checking)" | Out-Null
        $prompted = $true
    }
    Start-Sleep -Seconds 5
}
if (-not $ip) { Die "Database not reachable on $Subnet.x:$Port within $WaitSeconds s. Re-run once the app is up." }
Info "Database is up at $ip`:$Port ($Database)"

$env:AR_HOST=$ip; $env:AR_PORT="$Port"; $env:AR_DB=$Database; $env:AR_OUT=$OutDir; $env:AR_LANG="$Lang"; $env:AR_JAR=$Jar

# Stage jar + scripts into a space-free temp dir (Nashorn is fussy about spaced paths).
$stage = Join-Path $env:TEMP "ar_extract_stage"
New-Item -ItemType Directory -Force -Path $stage | Out-Null
Copy-Item $Jar (Join-Path $stage "tbjdbc.jar") -Force
Copy-Item (Join-Path $PSScriptRoot "pull.js")        $stage -Force
Copy-Item (Join-Path $PSScriptRoot "pull_applic.js") $stage -Force
Copy-Item (Join-Path $PSScriptRoot "langs.js")       $stage -Force

# ---- list languages and exit (if asked) -----------------------------------
if ($ListLanguages) {
    Step "Languages available in this database (SprachCode | lang | docs)"
    Push-Location $stage
    try { & $JavaExe -cp "tbjdbc.jar" jdk.nashorn.tools.Shell "langs.js" }
    finally { Pop-Location }
    Write-Host "`nRe-run with -Lang <SprachCode> to extract one of these." -ForegroundColor Green
    exit 0
}

# ---- 7. pull + parse -------------------------------------------------------
Step "Pulling document bodies (this is the long step)"
Push-Location $stage
try { & $JavaExe -cp "tbjdbc.jar" jdk.nashorn.tools.Shell "pull.js" }
finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { Die "pull stage failed (exit $LASTEXITCODE). Re-run to resume from the last checkpoint." }

Step "Pulling model/model-year applicability (W_Baumusterzeile + W_Dok2Reihe)"
Push-Location $stage
try { & $JavaExe -cp "tbjdbc.jar" jdk.nashorn.tools.Shell "pull_applic.js" }
finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { Warn "applicability pull failed (exit $LASTEXITCODE); records will lack model fields. You can re-run to add them." }

Step "Parsing to records.jsonl"
& $py (Join-Path $PSScriptRoot "parse.py")
if ($LASTEXITCODE -ne 0) { Die "parse stage failed (exit $LASTEXITCODE)." }

# ---- 8. report -------------------------------------------------------------
$records = Join-Path $OutDir "records.jsonl"
$count = 0
if (Test-Path $records) { $count = [System.Linq.Enumerable]::Count([System.IO.File]::ReadLines($records)) }
Step "DONE"
Write-Host "  records.jsonl : $records" -ForegroundColor Green
Write-Host "  records       : $count" -ForegroundColor Green
Write-Host "  Hand this file to AutoRundown. It needs nothing else - no VM, no DB." -ForegroundColor Green
