$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    $env:UV_PROJECT_ENVIRONMENT = '.venv-win'
    if (-not (Test-Path '.\.venv-win\Scripts\pyinstaller.exe')) {
        uv sync --locked
    }
    .\.venv-win\Scripts\pyinstaller.exe --noconfirm --clean packaging\toolbox.spec
}
finally {
    Pop-Location
}
