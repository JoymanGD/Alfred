$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $projectRoot '.local_services\searxng-venv\Scripts\python.exe'
$settings = Join-Path $projectRoot '.local_services\searxng-settings.yml'
$logDir = Join-Path $projectRoot '.local_services\logs'

if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $settings)) {
    Write-Output '[WEB] Local SearXNG installation is missing; Alfred will use direct search.'
    exit 0
}

function Test-SearchReady {
    try {
        $response = Invoke-WebRequest -Uri 'http://127.0.0.1:8080/' -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

if (Test-SearchReady) {
    Write-Output '[WEB] SearXNG is ready.'
    exit 0
}

New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$env:SEARXNG_SETTINGS_PATH = $settings
Start-Process -FilePath $python -ArgumentList '-m', 'searx.webapp' `
    -WorkingDirectory $projectRoot -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logDir 'searxng.out.log') `
    -RedirectStandardError (Join-Path $logDir 'searxng.err.log') | Out-Null

for ($attempt = 0; $attempt -lt 24; $attempt++) {
    if (Test-SearchReady) {
        Write-Output '[WEB] SearXNG is ready.'
        exit 0
    }
    Start-Sleep -Milliseconds 500
}

Write-Output '[WEB] SearXNG did not become ready; Alfred will use direct search.'
exit 0
