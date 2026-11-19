# Construye el ejecutable (PyInstaller) y el instalador (Inno Setup) del Editor 360.
# Lo usa GitHub Actions (.github/workflows/instalador.yml), pero también funciona en local:
#   pip install -r requirements.txt onnxruntime pyinstaller
#   powershell -ExecutionPolicy Bypass -File installer\build.ps1
# Resultado: dist\Instalar-Editor-360-<versión>.exe

param([string]$Version)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)      # raíz del repositorio

if (-not $Version) { $Version = (python -c "import paths; print(paths.VERSION)").Trim() }
Write-Host "Editor 360 $Version"

# La página lleva su versión (meta editor-version) para detectar un servidor antiguo en marcha
$page = [regex]::Match((Get-Content index.html -Raw -Encoding UTF8), 'name="editor-version" content="([^"]+)"').Groups[1].Value
if ($page -ne $Version) { throw "index.html dice editor-version=$page pero la versión es $Version. Actualiza la etiqueta meta." }

# 1. Ejecutable: Python + editor + numpy, Pillow y onnxruntime (para el modo local)
python -m PyInstaller --noconfirm --clean --windowed `
    --name "Editor 360" `
    --icon installer\icon.ico `
    --add-data "index.html;." `
    --collect-all onnxruntime `
    --exclude-module tkinter `
    --distpath dist --workpath build `
    server.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller ha fallado" }

# 2. Instalador
$iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "No se encuentra Inno Setup 6 (ISCC.exe)" }
& $iscc "/DAppVersion=$Version" installer\editor360.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup ha fallado" }

Get-ChildItem dist\Instalar-*.exe | ForEach-Object { "{0}  ({1:N1} MB)" -f $_.Name, ($_.Length / 1MB) }
