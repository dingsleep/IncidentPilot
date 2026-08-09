param(
    [switch]$SkipBuild,
    [switch]$ReadOnly,
    [int]$ApiHostPort = 8200,
    [int]$WebHostPort = 5173
)

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$demoPath = Join-Path $projectRoot '.runtime\opentelemetry-demo'
$overlayPath = Join-Path $projectRoot 'infra\otel-demo\docker-compose.incidentpilot.yml'
$env:DEMO_VERSION = '2.2.0'
$env:INCIDENTPILOT_API_HOST_PORT = "$ApiHostPort"
$env:INCIDENTPILOT_WEB_HOST_PORT = "$WebHostPort"
$env:INCIDENTPILOT_ACTION_ENABLED = if ($ReadOnly) { 'false' } else { 'true' }

if (-not (Test-Path (Join-Path $demoPath 'docker-compose.yml'))) {
    throw 'Pinned OpenTelemetry Demo is missing. Run .\scripts\bootstrap_otel_demo.ps1 first.'
}

Push-Location $demoPath
try {
    & docker compose --parallel 1 -f docker-compose.yml -f $overlayPath pull `
        --ignore-buildable --policy missing
    if ($LASTEXITCODE -ne 0) {
        Write-Warning 'Sequential image pre-pull did not complete; starting from the cached images.'
    }

    & docker compose -f docker-compose.yml -f $overlayPath up --detach --pull never
    if ($LASTEXITCODE -ne 0) {
        throw 'Docker Compose failed to start the OpenTelemetry Demo.'
    }

    $deadline = (Get-Date).AddMinutes(10)
    while ((Get-Date) -lt $deadline) {
        $raw = & docker compose -f docker-compose.yml -f $overlayPath ps --format json
        if ($LASTEXITCODE -eq 0 -and $raw) {
            $services = @($raw | ConvertFrom-Json)
            $pending = @($services | Where-Object {
                    $health = if ($_.PSObject.Properties.Match('Health').Count) { $_.Health } else { '' }
                    $_.State -ne 'running' -or ($health -and $health -ne 'healthy')
                })
            if ($services.Count -gt 0 -and $pending.Count -eq 0) {
                Write-Host 'OpenTelemetry Demo containers are running and healthy.'
                break
            }
        }
        Start-Sleep -Seconds 5
    }

    $unhealthy = @($services | Where-Object {
            $health = if ($_.PSObject.Properties.Match('Health').Count) { $_.Health } else { '' }
            $_.State -ne 'running' -or ($health -and $health -ne 'healthy')
        })
    if ($unhealthy.Count -gt 0) {
        & docker compose -f docker-compose.yml -f $overlayPath ps
        foreach ($service in $unhealthy) {
            & docker compose -f docker-compose.yml -f $overlayPath logs --tail 100 $service.Service
        }
        throw 'Timed out waiting for OpenTelemetry Demo containers to become healthy.'
    }
} finally {
    Pop-Location
}

$composeArgs = @('compose', '--profile', 'core')
if (-not $ReadOnly) {
    $composeArgs += @('--profile', 'actions')
}
$composeArgs += @('up', '--detach')
if (-not $SkipBuild) {
    $composeArgs += '--build'
}
& docker @composeArgs
if ($LASTEXITCODE -ne 0) {
    throw 'Docker Compose failed to start the IncidentPilot core profile.'
}

$deadline = (Get-Date).AddMinutes(2)
while ((Get-Date) -lt $deadline) {
    try {
        $health = Invoke-RestMethod "http://127.0.0.1:$ApiHostPort/api/v1/health/ready" -TimeoutSec 3
        if ($health.status -eq 'ready') {
            $mode = if ($ReadOnly) { 'read-only' } else { 'approval-gated actions' }
            Write-Host "IncidentPilot is ready ($mode): http://127.0.0.1:$WebHostPort"
            exit 0
        }
    } catch {
        # Containers may still be starting or migrations may still be running.
    }
    Start-Sleep -Seconds 3
}

& docker compose --profile core --profile actions ps
& docker compose --profile core --profile actions logs --tail 100 `
    incident-api graph-worker telemetry-mcp action-mcp demo-runner
throw 'Timed out waiting for IncidentPilot to become ready.'
