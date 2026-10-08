# Build the AutoRundown Windows installer (per-user MSI, no admin rights).
#
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
#
# Needs: Windows, the Python version to bundle on PATH as `python` (its
# version decides which embeddable Python is downloaded, so the packages pip
# installs match it), and the WiX tool: `dotnet tool install --global wix
# --version 5.0.2`. Output: dist\AutoRundown-<version>.msi.
#
# The bundle is the official embeddable python.exe plus the app's source, not
# a frozen exe: import jobs run ingest.py / rebuild_wis.py as child processes
# with sys.executable, which must stay a real Python.
param(
    [string]$Out = "dist"
)
$ErrorActionPreference = "Stop"
$root  = (Resolve-Path "$PSScriptRoot\..\..").Path
$stage = Join-Path $root "build\windows\stage"
$py    = Join-Path $stage "python"
$app   = Join-Path $stage "app"

$pyver = (& python -c "import platform; print(platform.python_version())").Trim()
$tag   = (& python -c "import sys; print(f'{sys.version_info[0]}{sys.version_info[1]}')").Trim()
$arch  = (& python -c "import struct; print(struct.calcsize('P') * 8)").Trim()
if ($arch -ne "64") { throw "need a 64-bit Python on PATH, found $arch-bit" }
$version = (& python -c "import sys; sys.path.insert(0, r'$root'); import autorundown; print(autorundown.__version__)").Trim()
Write-Host "AutoRundown $version, bundling Python $pyver"

if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force $py, $app | Out-Null

# 1. embeddable Python, same version as the one pip runs under
$zip = Join-Path $root "build\windows\python-$pyver-embed-amd64.zip"
if (-not (Test-Path $zip)) {
    Invoke-WebRequest "https://www.python.org/ftp/python/$pyver/python-$pyver-embed-amd64.zip" -OutFile $zip
}
Expand-Archive $zip -DestinationPath $py

# The ._pth file replaces sys.path entirely and stops Python adding a script's
# own folder. ..\app is what lets `python ingest.py` import autorundown, and
# `python -m autorundown.launcher` find it.
@(
    "python$tag.zip",
    ".",
    "..\app",
    "Lib\site-packages",
    "import site"
) | Set-Content -Encoding ascii (Join-Path $py "python$tag._pth")

# 2. dependencies, wheels only (no compiler on a user's PC or here). The
# extractor's (Pillow, ezdxf) too: the import dialog runs the user's WIS
# extractor with this Python, and its build step converts images.
& python -m pip install --disable-pip-version-check --only-binary=:all: `
    --target (Join-Path $py "Lib\site-packages") -r (Join-Path $root "requirements.txt") `
    -r (Join-Path $root "requirements-extractor.txt")
if ($LASTEXITCODE) { throw "pip install failed" }

# 3. the app: the package plus the command-line tools a user may run
Copy-Item -Recurse (Join-Path $root "autorundown") $app
foreach ($f in "serve.py", "ingest.py", "ask.py", "rebuild_wis.py", "LICENSE", "README.md") {
    Copy-Item (Join-Path $root $f) $app
}
Get-ChildItem $app -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
# Compile everything now so the bytecode is installed (and uninstalled) with
# the MSI. Otherwise the first run writes .pyc files the MSI does not own, and
# uninstall leaves them behind. unchecked-hash: Python never compares them to
# the source's timestamp (which the install may change) or rewrites them.
& (Join-Path $py "python.exe") -m compileall -q --invalidation-mode unchecked-hash `
    $app (Join-Path $py "Lib\site-packages")
if ($LASTEXITCODE) { throw "compileall failed" }

# 4. smoke test the stage before packaging it
& (Join-Path $py "python.exe") -c "import sqlite3; c = sqlite3.connect(':memory:'); c.execute('CREATE VIRTUAL TABLE t USING fts5(x)'); import autorundown.server, autorundown.launcher, lxml.html, anthropic, PIL.Image, ezdxf.recover, ezdxf.addons.drawing.svg; print('stage ok, sqlite', sqlite3.sqlite_version)"
if ($LASTEXITCODE) { throw "stage smoke test failed" }

# 5. MSI
New-Item -ItemType Directory -Force (Join-Path $root $Out) | Out-Null
$msi = Join-Path $root "$Out\AutoRundown-$version.msi"
& wix build (Join-Path $PSScriptRoot "AutoRundown.wxs") -arch x64 `
    -d "Version=$version" -d "StageDir=$stage" -o $msi
if ($LASTEXITCODE) { throw "wix build failed" }
Write-Host "built $msi"
