param(
    [string]$Name = "Jigration"
)

$ErrorActionPreference = "Stop"
$IconPath = "src\db_migrator\gui\assets\app-icon.ico"
$AssetsPath = "src\db_migrator\gui\assets;db_migrator\gui\assets"

uv run pyinstaller `
    --noconfirm `
    --clean `
    --onedir `
    --name $Name `
    --icon $IconPath `
    --add-data $AssetsPath `
    --collect-all PySide6 `
    --hidden-import db_migrator.gui.main `
    src\db_migrator\gui\main.py

Write-Host "Build complete: dist\$Name"
