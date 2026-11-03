[CmdletBinding()]
param(
    [string]$OutputDirectory = "",
    [string]$WslDistribution = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$RunId = [DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssZ")
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $ProjectRoot "reports\environment\$RunId-host"
}
if (Test-Path $OutputDirectory) {
    Write-Error "Refusing to overwrite existing evidence directory: $OutputDirectory"
    exit 1
}

try {
    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
} catch {
    Write-Error "Cannot create evidence directory '$OutputDirectory': $($_.Exception.Message)"
    exit 1
}

$Utf8 = New-Object System.Text.UTF8Encoding($false)
$Results = [System.Collections.Generic.List[object]]::new()

function Write-Status {
    param([string]$Name, [string]$Status, [string]$LogPath)
    Write-Host ("[{0}] {1} -> {2}" -f $Status.ToUpperInvariant(), $Name, $LogPath)
}

function Add-Result {
    param(
        [string]$Name,
        [bool]$Required,
        [string]$Command,
        [string]$Status,
        [Nullable[int]]$ExitCode,
        [string]$LogPath
    )
    $Results.Add([pscustomobject]@{
        name = $Name
        required = $Required
        command = $Command
        status = $Status
        exit_code = $ExitCode
        log = (Split-Path -Leaf $LogPath)
    })
    Write-Status -Name $Name -Status $Status -LogPath $LogPath
}

function Invoke-NativeProbe {
    param(
        [string]$Name,
        [string]$FilePath,
        [string[]]$Arguments = @(),
        [switch]$Required
    )
    $LogPath = Join-Path $OutputDirectory "$Name.log"
    $Command = Get-Command $FilePath -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    $DisplayCommand = (@($FilePath) + $Arguments) -join " "
    if ($null -eq $Command) {
        [IO.File]::WriteAllText($LogPath, "COMMAND NOT FOUND: $DisplayCommand`n", $Utf8)
        Add-Result -Name $Name -Required ([bool]$Required) -Command $DisplayCommand -Status "not_found" -ExitCode 127 -LogPath $LogPath
        return
    }
    try {
        $Captured = @(& $Command.Source @Arguments 2>&1)
        $Code = $LASTEXITCODE
        if ($null -eq $Code) { $Code = 0 }
        $Text = $Captured | Out-String -Width 4096
        [IO.File]::WriteAllText($LogPath, $Text, $Utf8)
        $Status = if ($Code -eq 0) { "passed" } else { "failed" }
        Add-Result -Name $Name -Required ([bool]$Required) -Command $DisplayCommand -Status $Status -ExitCode ([int]$Code) -LogPath $LogPath
    } catch {
        [IO.File]::WriteAllText($LogPath, "COMMAND LAUNCH FAILED: $DisplayCommand`n$($_.Exception.Message)`n", $Utf8)
        Add-Result -Name $Name -Required ([bool]$Required) -Command $DisplayCommand -Status "launch_failed" -ExitCode 126 -LogPath $LogPath
    }
}

function Invoke-CmdletProbe {
    param(
        [string]$Name,
        [scriptblock]$Action,
        [switch]$Required
    )
    $LogPath = Join-Path $OutputDirectory "$Name.log"
    try {
        $Captured = @(& $Action 2>&1)
        $Succeeded = $?
        $Text = $Captured | Out-String -Width 4096
        [IO.File]::WriteAllText($LogPath, $Text, $Utf8)
        $Status = if ($Succeeded) { "passed" } else { "failed" }
        $Code = if ($Succeeded) { 0 } else { 1 }
        Add-Result -Name $Name -Required ([bool]$Required) -Command $Action.ToString().Trim() -Status $Status -ExitCode $Code -LogPath $LogPath
    } catch {
        [IO.File]::WriteAllText($LogPath, "CMDLET FAILED`n$($_.Exception.ToString())`n", $Utf8)
        Add-Result -Name $Name -Required ([bool]$Required) -Command $Action.ToString().Trim() -Status "failed" -ExitCode 1 -LogPath $LogPath
    }
}

function Invoke-WindowsGpuProbe {
    $Name = "windows_gpu_identity"
    $LogPath = Join-Path $OutputDirectory "$Name.log"
    $CommandText = "Get-CimInstance Win32_VideoController; require RTX 5070 Ti identity"
    try {
        $Controllers = @(Get-CimInstance Win32_VideoController -ErrorAction Stop)
        $Controllers | Select-Object Name, DriverVersion, AdapterRAM, PNPDeviceID, Status | Format-List | Out-String -Width 240 | ForEach-Object {
            [IO.File]::WriteAllText($LogPath, $_, $Utf8)
        }
        $Expected = @($Controllers | Where-Object { $_.Name -match "RTX\s+5070\s+Ti" })
        if ($Expected.Count -eq 0) {
            Add-Content -Path $LogPath -Value "EXPECTED GPU NOT FOUND: NVIDIA RTX 5070 Ti" -Encoding UTF8
            Add-Result -Name $Name -Required $true -Command $CommandText -Status "failed" -ExitCode 3 -LogPath $LogPath
        } else {
            Add-Result -Name $Name -Required $true -Command $CommandText -Status "passed" -ExitCode 0 -LogPath $LogPath
        }
    } catch {
        [IO.File]::WriteAllText($LogPath, "WINDOWS GPU QUERY FAILED`n$($_.Exception.ToString())`n", $Utf8)
        Add-Result -Name $Name -Required $true -Command $CommandText -Status "failed" -ExitCode 1 -LogPath $LogPath
    }
}

[IO.File]::WriteAllText((Join-Path $OutputDirectory "invocation.txt"), @(
    "utc_started=$([DateTimeOffset]::UtcNow.ToString('o'))",
    "project_root=$ProjectRoot",
    "powershell=$($PSVersionTable.PSVersion.ToString())",
    "wsl_distribution=$WslDistribution",
    "note=Inspection only; no drivers, packages, images, or system settings are changed."
) -join "`n", $Utf8)

Invoke-CmdletProbe -Name "windows_os" -Required -Action { Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, BuildNumber, OSArchitecture, LastBootUpTime | Format-List }
Invoke-WindowsGpuProbe
Invoke-CmdletProbe -Name "windows_computer" -Action { Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer, Model, TotalPhysicalMemory, NumberOfLogicalProcessors | Format-List }
Invoke-CmdletProbe -Name "windows_cpu" -Action { Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed | Format-List }
Invoke-CmdletProbe -Name "windows_disks" -Action { Get-PSDrive -PSProvider FileSystem | Select-Object Name, Used, Free, Root | Format-Table -AutoSize }
Invoke-CmdletProbe -Name "windows_battery" -Action { Get-CimInstance Win32_Battery | Select-Object Name, BatteryStatus, EstimatedChargeRemaining | Format-List }

Invoke-NativeProbe -Name "windows_nvidia_smi" -FilePath "nvidia-smi.exe" -Required
Invoke-NativeProbe -Name "windows_nvidia_smi_query" -FilePath "nvidia-smi.exe" -Arguments @("--query-gpu=name,uuid,driver_version,memory.total,memory.used,temperature.gpu,utilization.gpu", "--format=csv")
Invoke-NativeProbe -Name "windows_nvcc" -FilePath "nvcc.exe" -Arguments @("--version")
Invoke-NativeProbe -Name "windows_node" -FilePath "node.exe" -Arguments @("--version")
Invoke-NativeProbe -Name "windows_npm" -FilePath "npm.cmd" -Arguments @("--version")
Invoke-NativeProbe -Name "windows_python" -FilePath "python.exe" -Arguments @("--version")
Invoke-NativeProbe -Name "windows_ffmpeg" -FilePath "ffmpeg.exe" -Arguments @("-version")
Invoke-NativeProbe -Name "windows_gstreamer" -FilePath "gst-launch-1.0.exe" -Arguments @("--version")
Invoke-NativeProbe -Name "windows_deepstream" -FilePath "deepstream-app.exe" -Arguments @("--version")
Invoke-NativeProbe -Name "windows_tensorrt" -FilePath "trtexec.exe" -Arguments @("--version")
Invoke-NativeProbe -Name "windows_docker_version" -FilePath "docker.exe" -Arguments @("version")
Invoke-NativeProbe -Name "windows_docker_info" -FilePath "docker.exe" -Arguments @("info")

Invoke-NativeProbe -Name "wsl_status" -FilePath "wsl.exe" -Arguments @("--status") -Required
Invoke-NativeProbe -Name "wsl_version" -FilePath "wsl.exe" -Arguments @("--version")
Invoke-NativeProbe -Name "wsl_distros" -FilePath "wsl.exe" -Arguments @("--list", "--verbose") -Required

$DistroArgs = @()
if (-not [string]::IsNullOrWhiteSpace($WslDistribution)) {
    $DistroArgs = @("--distribution", $WslDistribution)
}
Invoke-NativeProbe -Name "wsl_kernel" -FilePath "wsl.exe" -Arguments ($DistroArgs + @("--exec", "uname", "-a")) -Required
Invoke-NativeProbe -Name "wsl_os_release" -FilePath "wsl.exe" -Arguments ($DistroArgs + @("--exec", "cat", "/etc/os-release")) -Required
Invoke-NativeProbe -Name "wsl_nvidia_smi" -FilePath "wsl.exe" -Arguments ($DistroArgs + @("--exec", "bash", "-lc", "nvidia-smi")) -Required
Invoke-NativeProbe -Name "wsl_nvidia_smi_query" -FilePath "wsl.exe" -Arguments ($DistroArgs + @("--exec", "bash", "-lc", "nvidia-smi --query-gpu=name,uuid,driver_version,memory.total,memory.used,temperature.gpu,utilization.gpu --format=csv"))
Invoke-NativeProbe -Name "wsl_deepstream" -FilePath "wsl.exe" -Arguments ($DistroArgs + @("--exec", "bash", "-lc", "deepstream-app --version"))
Invoke-NativeProbe -Name "wsl_gstreamer" -FilePath "wsl.exe" -Arguments ($DistroArgs + @("--exec", "bash", "-lc", "gst-launch-1.0 --version"))
Invoke-NativeProbe -Name "wsl_tensorrt" -FilePath "wsl.exe" -Arguments ($DistroArgs + @("--exec", "bash", "-lc", "trtexec --version"))
Invoke-NativeProbe -Name "wsl_docker" -FilePath "wsl.exe" -Arguments ($DistroArgs + @("--exec", "bash", "-lc", "docker version"))

$RequiredFailures = @($Results | Where-Object { $_.required -and $_.status -ne "passed" })
$Summary = [ordered]@{
    schema_version = 1
    run_id = $RunId
    utc_finished = [DateTimeOffset]::UtcNow.ToString("o")
    scope = "windows-host-with-read-only-wsl-probes"
    phase_0_result = "BLOCKED"
    phase_0_note = "Host probes alone cannot prove DeepStream, NVDEC, TensorRT, or accelerated inference. Run verify-environment.sh in the intended runtime."
    overall_probe_status = if ($RequiredFailures.Count -eq 0) { "required_probes_passed" } else { "required_probes_failed" }
    required_failure_count = $RequiredFailures.Count
    optional_unavailable_count = @($Results | Where-Object { -not $_.required -and $_.status -ne "passed" }).Count
    probes = $Results
}
$Json = $Summary | ConvertTo-Json -Depth 7
[IO.File]::WriteAllText((Join-Path $OutputDirectory "summary.json"), $Json, $Utf8)

Write-Host "Evidence saved to: $OutputDirectory"
if ($RequiredFailures.Count -gt 0) {
    Write-Warning "$($RequiredFailures.Count) required host/WSL probe(s) failed or were unavailable. See summary.json and the named logs."
    exit 2
}
exit 0
