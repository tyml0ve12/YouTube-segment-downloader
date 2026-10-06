# Script CHO NGUOI PHAT TRIEN (ban) dung - dong goi "Tai Video Youtube Theo
# Doan" tu code moi nhat trong thu muc goc, tao ca bo cai dat LAN goi cap nhat
# (latest.json + app_update.zip) de dang GitHub Release. KHONG gui script nay
# cho nguoi dung cuoi.
#
# Chay: powershell -ExecutionPolicy Bypass -File make_release.ps1 -Notes "Sua loi X, them Y"
# Them -Optional neu muon nguoi dung duoc chon "De sau" thay vi bat buoc cap nhat.

param(
    [string]$Notes = "",
    [switch]$Optional
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$AppName = "TaiVideoYoutubeTheoDoan"

# Doc APP_VERSION tu core/version.py - nguon duy nhat cua so phien ban.
$VersionFileContent = Get-Content (Join-Path $Root "core\version.py") -Raw
if ($VersionFileContent -notmatch 'APP_VERSION\s*=\s*"([^"]+)"') {
    Write-Host "LOI: Khong doc duoc APP_VERSION tu core/version.py" -ForegroundColor Red
    exit 1
}
$Version = $Matches[1]

$GithubRepoMatch = [regex]::Match($VersionFileContent, 'GITHUB_REPO\s*=\s*"([^"]+)"')
$GithubRepo = if ($GithubRepoMatch.Success) { $GithubRepoMatch.Groups[1].Value } else { "" }

Write-Host "Dong goi phien ban: $Version" -ForegroundColor Cyan

# ---------------- Phan 1: Bo cai dat day du (giong truoc, cho nguoi dung moi) ----------------

$DistApp = Join-Path $Root "dist\$AppName"
$FilesDir = Join-Path $DistApp "_files"

# Xoa sach thu muc dong goi cu truoc khi tao lai - tranh con sot file/thu muc
# tu lan build truoc (VD tung bi long 1 ban cu ben trong do build de chong).
if (Test-Path $DistApp) { Remove-Item -Path $DistApp -Recurse -Force }

New-Item -ItemType Directory -Force -Path $FilesDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $FilesDir "core") | Out-Null

# CODE_ITEMS - PHAI khop voi CODE_ITEMS trong core/updater.py va voi
# $filesToCopy/$coreFilesToCopy trong installer/install.ps1.
$RootFiles = @("main.py", "gui.py", "update_ui.py", "YoutubeSegmentDownloader.ico")
$CoreFiles = @("__init__.py", "deps.py", "downloader.py", "version.py", "updater.py")

foreach ($f in $RootFiles) {
    Copy-Item -Path (Join-Path $Root $f) -Destination $FilesDir -Force
}
foreach ($f in $CoreFiles) {
    Copy-Item -Path (Join-Path $Root "core\$f") -Destination (Join-Path $FilesDir "core") -Force
}
Copy-Item -Path (Join-Path $Root "installer\install.ps1") -Destination $FilesDir -Force

$BatSrc = Join-Path $Root "installer\CAI DAT.bat"
if (Test-Path $BatSrc) {
    Copy-Item -Path $BatSrc -Destination $DistApp -Force
}

$GuideSrc = Join-Path $Root "installer\HUONG DAN SU DUNG.txt"
if (Test-Path $GuideSrc) {
    Copy-Item -Path $GuideSrc -Destination $DistApp -Force
}

$InstallerZipPath = Join-Path $Root "dist\${AppName}_v$Version.zip"
if (Test-Path $InstallerZipPath) { Remove-Item $InstallerZipPath -Force }
Compress-Archive -Path $DistApp -DestinationPath $InstallerZipPath

Write-Host "Da dong goi bo cai dat: $InstallerZipPath" -ForegroundColor Green

# ---------------- Phan 2: Goi cap nhat (latest.json + app_update.zip) ----------------

