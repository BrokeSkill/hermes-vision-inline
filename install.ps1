# Installs hermes-vision-inline on Windows.
# Copies the plugin into %USERPROFILE%\.hermes\plugins\hermes-vision-inline and enables it.
$ErrorActionPreference = "Stop"

$src = Split-Path -Parent $MyInvocation.MyCommand.Path
$hermesHome = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { Join-Path $env:USERPROFILE ".hermes" }
$dest = Join-Path $hermesHome "plugins\hermes-vision-inline"

New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item (Join-Path $src "__init__.py") $dest -Force
Copy-Item (Join-Path $src "plugin.yaml") $dest -Force
if (Test-Path (Join-Path $src "README.md")) { Copy-Item (Join-Path $src "README.md") $dest -Force }
if (Test-Path (Join-Path $src "docs")) { Copy-Item (Join-Path $src "docs") $dest -Recurse -Force }
Write-Host "copied plugin to $dest"

if (-not (Get-Command hermes -ErrorAction SilentlyContinue)) {
    Write-Host "hermes is not on PATH. Run this once it is:"
    Write-Host "  hermes plugins enable hermes-vision-inline"
    return
}

& hermes plugins enable hermes-vision-inline
if ($LASTEXITCODE -eq 0) {
    Write-Host "enabled. Restart the desktop backend to pick it up."
} else {
    Write-Host "enable failed. Add 'hermes-vision-inline' to plugins.enabled in config.yaml by hand."
}
