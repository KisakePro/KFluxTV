# Compile KFluxTV : exe principal, version portable et setup.
# Prérequis : pip install -r requirements.txt
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$ver = (Select-String -Path iptv.py -Pattern '^VERSION = "(.+)"').Matches[0].Groups[1].Value
Write-Host "Version $ver"
Set-Content -Path version.txt -Value $ver -NoNewline -Encoding ascii

Get-Process KFluxTV -ErrorAction SilentlyContinue | Stop-Process -Force

# 1) application
python -m PyInstaller --noconfirm --onefile --windowed --icon icon.ico --name KFluxTV `
  --collect-all pychromecast --collect-all zeroconf --collect-all casttube --collect-all imageio_ffmpeg `
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets `
  --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtQuick --exclude-module PySide6.QtQml `
  --exclude-module PySide6.QtPdf --exclude-module PySide6.QtDesigner --exclude-module PySide6.QtCharts `
  iptv.py
if ($LASTEXITCODE) { throw "Échec build application" }

New-Item -ItemType Directory -Force release | Out-Null
Copy-Item dist\KFluxTV.exe "release\KFluxTV-Portable-$ver.exe" -Force

# 2) setup (embarque l'exe de l'étape 1)
python -m PyInstaller --noconfirm --onefile --windowed --icon icon.ico --name "KFluxTV-Setup-$ver" `
  --add-data "dist\KFluxTV.exe;." --add-data "icon.ico;." --add-data "version.txt;." installer.py
if ($LASTEXITCODE) { throw "Échec build setup" }
Copy-Item "dist\KFluxTV-Setup-$ver.exe" release -Force

Get-ChildItem release | Select-Object Name, @{n='Mo';e={[math]::Round($_.Length/1MB)}}
