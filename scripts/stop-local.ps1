[CmdletBinding()]
param()

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
& (Join-Path $repoRoot "backend\.venv\Scripts\python.exe") `
    (Join-Path $PSScriptRoot "service_manager.py") stop
exit $LASTEXITCODE
