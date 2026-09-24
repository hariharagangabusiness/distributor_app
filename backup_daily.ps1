# Daily backup script for Hari Hara Ganga (distributor_app).
# Backs up: (1) the production database on Railway, (2) the laptop's local
# database, (3) a snapshot of the app code — into a dated folder under
# C:\distributor_app\backups\, (4) a copy of that same dated folder into
# your Google Drive sync folder (see $driveBackupDir below), and prunes
# local backups older than 60 days so that folder doesn't grow forever.
#
# Run manually any time with:  powershell -ExecutionPolicy Bypass -File C:\distributor_app\backup_daily.ps1
# Set up to run automatically every day: see SETUP_BACKUP.md in this folder.

$ErrorActionPreference = "Continue"
$root = "C:\distributor_app"
$date = Get-Date -Format "yyyy-MM-dd"
$backupDir = Join-Path $root "backups\$date"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null

# Where Google Drive for Desktop mirrors your Drive to this PC - adjust this
# ONE line to match your own setup (My Drive shows up under a folder named
# "Google Drive" on older versions of the app, or directly as a lettered
# drive like "G:\My Drive" on newer "Mirror" installs - check File Explorer
# for which one you have). This is the only thing you need to configure;
# everything else below just copies into whatever path you set here, and
# Google's own sync client uploads it from there with no credentials or API
# setup needed in this script at all. See SETUP_BACKUP.md for more detail.
$driveBackupDir = "$env:USERPROFILE\Google Drive\My Drive\distributor_app_backups"

$logFile = Join-Path $backupDir "backup_log.txt"
"Backup started: $(Get-Date)" | Out-File -FilePath $logFile

Set-Location $root

# --- 1. Production database (Railway) ------------------------------------
# Uses `railway ssh` to run a tiny Python one-liner in the production
# container that base64-encodes the live SQLite file, captures that text
# over the SSH session, then decodes it back into a real .db file locally.
# (A raw `cat` of the binary file over SSH risks corruption from newline
# translation - base64 round-trips safely as plain text.)
try {
    $b64 = & .\railway.exe ssh "python -c `"import base64; print(base64.b64encode(open('db/distributor.db','rb').read()).decode())`"" 2>> $logFile
    $b64Clean = ($b64 -join "").Trim()
    if ($b64Clean.Length -gt 100) {
        [IO.File]::WriteAllBytes((Join-Path $backupDir "production_distributor.db"), [Convert]::FromBase64String($b64Clean))
        "Production DB backup: OK ($((Get-Item (Join-Path $backupDir 'production_distributor.db')).Length) bytes)" | Out-File -Append $logFile
    } else {
        "Production DB backup: FAILED (railway ssh returned no usable data - is Railway CLI logged in / linked?)" | Out-File -Append $logFile
    }
} catch {
    "Production DB backup: FAILED - $($_.Exception.Message)" | Out-File -Append $logFile
}

# --- 2. Laptop's local database -------------------------------------------
try {
    $localDb = Join-Path $root "db\distributor.db"
    if (Test-Path $localDb) {
        Copy-Item $localDb (Join-Path $backupDir "laptop_distributor.db") -Force
        "Laptop DB backup: OK" | Out-File -Append $logFile
    } else {
        "Laptop DB backup: skipped (no local db\distributor.db found)" | Out-File -Append $logFile
    }
} catch {
    "Laptop DB backup: FAILED - $($_.Exception.Message)" | Out-File -Append $logFile
}

# --- 3. App code snapshot ---------------------------------------------------
try {
    $codeZip = Join-Path $backupDir "app_code.zip"
    $items = Get-ChildItem $root -Filter *.py | Select-Object -ExpandProperty FullName
    $items += (Join-Path $root "templates")
    if (Test-Path (Join-Path $root "static")) { $items += (Join-Path $root "static") }
    Compress-Archive -Path $items -DestinationPath $codeZip -Force
    "App code snapshot: OK" | Out-File -Append $logFile
} catch {
    "App code snapshot: FAILED - $($_.Exception.Message)" | Out-File -Append $logFile
}

# --- 4. Copy today's backup into Google Drive -------------------------------
# This is the whole fix for "backups only ever lived on this one laptop":
# Google Drive for Desktop watches $driveBackupDir and uploads anything
# copied into it on its own, with no credentials or API calls in this
# script at all. If that folder doesn't exist yet - Drive for Desktop isn't
# installed, or $driveBackupDir above hasn't been pointed at your actual
# Drive folder - this is skipped with a clear message rather than failing
# the whole run (the local backup above still succeeded either way).
try {
    $driveParent = Split-Path $driveBackupDir -Parent
    if (Test-Path $driveParent) {
        $driveDateDir = Join-Path $driveBackupDir $date
        New-Item -ItemType Directory -Force -Path $driveDateDir | Out-Null
        Copy-Item (Join-Path $backupDir "*") $driveDateDir -Recurse -Force
        "Google Drive copy: OK ($driveDateDir)" | Out-File -Append $logFile
    } else {
        "Google Drive copy: SKIPPED - '$driveParent' doesn't exist. Either Google Drive for " +
        "Desktop isn't installed on this PC, or `$driveBackupDir` at the top of this script " +
        "needs to be adjusted to match where it's actually mirroring your Drive to - check " +
        "File Explorer for the real path, then update that one line." | Out-File -Append $logFile
    }
} catch {
    "Google Drive copy: FAILED - $($_.Exception.Message)" | Out-File -Append $logFile
}

# --- 5. Prune backups older than 60 days (both local and Google Drive) ------
try {
    $cutoff = (Get-Date).AddDays(-60)
    Get-ChildItem (Join-Path $root "backups") -Directory | Where-Object {
        $_.Name -match '^\d{4}-\d{2}-\d{2}$' -and [datetime]::ParseExact($_.Name, "yyyy-MM-dd", $null) -lt $cutoff
    } | Remove-Item -Recurse -Force
    if (Test-Path $driveBackupDir) {
        Get-ChildItem $driveBackupDir -Directory | Where-Object {
            $_.Name -match '^\d{4}-\d{2}-\d{2}$' -and [datetime]::ParseExact($_.Name, "yyyy-MM-dd", $null) -lt $cutoff
        } | Remove-Item -Recurse -Force
    }
    "Pruned backups older than 60 days (local and Google Drive)." | Out-File -Append $logFile
} catch {
    "Pruning old backups: FAILED - $($_.Exception.Message)" | Out-File -Append $logFile
}

"Backup finished: $(Get-Date)" | Out-File -Append $logFile
