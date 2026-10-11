# A fresh AutoRundown in a Windows Sandbox: installed from an MSI on a clean
# Windows, with none of this PC's files, settings, index or API key, and
# reachable from this PC's browser at http://localhost:18756 (admin view).
#
#   powershell -ExecutionPolicy Bypass -File tools\sandbox\start.ps1
#
# Which build goes in (first match wins):
#   -Msi <path>        that installer
#   -Run <id>          the MSI a CI run built (gh run download; see `gh run list`)
#   -Release <tag>     a GitHub release's MSI ("latest" for the newest)
#   (nothing)          the newest dist\AutoRundown-*.msi, else the latest release
# -Source              install that MSI, then lay THIS checkout's code over it
#                      (uncommitted changes included) -- to try a branch
#                      without building an installer. Not covered: a changed
#                      requirements.txt (the MSI's packages are used).
# -Manuals <folder>    also show a folder of real manuals, read-only, at
#                      C:\ar\manuals inside (import it from Shop setup)
# -Port <n>            this PC's port (default 18756; the installed app keeps 8756)
#
# Needs the Windows Sandbox feature (Windows 11 Pro), turned on once from an
# elevated PowerShell, then a restart:
#   Enable-WindowsOptionalFeature -Online -FeatureName Containers-DisposableClientVM -All
#
# What the sandbox can see of this PC: .sandbox\in (read-only: the MSI, the
# scripts, sample manuals), -Manuals if given (read-only), and .sandbox\out,
# the one folder it writes to (its log and its address). Close the Sandbox
# window and everything inside is gone. Inside it is a separate Windows: no
# API key unless you type one into its Shop setup, and then it is spent for
# real.
param(
    [string]$Msi,
    [string]$Run,
    [string]$Release,
    [switch]$Source,
    [string]$Manuals,
    [int]$Port = 18756,
    [int]$MemoryMB = 4096,
    [int]$TimeoutMin = 15
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path "$PSScriptRoot\..\..").Path
$work = Join-Path $root ".sandbox"
$in   = Join-Path $work "in"
$out  = Join-Path $work "out"

$exe = Join-Path $env:WINDIR "System32\WindowsSandbox.exe"
if (-not (Test-Path $exe)) {
    Write-Host "Windows Sandbox is not turned on. From an elevated PowerShell, run:"
    Write-Host "  Enable-WindowsOptionalFeature -Online -FeatureName Containers-DisposableClientVM -All"
    Write-Host "then restart Windows and run this again."
    exit 2
}
if (Get-Process WindowsSandbox, WindowsSandboxClient, WindowsSandboxRemoteSession -ErrorAction SilentlyContinue) {
    Write-Host "A Windows Sandbox is already open; only one can run at a time. Close it first."
    exit 2
}
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "Port $Port is in use on this PC; pick another with -Port."
    exit 2
}

# a clean exchange folder every time
if (Test-Path $work) { Remove-Item -Recurse -Force $work }
New-Item -ItemType Directory -Force $in, $out | Out-Null

# -- the installer --------------------------------------------------------
if ($Msi) {
    Copy-Item (Resolve-Path $Msi) $in
} elseif ($Run) {
    gh run download $Run --name AutoRundown-msi --dir $in
    if ($LASTEXITCODE) { throw "could not download the MSI of run $Run" }
} else {
    $local = Get-ChildItem (Join-Path $root "dist\AutoRundown-*.msi") -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($local -and -not $Release) {
        Copy-Item $local.FullName $in
    } else {
        $tag = if ($Release -and $Release -ne "latest") { @($Release) } else { @() }
        gh release download @tag --pattern "AutoRundown-*.msi" --dir $in
        if ($LASTEXITCODE) { throw "could not download a release MSI (is gh signed in?)" }
    }
}
$msiFile = Get-ChildItem "$in\*.msi" | Select-Object -First 1
if (-not $msiFile) { throw "no MSI found" }
Write-Host "installer: $($msiFile.Name)"

# -- this checkout's code (-Source) --------------------------------------
# the same files build.ps1 puts in the MSI's app folder
$mode = "msi"
if ($Source) {
    $mode = "source"
    $src = New-Item -ItemType Directory -Force (Join-Path $in "src")
    Copy-Item -Recurse (Join-Path $root "autorundown") $src
    foreach ($f in "serve.py", "ingest.py", "ask.py", "rebuild_wis.py") {
        Copy-Item (Join-Path $root $f) $src
    }
    Get-ChildItem $src -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
    $rev = (git -C $root rev-parse --short HEAD) + $(if (git -C $root status --porcelain) { " + uncommitted" })
    Write-Host "source: this checkout ($rev)"
}

