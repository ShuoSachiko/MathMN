[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$ShareSession,
    [ValidateSet(
        "matlab-core",
        "matlab-data-import-and-analysis",
        "math-and-optimization",
        "parallel-computing",
        "image-processing-and-computer-vision",
        "robotics-and-autonomous-systems"
    )]
    [string[]]$SkillGroups = @(
        "matlab-core",
        "matlab-data-import-and-analysis",
        "math-and-optimization",
        "parallel-computing"
    )
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runtimeRoot = Join-Path $repoRoot ".runtime"
$toolkitRoot = Join-Path $runtimeRoot "matlab-agentic-toolkit"
$serverRoot = Join-Path $toolkitRoot "bin"
$server = Join-Path $serverRoot "matlab-mcp-server-windows-x64.exe"
$logRoot = Join-Path $runtimeRoot "matlab-mcp-logs"
$statusScript = Join-Path $repoRoot "skills\3coding-visual\scripts\matlab_agentic_status.py"
$configPath = Join-Path $repoRoot ".codex\config.toml"

function Find-MatlabExecutable {
    $command = Get-Command matlab -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    $candidates = Get-ChildItem "C:\Program Files\MATLAB" -Directory -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending |
        ForEach-Object { Join-Path $_.FullName "bin\matlab.exe" }
    return $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}

function Quote-TomlLiteral([string]$Value) {
    if ($Value.Contains("'")) {
        throw "A path containing a single quote cannot be written safely to config.toml: $Value"
    }
    return "'$Value'"
}

if (-not $Install) {
    python $statusScript
    $probeExit = $LASTEXITCODE
    if ($ShareSession) {
        Write-Host "Run shareMATLABSession() in an interactive MATLAB Command Window."
        Write-Host "This is optional; the MCP server can start its own MATLAB session."
    }
    if ($probeExit -ne 0) {
        Write-Warning "MATLAB native Agent integration is not ready. Rerun with -Install."
    }
    exit $probeExit
}

$matlab = Find-MatlabExecutable
if (-not $matlab) {
    throw "MATLAB R2021a or later was not found. Install MATLAB before configuring its MCP server."
}
$matlabRoot = Split-Path (Split-Path $matlab -Parent) -Parent

New-Item -ItemType Directory -Force -Path $runtimeRoot, $serverRoot, $logRoot | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $toolkitRoot ".git"))) {
    git clone --depth 1 https://github.com/matlab/matlab-agentic-toolkit.git $toolkitRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Could not clone the official MATLAB Agentic Toolkit."
    }
} else {
    Write-Host "Using existing toolkit clone: $toolkitRoot"
}

if (-not (Test-Path -LiteralPath $server)) {
    Invoke-WebRequest `
        -Uri "https://github.com/matlab/matlab-mcp-server/releases/latest/download/matlab-mcp-server-windows-x64.exe" `
        -OutFile $server
}
& $server --version
if ($LASTEXITCODE -ne 0) {
    throw "The downloaded MATLAB MCP Server did not pass its version check."
}

$configDirectory = Split-Path $configPath -Parent
New-Item -ItemType Directory -Force -Path $configDirectory | Out-Null
$toml = @(
    "# Generated locally by scripts/setup-matlab-agentic.ps1; do not commit this machine-specific file.",
    "[mcp_servers.matlab]",
    "command = $(Quote-TomlLiteral $server)",
    "args = [$(Quote-TomlLiteral "--matlab-root=$matlabRoot"), $(Quote-TomlLiteral "--initial-working-folder=$repoRoot"), '--matlab-display-mode=desktop', '--disable-telemetry=true', $(Quote-TomlLiteral "--log-folder=$logRoot")]",
    "cwd = $(Quote-TomlLiteral $repoRoot)",
    "env_vars = ['WINDIR']",
    "startup_timeout_sec = 60",
    "tool_timeout_sec = 7200",
    "enabled = true",
    "required = false",
    "default_tools_approval_mode = 'writes'"
) -join [Environment]::NewLine
[IO.File]::WriteAllText($configPath, $toml + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
Write-Host "Wrote project-scoped Codex MCP config: $configPath"

if (-not $env:USERPROFILE) {
    throw "USERPROFILE is unavailable; cannot register MATLAB skills for Codex."
}
$globalSkillRoot = Join-Path $env:USERPROFILE ".agents\skills"
New-Item -ItemType Directory -Force -Path $globalSkillRoot | Out-Null
foreach ($group in $SkillGroups) {
    $groupRoot = Join-Path $toolkitRoot "skills-catalog\$group"
    if (-not (Test-Path -LiteralPath $groupRoot)) {
        throw "Unknown or unavailable MATLAB skill group: $group"
    }
    foreach ($skill in Get-ChildItem $groupRoot -Directory) {
        $link = Join-Path $globalSkillRoot $skill.Name
        if (Test-Path -LiteralPath $link) {
            $existing = Get-Item -LiteralPath $link -Force
            $target = @($existing.Target) | Select-Object -First 1
            if (-not $target -or [IO.Path]::GetFullPath($target) -ne $skill.FullName) {
                throw "Skill path already exists and points elsewhere: $link"
            }
            continue
        }
        New-Item -ItemType Junction -Path $link -Target $skill.FullName | Out-Null
    }
    Write-Host "Registered MATLAB skill group: $group"
}

if ($ShareSession) {
    Write-Host "The existing-session MATLAB toolbox is optional and is not installed by this script."
    Write-Host "After installing it with the official installer, run shareMATLABSession() interactively."
}

Write-Host "Restart Codex in this trusted project, then use /mcp to verify the 'matlab' server."
Write-Host "Official sources: https://github.com/matlab/matlab-agentic-toolkit and https://github.com/matlab/matlab-mcp-server"
