# ======================================================================
# KEENMIND AUTOMATION STUDIO - ZERO-TOKEN POWERSHELL AUTO-SYNC ENGINE
# ======================================================================

$ErrorActionPreference = "SilentlyContinue"
$ProgressPreference = "SilentlyContinue"

# Resolve root directory
$RootDir = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path "$RootDir\run_server.py")) {
    $RootDir = (Get-Location).Path
}

$ConfigFile = "$RootDir\config\updater_config.json"
$VersionFile = "$RootDir\version.json"

if (-not (Test-Path $ConfigFile)) {
    exit 0
}

try {
    $config = Get-Content $ConfigFile -Raw | ConvertFrom-Json
    $owner = if ($config.github_owner) { $config.github_owner } else { "Ganeshmakad" }
    $repo = if ($config.github_repo) { $config.github_repo } else { "keenmind-automation" }
    $branch = if ($config.branch) { $config.branch } else { "main" }
    $timeout = if ($config.check_timeout_seconds) { [int]$config.check_timeout_seconds } else { 4 }
} catch {
    exit 0
}

# 1. Read local commit SHA
$localSha = ""
if (Test-Path $VersionFile) {
    try {
        $verData = Get-Content $VersionFile -Raw | ConvertFrom-Json
        $localSha = $verData.commit_sha
    } catch {}
}

# 2. Query GitHub Commits API anonymously (Zero Tokens required)
$apiUrl = "https://api.github.com/repos/$owner/$repo/commits/$branch"
$headers = @{
    "User-Agent" = "Keenmind-AutoSync-Client"
    "Accept"     = "application/vnd.github.v3+json"
}

try {
    $response = Invoke-RestMethod -Uri $apiUrl -Headers $headers -TimeoutSec $timeout -Method Get
    $remoteSha = $response.sha
} catch {
    # Offline or rate-limited: fallback gracefully without blocking startup
    exit 0
}

if (-not $remoteSha) {
    exit 0
}

# 3. Fast Exit if code is already up-to-date (< 0.5s check)
if ($localSha -and ($localSha.Trim() -eq $remoteSha.Trim())) {
    exit 0
}

# 4. Perform Safe Update
Write-Host ""
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "  [AUTO-SYNC] New Studio update detected ($($remoteSha.Substring(0,7)))!" -ForegroundColor Green
Write-Host "  Synchronizing latest code from GitHub..." -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan

$tempDir = "$RootDir\temp"
if (-not (Test-Path $tempDir)) {
    New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
}

$zipPath = "$tempDir\update_latest.zip"
$extractDir = "$tempDir\update_extracted"
$zipUrl = "https://github.com/$owner/$repo/archive/refs/heads/$branch.zip"

try {
    # Download zip archive
    Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -TimeoutSec 30 -Headers $headers

    if (-not (Test-Path $zipPath) -or ((Get-Item $zipPath).Length -lt 1000)) {
        Write-Host "  [Auto-Sync] Download incomplete, continuing with local version..." -ForegroundColor Yellow
        exit 0
    }

    if (Test-Path $extractDir) {
        Remove-Item -Path $extractDir -Recurse -Force | Out-Null
    }

    # Expand Archive
    Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force

    $subFolders = Get-ChildItem -Path $extractDir -Directory
    $sourceCodeDir = if ($subFolders.Count -eq 1) { $subFolders[0].FullName } else { $extractDir }

    # Check requirements hash before updating
    $reqOldHash = if (Test-Path "$RootDir\requirements.txt") { (Get-FileHash "$RootDir\requirements.txt").Hash } else { "" }

    # Protected Folders/Files whitelist check: NEVER overwrite sensitive user data
    $protectedNames = @(".env", "cache", "output", "temp", "venv", ".venv", "env", ".git")

    $itemsToCopy = Get-ChildItem -Path $sourceCodeDir
    foreach ($item in $itemsToCopy) {
        if ($protectedNames -contains $item.Name.ToLower()) {
            continue
        }
        if ($item.PSIsContainer) {
            $destTarget = "$RootDir\$($item.Name)"
            if (-not (Test-Path $destTarget)) {
                New-Item -ItemType Directory -Path $destTarget -Force | Out-Null
            }
            Copy-Item -Path "$($item.FullName)\*" -Destination $destTarget -Recurse -Force
        } else {
            Copy-Item -Path $item.FullName -Destination "$RootDir\$($item.Name)" -Force
        }
    }

    # Re-install requirements if updated
    $reqNewHash = if (Test-Path "$RootDir\requirements.txt") { (Get-FileHash "$RootDir\requirements.txt").Hash } else { "" }
    if ($reqOldHash -ne $reqNewHash -and (Test-Path "$RootDir\venv\Scripts\pip.exe")) {
        Write-Host "  [Auto-Sync] Updating project dependencies in venv..." -ForegroundColor Yellow
        & "$RootDir\venv\Scripts\pip.exe" install -r "$RootDir\requirements.txt" --quiet
    }

    # Update version.json (BOM-less UTF-8 for cross-platform Python safety)
    $newVersionInfo = @{
        commit_sha = $remoteSha
        version    = "1.0.0"
        updated_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    }
    $jsonText = $newVersionInfo | ConvertTo-Json -Depth 4
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($VersionFile, $jsonText, $utf8NoBom)

    Write-Host "  [Auto-Sync] Successfully updated to commit $($remoteSha.Substring(0,7))!" -ForegroundColor Green
    Write-Host "======================================================================" -ForegroundColor Cyan
    Write-Host ""
} catch {
    Write-Host "  [Auto-Sync] Notice: Sync encountered an error ($($_.Exception.Message)). Launching server anyway..." -ForegroundColor Yellow
} finally {
    # Cleanup temporary update artifacts
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    if (Test-Path $extractDir) { Remove-Item $extractDir -Recurse -Force }
}

exit 0
