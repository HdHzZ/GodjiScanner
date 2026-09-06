$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -m PyInstaller --noconfirm --clean --onefile --console --name GodjiScanner scanner.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
Write-Output 'Built: dist\GodjiScanner.exe'
