$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$demoPath = Join-Path $projectRoot '.runtime\opentelemetry-demo'
$overlayPath = Join-Path $projectRoot 'infra\otel-demo\docker-compose.incidentpilot.yml'
$env:DEMO_VERSION = '2.2.0'

Push-Location $projectRoot
try {
    & docker compose --profile core --profile actions stop
    if ($LASTEXITCODE -ne 0) {
        throw 'Docker Compose failed to stop the IncidentPilot core profile.'
    }
} finally {
    Pop-Location
}

if (-not (Test-Path (Join-Path $demoPath 'docker-compose.yml'))) {
    throw 'Pinned OpenTelemetry Demo is missing. Nothing was stopped.'
}

Push-Location $demoPath
try {
    & docker compose -f docker-compose.yml -f $overlayPath stop
    if ($LASTEXITCODE -ne 0) {
        throw 'Docker Compose failed to stop the OpenTelemetry Demo.'
    }
} finally {
    Pop-Location
}
