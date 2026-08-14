[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$skillSource = (Resolve-Path (Join-Path $repoRoot "skills")).Path
$agentsRoot = Join-Path $repoRoot ".agents"
$skillLink = Join-Path $agentsRoot "skills"

New-Item -ItemType Directory -Force -Path $agentsRoot | Out-Null

if (Test-Path -LiteralPath $skillLink) {
    $existing = Get-Item -LiteralPath $skillLink -Force
    $resolvedTarget = @($existing.Target) | Select-Object -First 1
    if (-not $resolvedTarget -or [IO.Path]::GetFullPath($resolvedTarget) -ne $skillSource) {
        throw "Existing .agents/skills points to '$resolvedTarget', expected '$skillSource'. Remove or rename it explicitly, then rerun this script."
    }
    Write-Host "Codex skill link already configured: $skillLink"
} else {
    New-Item -ItemType Junction -Path $skillLink -Target $skillSource | Out-Null
    Write-Host "Created Codex skill link: $skillLink -> $skillSource"
}

if (Get-Command codex -ErrorAction SilentlyContinue) {
    Write-Host "Codex detected: $(codex --version)"
} else {
    Write-Warning "Codex CLI is not on PATH. The repository setup is complete, but install/start Codex before invoking skills."
}

Write-Host "Open Codex in '$repoRoot' (or a workspaces subdirectory) and invoke `$1start-mathmodel."
