# Jigration

The GUI is a Windows-first PySide6 desktop entry point over the same application service used by the CLI.

## Run locally

```powershell
uv sync --extra gui --extra test
uv run jigration-gui
```

The GUI keeps the existing CLI secret behavior. If `config.yml` contains `password` values, they are plaintext in that private local file. Use an ignored local config and avoid committing secrets.

## Build Windows onedir package

```powershell
uv sync --extra gui --extra test
.\scripts\build-gui.ps1
Compress-Archive -Path dist\Jigration -DestinationPath dist\Jigration.zip -Force
```

The first supported artifact is a folder-style zip. Single-file exe and installer packaging are intentionally deferred.
