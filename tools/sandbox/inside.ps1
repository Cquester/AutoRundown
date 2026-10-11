# Runs INSIDE the Windows Sandbox, at sign-in (the .wsb's LogonCommand).
# start.ps1 on the PC wrote everything it needs into C:\ar\in (read-only):
#   sandbox.json   {mode, port}
#   *.msi          the installer to test
#   src\           (mode "source") this checkout's app files, laid over the install
#   samples\       sample manuals to import from the Shop setup page
# It reports back only through C:\ar\out: inside.log, then ready.json
# ({ip, port, ...}) or failed.json. Nothing else on the PC is visible here.
$ErrorActionPreference = "Stop"
$in  = "C:\ar\in"
$out = "C:\ar\out"
Start-Transcript -Path "$out\inside.log" -Force | Out-Null

function Fail($msg) {
    @{ error = "$msg" } | ConvertTo-Json | Set-Content -Encoding utf8 "$out\failed.json"
    Write-Host "FAILED: $msg"
    Stop-Transcript | Out-Null
    exit 1
}

try {
    $cfg  = Get-Content "$in\sandbox.json" -Raw | ConvertFrom-Json
    $port = [int]$cfg.port          # what the PC's browser connects through
    $app  = 8756                    # the app's own port, as on a real install
    $inst = "$env:LOCALAPPDATA\Programs\AutoRundown"

    # 1. install it the way a user does (per-user, no UAC), but don't add
    #    the Startup shortcut: the sandbox is thrown away on close anyway
    $msi = (Get-ChildItem "$in\*.msi" | Select-Object -First 1).FullName
    if (-not $msi) { Fail "no .msi in $in" }
    Write-Host "installing $msi"
    $p = Start-Process msiexec -Wait -PassThru -ArgumentList "/i `"$msi`" AUTOSTART=0 /qn /l*v `"$out\install.log`""
    if ($p.ExitCode) { Fail "msiexec exited $($p.ExitCode) (see install.log)" }

    # 2. mode "source": this checkout's code over the installed app. The MSI
    #    compiles its .py with unchecked-hash .pyc, which Python never compares
    #    with the source -- they must go, or the old code keeps running.
    if ($cfg.mode -eq "source") {
        Write-Host "laying this checkout's source over $inst\app"
        Copy-Item -Recurse -Force "$in\src\*" "$inst\app"
        Get-ChildItem "$inst\app" -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
    }

    # 3. sample manuals on the sandbox's desktop, for the import page
    if (Test-Path "$in\samples") {
        Copy-Item -Recurse "$in\samples" "$env:USERPROFILE\Desktop\Sample manuals"
    }

    # 4. first start, from the Start-menu shortcut like a new user
    #    (background mode, tray icon, opens Edge in here)
    Start-Process "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\AutoRundown.lnk"
    $ok = $false
    foreach ($i in 1..120) {
        try { Invoke-RestMethod "http://127.0.0.1:$app/api/stats" | Out-Null; $ok = $true; break }
        catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ok) {
        Get-Content "$env:USERPROFILE\.autorundown\autorundown.log" -ErrorAction SilentlyContinue
        Fail "AutoRundown did not start on port $app"
    }

    # 5. the sandbox's own address (its virtual network card, behind NAT)
    $ip = (Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
        Select-Object -First 1).IPAddress
    if (-not $ip) { Fail "the sandbox has no IPv4 address (is networking on?)" }

    # 6. <ip>:<port> -> 127.0.0.1:8756, so the PC's requests arrive from
    #    loopback here (admin), and let the PC in through the firewall
    New-NetFirewallRule -DisplayName "AutoRundown sandbox" -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort $port, $app | Out-Null
    Start-Process "$inst\python\pythonw.exe" -WindowStyle Hidden `
        -ArgumentList "`"$in\forward.py`" --listen ${ip}:$port --to 127.0.0.1:$app"
    Start-Sleep -Seconds 1

    @{ ip = $ip; port = $port; app_port = $app; mode = $cfg.mode
       msi = (Split-Path $msi -Leaf); started = (Get-Date).ToString("s") } |
        ConvertTo-Json | Set-Content -Encoding utf8 "$out\ready.json"
    Write-Host "ready: admin via the PC's http://localhost:$port, mechanic view http://${ip}:$app"
} catch {
    Fail $_
}
Stop-Transcript | Out-Null
