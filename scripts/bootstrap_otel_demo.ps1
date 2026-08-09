$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$demoPath = Join-Path $projectRoot '.runtime\opentelemetry-demo'
$repoUrl = 'https://github.com/open-telemetry/opentelemetry-demo.git'
$tag = '2.2.0'
$commit = 'b74a7bc7bbe66099c61951f42b24dab8b6f02d18'

if (Test-Path $demoPath) {
    if (-not (Test-Path (Join-Path $demoPath '.git'))) {
        throw "$demoPath exists but is not a Git repository; refusing to overwrite it."
    }

    $origin = (& git -C $demoPath remote get-url origin).Trim()
    if ($LASTEXITCODE -ne 0 -or $origin -ne $repoUrl) {
        throw "$demoPath does not have the expected origin; refusing to overwrite it."
    }
} else {
    $runtimePath = Split-Path -Parent $demoPath
    New-Item -ItemType Directory -Force $runtimePath | Out-Null
    & git -c http.version=HTTP/1.1 clone --branch $tag --depth 1 --single-branch $repoUrl $demoPath
    if ($LASTEXITCODE -ne 0) {
        if (Test-Path $demoPath) {
            throw 'Failed clone left a directory behind; refusing to overwrite it.'
        }
        & docker run --rm --mount "type=bind,source=$runtimePath,target=/workspace" `
            alpine/git@sha256:697cb1c85aefc5724febaec2202a974e0d66f6abb6be91a9a86d0c8757af692a `
            clone --branch $tag --depth 1 --single-branch $repoUrl /workspace/opentelemetry-demo
        if ($LASTEXITCODE -ne 0) {
            throw 'Failed to clone the pinned OpenTelemetry Demo repository.'
        }
        & docker run --rm --mount "type=bind,source=$runtimePath,target=/workspace" `
            alpine/git@sha256:697cb1c85aefc5724febaec2202a974e0d66f6abb6be91a9a86d0c8757af692a `
            -C /workspace/opentelemetry-demo config core.filemode false
        if ($LASTEXITCODE -ne 0) {
            throw 'Failed to configure the Docker-created OpenTelemetry Demo clone.'
        }
    }
}

$actualCommit = (& git -C $demoPath rev-list -n 1 "refs/tags/$tag").Trim()
if ($LASTEXITCODE -ne 0 -or $actualCommit -ne $commit) {
    throw "Expected tag $tag at $commit, found $actualCommit."
}

Write-Host "OpenTelemetry Demo $tag is verified at $actualCommit"
