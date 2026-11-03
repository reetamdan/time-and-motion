[CmdletBinding()]
param(
    [string]$Distribution = "",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$DemoArgs
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Wsl = Get-Command "wsl.exe" -ErrorAction SilentlyContinue
if ($null -eq $Wsl) { throw "WSL was not found. The managed demo lifecycle uses scripts/run-demo.sh in the selected WSL runtime." }

$WslArgs = [System.Collections.Generic.List[string]]::new()
if (-not [string]::IsNullOrWhiteSpace($Distribution)) {
    $WslArgs.Add("--distribution")
    $WslArgs.Add($Distribution)
}
$WslArgs.Add("--cd")
$WslArgs.Add($Root)
$WslArgs.Add("--exec")
$WslArgs.Add("bash")
$WslArgs.Add("scripts/run-demo.sh")
foreach ($Argument in $DemoArgs) { $WslArgs.Add($Argument) }

Write-Host "Running the managed demo in WSL. Press Ctrl+C to stop its managed processes."
& $Wsl.Source @WslArgs
exit $LASTEXITCODE
