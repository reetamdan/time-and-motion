# Runtime environment for the Windows launcher (Start-Aegis.cmd) and the A6000 validators.
# Runtime data (SQLite, evidence, capability cache) lives under .install\a6000\state, outside the
# repository's checked-in historical databases and evidence.
$AegisRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$AegisState = Join-Path $AegisRoot '.install\a6000\state'
New-Item -ItemType Directory -Force -Path $AegisState | Out-Null

# Windows may block user PATH writes; merge the user PATH and a per-user FFmpeg install explicitly.
$UserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$env:Path = ((@($env:Path -split ';') + @($UserPath -split ';')) | Where-Object { $_ } | Select-Object -Unique) -join ';'
$FFmpegRoot = Join-Path $env:LOCALAPPDATA 'Programs\FFmpeg'
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -and (Test-Path $FFmpegRoot)) {
    $FFmpeg = Get-ChildItem -LiteralPath $FFmpegRoot -Filter ffmpeg.exe -Recurse | Select-Object -First 1
    if ($FFmpeg) { $env:Path = $FFmpeg.Directory.FullName + ';' + $env:Path }
}

$env:CCTV_PYTHON = Join-Path $AegisRoot '.venv-windows\Scripts\python.exe'
$env:PYTHON = $env:CCTV_PYTHON

# Profile: configs\a6000 on an RTX A6000 (its engines are sm86-specific), configs\ elsewhere.
# An explicit CCTV_CONFIG_DIR always wins.
if (-not $env:CCTV_CONFIG_DIR) {
    $Gpu = ''
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        $Gpu = (& nvidia-smi --query-gpu=name --format=csv,noheader 2>$null | Select-Object -First 1)
    }
    $env:CCTV_CONFIG_DIR = if ($Gpu -match 'RTX A6000') { Join-Path $AegisRoot 'configs\a6000' } else { Join-Path $AegisRoot 'configs' }
}
$AegisProfile = if ($env:CCTV_CONFIG_DIR -like '*a6000*') { 'RTX A6000 (configs\a6000)' } else { "default ($env:CCTV_CONFIG_DIR)" }

if (-not $env:CCTV_DATABASE_PATH) { $env:CCTV_DATABASE_PATH = Join-Path $AegisState 'cctv.db' }
if (-not $env:CCTV_EVIDENCE_ROOT) { $env:CCTV_EVIDENCE_ROOT = Join-Path $AegisState 'evidence' }
if (-not $env:CCTV_CAPABILITY_CACHE) { $env:CCTV_CAPABILITY_CACHE = Join-Path $AegisState 'capability-cache.json' }
$env:CCTV_OFFLINE = '1'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$env:HF_DATASETS_OFFLINE = '1'
$env:NO_PROXY = 'localhost,127.0.0.1,::1'
$env:PYTHONDONTWRITEBYTECODE = '1'
