[CmdletBinding()]
param(
    [switch]$Json
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path

function Get-CommandStatus {
    param(
        [Parameter(Mandatory)] [string]$Name,
        [string[]]$VersionArgs = @("--version")
    )

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $command) {
        return [pscustomobject]@{ Name = $Name; Available = $false; Path = $null; Version = $null }
    }

    $version = $null
    try {
        $version = (& $command.Source @VersionArgs 2>&1 | Select-Object -First 1).ToString().Trim()
    } catch {
        $version = "detected"
    }
    [pscustomobject]@{
        Name = $Name
        Available = $true
        Path = $command.Source
        Version = $version
    }
}

$tools = @(
    Get-CommandStatus "codex"
    Get-CommandStatus "python"
    Get-CommandStatus "uv"
    Get-CommandStatus "node"
    Get-CommandStatus "git"
    Get-CommandStatus "typst"
    Get-CommandStatus "xelatex" @("--version")
    Get-CommandStatus "matlab" @("-batch", "disp(version)")
    Get-CommandStatus "octave"
    Get-CommandStatus "pdftoppm" @("-v")
    Get-CommandStatus "drawio" @("--version")
)

$pythonPackages = @()
$pythonForPackages = Join-Path $repoRoot "backend\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonForPackages)) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    $pythonForPackages = if ($pythonCommand) { $pythonCommand.Source } else { $null }
}
if ($pythonForPackages) {
    $packageCode = @'
import importlib.util, json
packages = ["numpy", "scipy", "pandas", "matplotlib", "sklearn", "openpyxl"]
print(json.dumps({name: importlib.util.find_spec(name) is not None for name in packages}))
'@
    $packageResult = $packageCode | & $pythonForPackages -
    $packageMap = $packageResult | ConvertFrom-Json
    foreach ($name in "numpy", "scipy", "pandas", "matplotlib", "sklearn", "openpyxl") {
        $pythonPackages += [pscustomobject]@{
            Name = $name
            Available = [bool]$packageMap.$name
        }
    }
}

$planPath = Join-Path (Get-Location) "plan.md"
$selectedLanguage = "unknown"
if (Test-Path -LiteralPath $planPath) {
    $plan = Get-Content -Raw -Encoding UTF8 $planPath
    if ($plan -match "编程语言\s*[：:]\s*MATLAB") { $selectedLanguage = "MATLAB" }
    elseif ($plan -match "编程语言\s*[：:]\s*Python") { $selectedLanguage = "Python" }
}

$report = [pscustomobject]@{
    Repository = $repoRoot
    SelectedLanguage = $selectedLanguage
    Tools = $tools
    PythonPackageInterpreter = $pythonForPackages
    PythonPackages = $pythonPackages
    MatlabReady = [bool](($tools | Where-Object Name -eq "matlab").Available -or ($tools | Where-Object Name -eq "octave").Available)
    LatexReady = [bool](($tools | Where-Object Name -eq "xelatex").Available)
    TypstReady = [bool](($tools | Where-Object Name -eq "typst").Available)
}

if ($Json) {
    $report | ConvertTo-Json -Depth 5
    exit 0
}

Write-Host "MathModelAgent environment ($selectedLanguage)"
$tools | Format-Table Name, Available, Version, Path -AutoSize
if ($pythonPackages.Count -gt 0) {
    Write-Host "Python packages ($pythonForPackages)"
    $pythonPackages | Format-Table Name, Available -AutoSize
}
Write-Host "MATLAB path ready: $($report.MatlabReady)"
Write-Host "LaTeX path ready: $($report.LatexReady)"
Write-Host "Typst path ready: $($report.TypstReady)"
