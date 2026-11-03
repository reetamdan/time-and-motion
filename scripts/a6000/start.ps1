[CmdletBinding()]
param([switch]$Dev)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'env.ps1')
Set-Location $AegisRoot
$AegisPort = if ($Dev -and $env:CCTV_API_PORT) { [int]$env:CCTV_API_PORT } elseif (-not $Dev -and $env:BACKEND_PORT) { [int]$env:BACKEND_PORT } else { 8000 }
$Listeners = @(Get-NetTCPConnection -State Listen -LocalPort $AegisPort -ErrorAction SilentlyContinue)
if ($Listeners.Count -gt 0) {
    # A second backend must not run startup recovery against an active instance's database.
    Write-Host "Port $AegisPort is already in use; no second backend was started."
    Write-Host "If Aegis Ops is already running, open http://127.0.0.1:$AegisPort/"
    Write-Host 'Stop it first with: .venv-windows\Scripts\python.exe scripts\a6000\stop-server.py'
    exit 0
}

# WSL stops its VM when no Windows process is attached to a distro, which takes Docker down with it:
# the dashboard then reports not_ready and the next Start demo waits for WSL to boot. Keep one
# attached process for the lifetime of this launcher. -NoNewWindow ties it to this console, so
# Ctrl+C or closing the window ends it too; stop-server.py removes one left behind.
$RuntimeYaml = Join-Path $env:CCTV_CONFIG_DIR 'runtime.yaml'
$Distro = 'Ubuntu-22.04'
if ((Test-Path $RuntimeYaml) -and ((Get-Content -Raw $RuntimeYaml) -match '(?m)^\s*wsl_distro:\s*(\S+)')) { $Distro = $Matches[1] }

function Test-WslDocker {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & wsl.exe -d $Distro -e docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $previous
    }
}

$KeepAlive = $null
try {
    if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
        $KeepAlive = Start-Process -FilePath wsl.exe -NoNewWindow -PassThru `
            -ArgumentList "-d $Distro -e sh -c `"exec sleep infinity # aegis-keepalive`""
        Write-Host "Keeping WSL ($Distro) running while the server runs."
        $Deadline = (Get-Date).AddSeconds(90)
        while (-not (Test-WslDocker)) {
            if ((Get-Date) -gt $Deadline) {
                Write-Warning "Docker in WSL ($Distro) is not reachable; the dashboard will report not_ready until it is."
                break
            }
            Start-Sleep -Seconds 2
        }
    }

    Write-Host "Profile: $AegisProfile"
    if ($Dev) {
        Write-Host 'Aegis Ops development dashboard: http://127.0.0.1:7100/'
        & node (Join-Path $AegisRoot 'scripts\dev.mjs')
    } else {
        Write-Host 'Aegis Ops dashboard: http://127.0.0.1:8000/  (Ctrl+C stops the server)'
        & (Join-Path $AegisRoot 'scripts\run-backend.ps1')
    }
    $ExitCode = $LASTEXITCODE
} finally {
    if ($KeepAlive -and -not $KeepAlive.HasExited) { Stop-Process -Id $KeepAlive.Id -Force -ErrorAction SilentlyContinue }
}
exit $ExitCode
