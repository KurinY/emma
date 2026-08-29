<#
.SYNOPSIS
    Dated, rotated snapshot of the EMMA project on the Windows development PC.

.DESCRIPTION
    This is the *local physical copy* of the development machine, deliberately
    independent from Git and from GitHub: if the repository gets corrupted, if a
    bad rebase eats a branch, or if GitHub is unreachable, the zip files
    produced here still hold a complete copy of the project.

    The snapshot includes the .git directory, so a restored zip is a full
    repository with its history, not just a pile of files. Virtual environments
    and tool caches are excluded: they are large, machine-specific and fully
    reproducible from requirements.txt.

    The script writes nothing outside the destination directory and never
    touches the project itself.

.PARAMETER ProjectPath
    Project directory to snapshot. Defaults to the parent of the directory this
    script lives in, so running it from anywhere just works.

.PARAMETER DestinationPath
    Where the zip files are written. Defaults to D:\EmmaBackups.

.PARAMETER Keep
    How many snapshots to keep; older ones are deleted. Defaults to 14.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\backup-dev.ps1

.EXAMPLE
    .\scripts\backup-dev.ps1 -DestinationPath 'E:\Backups\Emma' -Keep 30

.NOTES
    CLAUDE.md asks every AI assistant working on this project to run this script
    at the end of a session, before committing.
#>

[CmdletBinding()]
param(
    [string] $ProjectPath = (Split-Path -Parent $PSScriptRoot),
    [string] $DestinationPath = 'D:\EmmaBackups',
    [ValidateRange(1, 1000)]
    [int] $Keep = 14
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Step {
    param([string] $Message)
    Write-Host ("{0} | {1}" -f (Get-Date -Format 's'), $Message)
}

# --- Validate input --------------------------------------------------------
if (-not (Test-Path -LiteralPath $ProjectPath -PathType Container)) {
    throw "Project directory not found: $ProjectPath"
}
$ProjectPath = (Resolve-Path -LiteralPath $ProjectPath).Path
$projectName = Split-Path -Leaf $ProjectPath

# --- Prepare the destination ----------------------------------------------
# A missing D: drive is the most likely failure here, and the message says so
# rather than leaving a bare "path not found".
if (-not (Test-Path -LiteralPath $DestinationPath)) {
    try {
        New-Item -ItemType Directory -Path $DestinationPath -Force | Out-Null
    }
    catch {
        throw "Cannot create $DestinationPath - is the drive connected? ($($_.Exception.Message))"
    }
}

$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$archivePath = Join-Path $DestinationPath ("{0}-{1}.zip" -f $projectName, $timestamp)

Write-Step "project:     $ProjectPath"
Write-Step "destination: $DestinationPath"
Write-Step "rotation:    keep $Keep snapshots"

# --- Copy to a staging area, minus the noise -------------------------------
# Compress-Archive has no exclusion support, so the project is mirrored into a
# temporary folder first. robocopy is used because it handles long paths and
# read-only attributes far better than Copy-Item.
$staging = Join-Path ([System.IO.Path]::GetTempPath()) ("emma-backup-" + [guid]::NewGuid().ToString('N'))
$stagingProject = Join-Path $staging $projectName
New-Item -ItemType Directory -Path $stagingProject -Force | Out-Null

try {
    $excludedDirs = @('.venv', 'venv', 'env', '__pycache__', '.pytest_cache', '.ruff_cache', '.mypy_cache')
    $robocopyArgs = @(
        $ProjectPath, $stagingProject,
        '/MIR',                      # mirror the tree
        '/XD'
    ) + $excludedDirs + @(
        '/XF', '*.pyc',
        '/NFL', '/NDL', '/NJH', '/NJS', '/NP', '/R:1', '/W:1'
    )

    Write-Step 'copying project to a temporary staging folder'
    & robocopy.exe @robocopyArgs | Out-Null
    # robocopy uses exit codes as a bitmask: 0-7 mean success, 8 and above mean
    # at least one file could not be copied.
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy failed with exit code $LASTEXITCODE"
    }

    Write-Step "creating $archivePath"
    Compress-Archive -Path $stagingProject -DestinationPath $archivePath -CompressionLevel Optimal -Force

    # A zip that cannot be opened is not a backup: verify before rotating, so a
    # failed run can never delete a good older snapshot.
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($archivePath)
    try {
        $entryCount = $zip.Entries.Count
    }
    finally {
        $zip.Dispose()
    }
    if ($entryCount -lt 1) {
        throw "The archive just written looks empty: $archivePath"
    }

    $sizeMb = [math]::Round((Get-Item -LiteralPath $archivePath).Length / 1MB, 2)
    Write-Step "written $sizeMb MB, $entryCount entries"
}
finally {
    if (Test-Path -LiteralPath $staging) {
        Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# --- Rotation --------------------------------------------------------------
$pattern = "$projectName-*.zip"
$snapshots = @(Get-ChildItem -LiteralPath $DestinationPath -Filter $pattern -File |
    Sort-Object -Property Name -Descending)

if ($snapshots.Count -gt $Keep) {
    foreach ($stale in $snapshots[$Keep..($snapshots.Count - 1)]) {
        Write-Step "rotating out $($stale.Name)"
        Remove-Item -LiteralPath $stale.FullName -Force
    }
}

$kept = @(Get-ChildItem -LiteralPath $DestinationPath -Filter $pattern -File).Count
Write-Step "done: $kept snapshot(s) kept in $DestinationPath"
