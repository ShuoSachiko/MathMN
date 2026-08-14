[CmdletBinding()]
param(
    [switch]$CodexOnly,
    [switch]$SkipRedis
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runtimeRoot = Join-Path $repoRoot ".runtime"

& (Join-Path $PSScriptRoot "setup-codex.ps1")
New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null

if ($CodexOnly) {
    Write-Host "Codex-first setup complete. WebUI dependencies were skipped."
    exit 0
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required for the backend. Install it from https://docs.astral.sh/uv/."
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "Node.js/npm is required for the frontend."
}

$backendEnv = Join-Path $repoRoot "backend\.env.dev"
if (-not (Test-Path -LiteralPath $backendEnv)) {
    Copy-Item -LiteralPath (Join-Path $repoRoot "backend\.env.example") -Destination $backendEnv
    Write-Host "Created backend/.env.dev from the safe example. Add model API keys in the UI or this ignored file."
}

Write-Host "Installing backend dependencies into backend/.venv"
& uv sync --project (Join-Path $repoRoot "backend")
if ($LASTEXITCODE -ne 0) { throw "uv sync failed with exit code $LASTEXITCODE" }

$pnpmRoot = Join-Path $runtimeRoot "pnpm"
Write-Host "Installing pnpm 10.6.3 inside .runtime/pnpm"
& npm install --prefix $pnpmRoot --no-save --no-audit --no-fund pnpm@10.6.3
if ($LASTEXITCODE -ne 0) { throw "local pnpm installation failed" }

$pnpm = Join-Path $pnpmRoot "node_modules\.bin\pnpm.cmd"
Write-Host "Installing frontend dependencies into frontend/node_modules"
& $pnpm --dir (Join-Path $repoRoot "frontend") install --frozen-lockfile
if ($LASTEXITCODE -ne 0) { throw "pnpm install failed with exit code $LASTEXITCODE" }

if (-not $SkipRedis) {
    $redisRoot = Join-Path $runtimeRoot "redis"
    $redisExecutable = Join-Path $redisRoot "redis-server.exe"
    if (-not (Test-Path -LiteralPath $redisExecutable)) {
        $redisVersion = "5.0.14.1"
        $expectedRedisSha256 = "018EA18A35876383CBB5F4CD0258ADFC87747CF9D619BCE1CF73A2E36F720CCF"
        $redisArchive = Join-Path $runtimeRoot "Redis-x64-$redisVersion.zip"
        $redisUrl = "https://github.com/tporadowski/redis/releases/download/v$redisVersion/Redis-x64-$redisVersion.zip"
        Write-Host "Downloading pinned Redis for Windows $redisVersion into .runtime"
        Invoke-WebRequest -UseBasicParsing -Uri $redisUrl -OutFile $redisArchive
        $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $redisArchive).Hash
        if ($hash -ne $expectedRedisSha256) {
            throw "Redis archive checksum mismatch. Expected $expectedRedisSha256, received $hash."
        }
        New-Item -ItemType Directory -Force -Path $redisRoot | Out-Null
        Expand-Archive -LiteralPath $redisArchive -DestinationPath $redisRoot -Force
        Set-Content -Encoding ASCII -LiteralPath (Join-Path $runtimeRoot "redis.sha256") -Value "$hash  Redis-x64-$redisVersion.zip"
    }
    if (-not (Test-Path -LiteralPath $redisExecutable)) {
        throw "Redis archive was downloaded but redis-server.exe was not found."
    }
}

Write-Host "Local WebUI setup complete. Run scripts/start-local.ps1."
