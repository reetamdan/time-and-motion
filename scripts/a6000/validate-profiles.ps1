# All three detector profiles on the RTX A6000: real clip, evidence, history and container cleanup.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'env.ps1')
Set-Location $AegisRoot
& $env:CCTV_PYTHON (Join-Path $PSScriptRoot 'validate_profiles.py')
exit $LASTEXITCODE
