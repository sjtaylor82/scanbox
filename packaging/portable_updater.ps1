param(
    [int]$ScanBoxProcessId,
    [string]$PayloadPath,
    [string]$AppDirectory,
    [string]$ReadyPath,
    [string]$LogPath,
    [switch]$NoRestart
)

# Adapted from MapInABox's portable updater: prepare before signalling ready,
# tolerate an exited parent, record errors, and restore the previous files.
$ErrorActionPreference = 'Stop'
$appRoot = [IO.Path]::GetFullPath($AppDirectory).TrimEnd('\')
$token = [guid]::NewGuid().ToString('N')
$stage = Join-Path $appRoot ('.update-new-' + $token)
$backup = Join-Path $appRoot ('.update-backup-' + $token)
$oldItems = New-Object System.Collections.Generic.List[string]
$newItems = New-Object System.Collections.Generic.List[string]
$closed = $false
$success = $false

function Write-UpdateLog([string]$Message) {
    Add-Content -LiteralPath $LogPath -Value ("[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message") -Encoding UTF8
}

try {
    Write-UpdateLog "Preparing update for $appRoot from $PayloadPath"
    if (-not (Test-Path -LiteralPath (Join-Path $PayloadPath 'ScanBox.exe') -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $PayloadPath '_internal') -PathType Container)) {
        throw 'The update is missing ScanBox.exe or its _internal folder.'
    }
    # These are the only application-owned entries in the Windows bundle.
    # Never copy config, engines, images, output, or other user-owned folders.
    New-Item -ItemType Directory -Path $stage | Out-Null
    New-Item -ItemType Directory -Path $backup | Out-Null
    foreach ($name in @('ScanBox.exe', '_internal')) {
        Copy-Item -LiteralPath (Join-Path $PayloadPath $name) -Destination $stage -Recurse -Force
    }
    Write-UpdateLog 'Preparation complete; waiting for ScanBox to close.'
    Set-Content -LiteralPath $ReadyPath -Value 'ready' -Encoding ASCII
    # ScanBox may have exited before PowerShell reaches this instruction.
    Wait-Process -Id $ScanBoxProcessId -ErrorAction SilentlyContinue
    $closed = $true
    foreach ($name in @('ScanBox.exe', '_internal')) {
        $destination = Join-Path $appRoot $name
        if (Test-Path -LiteralPath $destination) {
            Move-Item -LiteralPath $destination -Destination (Join-Path $backup $name)
            $oldItems.Add($name)
        }
        Move-Item -LiteralPath (Join-Path $stage $name) -Destination $destination
        $newItems.Add($name)
    }
    $success = $true
    Write-UpdateLog "Update completed. Previous application retained at $backup"
} catch {
    $failure = $_.Exception.Message
    $env:SCANBOX_UPDATE_FAILED = $LogPath
    Write-UpdateLog "Update failed: $failure"
    if ($closed) {
        try {
            foreach ($name in $newItems) {
                # Move failed replacements back into our own staging folder.
                Move-Item -LiteralPath (Join-Path $appRoot $name) -Destination (Join-Path $stage $name)
            }
            foreach ($name in $oldItems) {
                Move-Item -LiteralPath (Join-Path $backup $name) -Destination (Join-Path $appRoot $name)
            }
            Write-UpdateLog 'Previous application restored.'
        } catch {
            Write-UpdateLog "Restore failed: $($_.Exception.Message). Backup retained at $backup"
        }
    } else {
        Set-Content -LiteralPath $ReadyPath -Value 'error' -Encoding ASCII
    }
} finally {
    if ($closed -and -not $NoRestart) {
        try {
            Start-Process -FilePath (Join-Path $appRoot 'ScanBox.exe') -WorkingDirectory $appRoot -WindowStyle Hidden
            Write-UpdateLog 'ScanBox restart requested.'
        } catch {
            Write-UpdateLog "Restart failed: $($_.Exception.Message)"
            $success = $false
        }
    }
}
if (-not $success) { exit 1 }
