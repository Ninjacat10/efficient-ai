# Builds dist\EfficientAI\EfficientAI.exe and dist\EfficientAI-Setup-<version>.exe
# Needs: uv (https://docs.astral.sh/uv/) and Inno Setup 6 (winget install JRSoftware.InnoSetup)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

# A clean build environment, separate from your dev .venv
if (-not (Test-Path .build-venv\Scripts\python.exe)) { uv venv --python 3.12 .build-venv }
uv pip install --python .build-venv\Scripts\python.exe -e . pyinstaller

# Regenerate the icon from the logo code, then build the app folder
.build-venv\Scripts\python.exe -m efficient.brand
.build-venv\Scripts\python.exe -m PyInstaller packaging\efficient.spec --noconfirm --clean --distpath dist --workpath build

# Wrap it in an installer
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found. Install it with: winget install JRSoftware.InnoSetup" }
& $iscc packaging\installer.iss
Write-Host "`nDone: $(Get-ChildItem dist\EfficientAI-Setup-*.exe | Select-Object -Last 1 -ExpandProperty FullName)"
