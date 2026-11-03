[CmdletBinding()]
param(
    [string]$BackendHost = $env:BACKEND_HOST,
    [int]$Port = $(if ($env:BACKEND_PORT) { [int]$env:BACKEND_PORT } else { 8000 }),
    [string]$Python = $env:PYTHON,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$UvicornArgs
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($BackendHost)) { $BackendHost = "127.0.0.1" }
if ($Port -lt 1 -or $Port -gt 65535) { throw "Port must be from 1 to 65535" }
if ([string]::IsNullOrWhiteSpace($Python)) {
    $Candidates = @(
        (Join-Path $Root ".venv-windows\Scripts\python.exe"),
        (Join-Path $Root ".venv\Scripts\python.exe")
    )
    $Python = $Candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if ([string]::IsNullOrWhiteSpace($Python) -or -not (Get-Command $Python -ErrorAction SilentlyContinue)) {
    throw "Windows project Python was not found. Create .venv-windows using the README installation commands."
}
if (-not (Test-Path (Join-Path $Root "backend\app\main.py"))) {
    throw "backend/app/main.py not found; cannot start backend.app.main:app"
}
if ($BackendHost -notin @("127.0.0.1", "localhost", "::1")) {
    Write-Warning "Non-loopback or wildcard bind requested. Configure authentication and firewall rules explicitly."
}

$env:CCTV_CONFIG_DIR = $(if ($env:CCTV_CONFIG_DIR) { $env:CCTV_CONFIG_DIR } else { Join-Path $Root "configs" })
$env:CCTV_DATA_DIR = $(if ($env:CCTV_DATA_DIR) { $env:CCTV_DATA_DIR } else { Join-Path $Root "data" })
$env:CCTV_MODEL_DIR = $(if ($env:CCTV_MODEL_DIR) { $env:CCTV_MODEL_DIR } else { Join-Path $Root "models" })
$env:CCTV_OFFLINE = $(if ($env:CCTV_OFFLINE) { $env:CCTV_OFFLINE } else { "1" })
$env:HF_HUB_OFFLINE = $(if ($env:HF_HUB_OFFLINE) { $env:HF_HUB_OFFLINE } else { "1" })
$env:TRANSFORMERS_OFFLINE = $(if ($env:TRANSFORMERS_OFFLINE) { $env:TRANSFORMERS_OFFLINE } else { "1" })
$env:HF_DATASETS_OFFLINE = $(if ($env:HF_DATASETS_OFFLINE) { $env:HF_DATASETS_OFFLINE } else { "1" })
$env:NO_PROXY = $(if ($env:NO_PROXY) { $env:NO_PROXY } else { "localhost,127.0.0.1,::1" })
$env:no_proxy = $env:NO_PROXY
$env:PIP_NO_INDEX = "1"
$env:PYTHONDONTWRITEBYTECODE = "1"

Write-Warning "This Windows command starts the API only. It does not prove WSL/DeepStream GPU execution; use the WSL runtime for perception validation."
Set-Location $Root
& $Python -m uvicorn backend.app.main:app --host $BackendHost --port $Port @UvicornArgs
exit $LASTEXITCODE