# -- sample manuals (invented text) to import from Shop setup --------------
$samples = New-Item -ItemType Directory -Force (Join-Path $in "samples")
if (-not (Test-Path (Join-Path $root "sample_corpus\manifest.json"))) {
    python (Join-Path $root "make_sample_corpus.py") | Out-Null
    if ($LASTEXITCODE) { throw "make_sample_corpus.py failed" }
}
Copy-Item -Recurse (Join-Path $root "sample_corpus") (Join-Path $samples "Mercedes WIS sample")
python (Join-Path $root "make_sample_lemon.py") --out (Join-Path $samples "LEMON sample") | Out-Null
if ($LASTEXITCODE) { throw "make_sample_lemon.py failed" }

Copy-Item (Join-Path $PSScriptRoot "inside.ps1"), (Join-Path $PSScriptRoot "forward.py") $in
@{ mode = $mode; port = $Port } | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $in "sandbox.json")

# -- the sandbox's configuration -----------------------------------------
function Map($hostDir, $inside, $ro) {
    $h = [Security.SecurityElement]::Escape($hostDir)
    "    <MappedFolder><HostFolder>$h</HostFolder><SandboxFolder>$inside</SandboxFolder><ReadOnly>$ro</ReadOnly></MappedFolder>"
}
$maps = @((Map $in "C:\ar\in" "true"), (Map $out "C:\ar\out" "false"))
if ($Manuals) { $maps += Map (Resolve-Path $Manuals).Path "C:\ar\manuals" "true" }
$wsb = Join-Path $work "AutoRundown.wsb"
@"
<Configuration>
  <Networking>Enable</Networking>
  <MemoryInMB>$MemoryMB</MemoryInMB>
  <ClipboardRedirection>Enable</ClipboardRedirection>
  <PrinterRedirection>Disable</PrinterRedirection>
  <AudioInput>Disable</AudioInput>
  <VideoInput>Disable</VideoInput>
  <ProtectedClient>Disable</ProtectedClient>
  <MappedFolders>
$($maps -join "`n")
  </MappedFolders>
  <LogonCommand>
    <Command>powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\ar\in\inside.ps1</Command>
  </LogonCommand>
</Configuration>
"@ | Set-Content -Encoding utf8 $wsb

Write-Host "starting Windows Sandbox (installing inside takes a minute or two)..."
Start-Process $exe -ArgumentList "`"$wsb`""

# -- wait for it, then forward localhost:<Port> into it ---------------------
$ready = Join-Path $out "ready.json"
$failed = Join-Path $out "failed.json"
$deadline = (Get-Date).AddMinutes($TimeoutMin)
while (-not (Test-Path $ready)) {
    if (Test-Path $failed) {
        Get-Content (Join-Path $out "inside.log") -Tail 30 -ErrorAction SilentlyContinue
        throw "setup inside the sandbox failed: $((Get-Content $failed -Raw | ConvertFrom-Json).error)"
    }
    if ((Get-Date) -gt $deadline) { throw "the sandbox did not report ready in $TimeoutMin min (see $out\inside.log)" }
    Start-Sleep -Seconds 2
}
$r = Get-Content $ready -Raw | ConvertFrom-Json
Write-Host ""
Write-Host "AutoRundown sandbox is up ($($r.msi), $($r.mode))"
Write-Host "  admin (Shop setup):  http://localhost:$Port"
Write-Host "  mechanic/phone view: http://$($r.ip):$($r.app_port)"
Write-Host "  sample manuals:      Desktop\Sample manuals inside the sandbox"
Write-Host "Ctrl+C stops the forwarding; closing the Sandbox window discards it all."
$fwd = Start-Process python -NoNewWindow -PassThru -ArgumentList `
    "`"$(Join-Path $PSScriptRoot 'forward.py')`" --listen 127.0.0.1:$Port --to $($r.ip):$($r.port)"
Start-Sleep -Seconds 1
# prove it end to end: the PC's browser must reach it, and as admin
try {
    $me = Invoke-RestMethod "http://localhost:$Port/api/me" -TimeoutSec 15
    if (-not $me.admin) { Write-Warning "reachable, but not as admin -- Shop setup will be refused" }
    else { Write-Host "checked: http://localhost:$Port answers as admin" }
} catch {
    Write-Warning "http://localhost:$Port does not answer ($($_.Exception.Message)); see $out\inside.log"
}
Start-Process "http://localhost:$Port"
try { Wait-Process -Id $fwd.Id } finally { Stop-Process -Id $fwd.Id -ErrorAction SilentlyContinue }
