[CmdletBinding()]
param([switch]$Docker, [switch]$Browser)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

function Invoke-Checked {
    param([string]$Command, [string[]]$Arguments)
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Command failed with exit code $LASTEXITCODE"
    }
}

Push-Location $repoRoot
try {
    Invoke-Checked uv @('run', '--frozen', 'python', '-m', 'unittest', 'discover', '-s', 'tests', '-v')
    Invoke-Checked node @('--check', 'html/app.js')
    if ($Docker) {
        foreach ($mode in @('dev', 'prod')) {
            Invoke-Checked docker @('compose', '-f', 'compose.yaml', '-f', "compose.$mode.yaml", 'config', '--quiet')
        }
        Invoke-Checked docker @('compose', '-f', 'compose.yaml', '-f', 'compose.dev.yaml', 'build', 'app')
        $testsPath = Join-Path $repoRoot 'tests'
        Invoke-Checked docker @('compose', '-f', 'compose.yaml', '-f', 'compose.dev.yaml', 'run', '--rm', '--no-deps', '--volume', "${testsPath}:/app/tests:ro", 'app', 'python', '-m', 'unittest', 'discover', '-s', 'tests', '-v')
        $caddyPath = Join-Path $repoRoot 'deploy/caddy'
        foreach ($mode in @('dev', 'prod')) {
            Invoke-Checked docker @('run', '--rm', '--volume', "${caddyPath}:/etc/caddy:ro", 'caddy:2-alpine', 'caddy', 'validate', '--config', "/etc/caddy/Caddyfile.$mode", '--adapter', 'caddyfile')
        }
    }
    if ($Browser) {
        Invoke-Checked uv @('run', '--with', 'playwright', 'python', 'scripts/check_browser.py')
    }
} finally {
    Pop-Location
}