$ReleaseDir = Join-Path $Root "dist\release_v$Version"
if (Test-Path $ReleaseDir) { Remove-Item -Path $ReleaseDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $ReleaseDir "update_staging\core") | Out-Null

$StagingDir = Join-Path $ReleaseDir "update_staging"
foreach ($f in $RootFiles) {
    Copy-Item -Path (Join-Path $Root $f) -Destination $StagingDir -Force
}
foreach ($f in $CoreFiles) {
    Copy-Item -Path (Join-Path $Root "core\$f") -Destination (Join-Path $StagingDir "core") -Force
}

$AppUpdateZip = Join-Path $ReleaseDir "app_update.zip"
# Nen TUNG item rieng vao goc zip (khong boc trong 1 thu muc cha) - dung quy
# uoc "code nam ngay goc zip" cua core/updater.py::_safe_extract.
Compress-Archive -Path (Join-Path $StagingDir "*") -DestinationPath $AppUpdateZip
Remove-Item -Path $StagingDir -Recurse -Force

$Sha256 = (Get-FileHash -Path $AppUpdateZip -Algorithm SHA256).Hash.ToLower()
Write-Host "SHA256 cua app_update.zip: $Sha256" -ForegroundColor Cyan

if (-not $GithubRepo) {
    Write-Host "CANH BAO: GITHUB_REPO chua duoc dien trong core/version.py - bo qua tao latest.json." -ForegroundColor Yellow
} else {
    $DownloadUrl = "https://github.com/$GithubRepo/releases/download/v$Version/app_update.zip"
    $LatestJson = [ordered]@{
        version   = $Version
        notes     = $Notes
        url       = $DownloadUrl
        sha256    = $Sha256
        mandatory = -not $Optional.IsPresent
    }
    $LatestJsonPath = Join-Path $ReleaseDir "latest.json"
    # Dung UTF8Encoding($false) de KHONG ghi BOM dau file - neu co BOM,
    # json.loads() ben phia core/updater.py se bao loi "Expecting value".
    $Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($LatestJsonPath, ($LatestJson | ConvertTo-Json), $Utf8NoBom)
    Write-Host "Da tao: $LatestJsonPath" -ForegroundColor Green
}

Copy-Item -Path $InstallerZipPath -Destination $ReleaseDir -Force

Write-Host ""
Write-Host "=== HOAN TAT ===" -ForegroundColor Green
Write-Host "Thu muc release (3 file can dang GitHub Release): $ReleaseDir"
Write-Host "  - latest.json"
Write-Host "  - app_update.zip"
Write-Host "  - ${AppName}_v$Version.zip"

# ---------------- Phan 3: Tu tao GitHub Release (neu co gh CLI) ----------------

$GhCmd = Get-Command gh -ErrorAction SilentlyContinue
if ($GhCmd -and $GithubRepo) {
    $Answer = Read-Host "Da dang nhap gh CLI - tu tao GitHub Release v$Version cho repo $GithubRepo? (y/N)"
    if ($Answer -eq "y" -or $Answer -eq "Y") {
        $ReleaseFiles = @(
            (Join-Path $ReleaseDir "latest.json"),
            (Join-Path $ReleaseDir "app_update.zip"),
            (Join-Path $ReleaseDir "${AppName}_v$Version.zip")
        )
        $NotesArg = if ($Notes) { $Notes } else { "Phien ban $Version" }
        gh release create "v$Version" $ReleaseFiles --repo $GithubRepo --title "v$Version" --notes "$NotesArg"
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Da tao GitHub Release v$Version thanh cong." -ForegroundColor Green
        } else {
            Write-Host "Tao GitHub Release that bai (xem loi o tren)." -ForegroundColor Red
        }
    }
} else {
    Write-Host ""
    Write-Host "Khong co gh CLI hoac chua dien GITHUB_REPO - tu dang 3 file tren len:" -ForegroundColor Yellow
    Write-Host "  github.com/$GithubRepo/releases/new -> tag v$Version -> keo tha 3 file -> Publish"
}
