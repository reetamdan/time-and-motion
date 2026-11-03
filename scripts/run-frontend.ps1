[CmdletBinding()]
param(
    [string]$FrontendHost = $env:FRONTEND_HOST,
    [int]$Port = $(if ($env:FRONTEND_PORT) { [int]$env:FRONTEND_PORT } else { 5173 }),
    [ValidateSet("dev", "preview")]
    [string]$Mode = $(if ($env:CCTV_FRONTEND_MODE) { $env:CCTV_FRONTEND_MODE } else { "dev" }),
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ServerArgs
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Frontend = Join-Path $Root "frontend"
if ([string]::IsNullOrWhiteSpace($FrontendHost)) { $FrontendHost = "127.0.0.1" }
if ($Port -lt 1 -or $Port -gt 65535) { throw "Port must be from 1 to 65535" }
if (-not (Get-Command "node.exe" -ErrorAction SilentlyContinue)) { throw "node was not found; runtime does not install it" }
if (-not (Get-Command "npm.cmd" -ErrorAction SilentlyContinue)) { throw "npm was not found; runtime does not install it" }
if (-not (Test-Path (Join-Path $Frontend "package.json"))) { throw "frontend/package.json was not found" }
if (-not (Test-Path (Join-Path $Frontend "node_modules"))) { throw "Frontend dependencies are missing; run installation first" }
if ($Mode -eq "preview" -and -not (Test-Path (Join-Path $Frontend "dist\index.html"))) {
    throw "frontend/dist/index.html is missing; build it during installation"
}
if ($FrontendHost -notin @("127.0.0.1", "localhost", "::1")) {
    Write-Warning "Non-loopback or wildcard bind requested. Configure authentication and firewall rules explicitly."
}

$env:CCTV_OFFLINE = $(if ($env:CCTV_OFFLINE) { $env:CCTV_OFFLINE } else { "1" })
$env:NO_PROXY = $(if ($env:NO_PROXY) { $env:NO_PROXY } else { "localhost,127.0.0.1,::1" })
$env:no_proxy = $env:NO_PROXY
Set-Location $Frontend
& npm.cmd run $Mode -- --host $FrontendHost --port $Port --strictPort @ServerArgs
exit $LASTEXITCODE
