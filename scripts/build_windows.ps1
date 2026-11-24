$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

uv sync --extra win --extra hotkey --extra bundle --extra dev
uv run pyinstaller packaging/stt.spec --noconfirm

Write-Host 'Built dist\stt.exe'
Write-Host 'Install at least one OCR language pack, then launch stt.exe.'
