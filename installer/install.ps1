# Cai dat "Tai Video Youtube Theo Doan" - khong dung exe tu build (PyInstaller),
# tranh bi Smart App Control / Windows Defender chan vi "unknown publisher,
# no reputation".
#
# Co che: neu may DA CO SAN 1 ban Python (bat ky ban nao) kem tkinter chay
# duoc, DUNG LUON ban do - khong cai gi ca. Chi khi may chua co Python nao
# dung duoc moi tai + cai 1 ban rieng (chinh chu tu python.org) cho tool nay.
# Sau do tao shortcut chay thang qua pythonw.exe. Vi pythonw.exe la binary da
# ky so + co san reputation toan cau, Windows se KHONG coi day la "unknown
# app" o nhung lan chay sau.
#
# Idempotent: chay lai script nay (vd khi cap nhat code) se BO QUA buoc tai/cai
# Python neu da co san (o bat ky dau), chi copy lai code moi + tao lai shortcut.

$ErrorActionPreference = "Stop"

$PythonVersion = "3.12.6"
$PythonInstallerUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe"

$AppRoot = Join-Path $env:LOCALAPPDATA "TaiVideoYoutubeTheoDoan"
$PyDir = Join-Path $AppRoot "python"
$AppDir = Join-Path $AppRoot "app"
$PythonwExe = Join-Path $PyDir "pythonw.exe"

# Code nguon (main.py, gui.py, core\, icon) nam CUNG thu muc voi chinh script
# nay (thu muc "_files" khi dong goi phat hanh - xem README).
$SourceDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Step($msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

# ---------------- Buoc 1: Cai Python (chi khi may chua co ban nao dung duoc) ----------------
#
# Ly do lam don gian nhu the nay (thay vi tu cai rieng 1 ban co dinh): Windows
# Installer theo doi "da cai Python 3.12" nhu 1 SAN PHAM DUY NHAT bat ke
# TargetDir minh chi dinh khac - nen khi co gang cai them 1 ban rieng vao thu
# muc khac trong khi may da co san 1 ban Python cung nhanh (vd "JustForMe"),
# installer coi day la "khong co gi de lam" va KHONG COPY FILE NAO CA, du van
# bao "thanh cong" (exit code 0). Cach chac chan tranh loi nay: don gian la
# DUNG BAT KY BAN PYTHON NAO da co san chay duoc tkinter tren may, khong cai
# chong len nhau.

function Test-TkinterWorks($pythonExePath) {
    if (-not (Test-Path $pythonExePath)) { return $false }
    try {
        & $pythonExePath -c "import tkinter" *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

# Doc registry de tim MOI ban Python da duoc Windows Installer ghi nhan cho
# user hien tai, BAT KE cai o thu muc nao - quan trong vi nhieu tool khac
# (vd "CapCut Auto Ducking") cung tu cai rieng 1 ban Python vao thu muc rieng
# cua no (khong nam trong "Programs\Python" tieu chuan), nen chi do theo
# duong dan thu muc pho bien se BO SOT cac ban nay. Windows Installer luon
# ghi InstallPath that vao registry bat ke TargetDir la gi, nen day la cach
# CHAC CHAN nhat de tim moi ban Python thuc su co tren may.
function Get-RegisteredPythonInstallPaths {
    $paths = New-Object System.Collections.Generic.List[string]
    foreach ($root in @(
        "HKCU:\Software\Python\PythonCore",
        "HKLM:\Software\Python\PythonCore",
        "HKLM:\Software\WOW6432Node\Python\PythonCore"
    )) {
        if (-not (Test-Path $root)) { continue }
        Get-ChildItem -Path $root -ErrorAction SilentlyContinue | ForEach-Object {
            $installPathKey = Join-Path $_.PSPath "InstallPath"
            if (Test-Path $installPathKey) {
                $val = (Get-ItemProperty -Path $installPathKey -ErrorAction SilentlyContinue).'(default)'
                if ($val) { $paths.Add($val.TrimEnd('\')) }
            }
        }
    }
    return $paths
}

function Find-UsablePythonw {
    $candidateDirs = New-Object System.Collections.Generic.List[string]

    # Python rieng cua tool nay tu lan cai truoc (neu co)
    $candidateDirs.Add($PyDir)

    # Python co san trong PATH he thong (cach pho bien nhat)
    foreach ($cmdName in @("python.exe", "pythonw.exe")) {
        $cmd = Get-Command $cmdName -ErrorAction SilentlyContinue
        if ($cmd) { $candidateDirs.Add((Split-Path $cmd.Source -Parent)) }
    }

    # Cac vi tri cai Python pho bien (du khong co trong PATH)
    $commonRoots = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python"),
        (Join-Path $env:LOCALAPPDATA "Programs\PythonSoftwareFoundation"),
        "C:\Python3*",
        (Join-Path ${env:ProgramFiles} "Python3*"),
        (Join-Path ${env:ProgramFiles(x86)} "Python3*")
    )
    foreach ($root in $commonRoots) {
        $matches = Get-ChildItem -Path $root -Directory -Filter "Python3*" -ErrorAction SilentlyContinue
        if (-not $matches) { $matches = Get-ChildItem -Path $root -Directory -ErrorAction SilentlyContinue }
        foreach ($m in $matches) { $candidateDirs.Add($m.FullName) }
    }

    # Bat ky ban Python nao khac tool da cai rieng (vd CapCut Auto Ducking) -
    # doc thang tu registry, khong doan theo ten thu muc (xem ham o tren).
    foreach ($p in (Get-RegisteredPythonInstallPaths)) { $candidateDirs.Add($p) }

    $checked = New-Object System.Collections.Generic.HashSet[string]
    foreach ($dir in $candidateDirs) {
        if (-not $dir -or $checked.Contains($dir)) { continue }
        $checked.Add($dir) | Out-Null
        $pw = Join-Path $dir "pythonw.exe"
        $p = Join-Path $dir "python.exe"
        if ((Test-Path $pw) -and (Test-TkinterWorks $p)) {
            return $pw
        }
    }
    return $null
}

$usablePythonw = Find-UsablePythonw

if ($usablePythonw) {
    Write-Step "May da co san Python kem tkinter tai '$usablePythonw' - dung luon, khong can cai gi them."
    $PythonwExe = $usablePythonw
} else {
    # Chi ban Python DUNG 3.12 (trung voi $PythonVersion se tai) moi an toan de
    # "Modify" (sua doi) tai cho - vi bo cai dat 3.12.6 chi biet sua doi san
    # pham 3.12, KHONG the dung de bo sung tcltk cho 1 ban Python khac version
    # (vd 3.11, 3.13) ma khong lam hong ban do.
    $existingPy312 = Get-RegisteredPythonInstallPaths | Where-Object {
        Test-Path (Join-Path $_ "python.exe")
    } | Where-Object {
        (& (Join-Path $_ "python.exe") --version 2>&1) -match "Python 3\.12\."
    } | Select-Object -First 1

    if ($existingPy312) {
        $InstallTargetDir = $existingPy312
        Write-Step "May da co Python 3.12 tai '$existingPy312' nhung thieu tkinter - dang bo sung tcltk vao ban co san (khong cai ban moi de tranh xung dot voi Windows Installer)..."
    } else {
        $InstallTargetDir = $PyDir
        Write-Step "Chua tim thay Python nao - dang tai bo cai dat chinh chu tu python.org (khoang 25MB)..."
    }

    New-Item -ItemType Directory -Force -Path $AppRoot | Out-Null
    $InstallerPath = Join-Path $env:TEMP "python-$PythonVersion-amd64.exe"
    try {
        Invoke-WebRequest -Uri $PythonInstallerUrl -OutFile $InstallerPath -UseBasicParsing
    } catch {
        Write-Host "LOI: Khong tai duoc bo cai Python. Kiem tra ket noi internet roi thu lai." -ForegroundColor Red
        Write-Host $_.Exception.Message -ForegroundColor Red
        exit 1
    }

    $LogPath = Join-Path $env:TEMP "tai_video_youtube_theo_doan_python_install.log"

    Write-Step "Dang cai dat (khong can quyen admin, khong anh huong Python khac tren may)..."
    # InstallAllUsers=0        -> cai cho user hien tai, KHONG can admin
    # TargetDir=$InstallTargetDir -> thu muc rieng cua tool, HOAC dung lai
    #                                thu muc Python 3.12 da co san (xem trên)
    # PrependPath=0            -> KHONG them vao PATH he thong
    # Include_launcher=0       -> khong can "py launcher" dung chung toan may
    # Include_test=0           -> bot dung luong, khong can bo test cua Python
    # Include_tcltk=1          -> BAT BUOC, GUI dung tkinter can cai nay
    # Include_pip=0            -> tool khong dung package ngoai nao, khong can pip
    # Gia tri chua duong dan (TargetDir) phai tu boc ngoac kep rieng, vi
    # Start-Process ghep cac phan tu ArgumentList bang khoang trang don thuan.
    $installArgs = @(
        "/quiet",
        "/log", "`"$LogPath`"",
        "InstallAllUsers=0",
        "TargetDir=`"$InstallTargetDir`"",
        "PrependPath=0",
        "Include_launcher=0",
        "Include_test=0",
        "Include_tcltk=1",
        "Include_pip=0"
    )
    $proc = Start-Process -FilePath $InstallerPath -ArgumentList $installArgs -Wait -PassThru
    Remove-Item $InstallerPath -ErrorAction SilentlyContinue

    $PythonwExe = Join-Path $InstallTargetDir "pythonw.exe"
    $PythonExe = Join-Path $InstallTargetDir "python.exe"

    if ($proc.ExitCode -ne 0 -or -not (Test-Path $PythonwExe) -or -not (Test-TkinterWorks $PythonExe)) {
        Write-Host "LOI: Cai Python that bai (exit code $($proc.ExitCode))." -ForegroundColor Red
        Write-Host "Chi tiet log cai dat: $LogPath" -ForegroundColor Yellow
        Write-Host "Cach sua thu cong: vao Settings > Apps > Installed apps, tim 'Python $PythonVersion'," -ForegroundColor Yellow
        Write-Host "bam 'Modify' (Sua doi), tich chon 'tcl/tk and IDLE' roi cai lai, sau do chay lai file nay." -ForegroundColor Yellow
        exit 1
    }
    Write-Host "Cai dat thanh cong tai '$InstallTargetDir'." -ForegroundColor Green
}

# ---------------- Buoc 2: Copy code vao noi cai dat ----------------

Write-Step "Dang sao chep code tool vao '$AppDir'..."
New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $AppDir "core") | Out-Null

$filesToCopy = @("main.py", "gui.py", "update_ui.py", "YoutubeSegmentDownloader.ico")
foreach ($f in $filesToCopy) {
    $src = Join-Path $SourceDir $f
    if (-not (Test-Path $src)) {
        Write-Host "LOI: Khong tim thay '$src'. Kiem tra lai cau truc thu muc phat hanh." -ForegroundColor Red
        exit 1
    }
    Copy-Item -Path $src -Destination $AppDir -Force
}

$coreFilesToCopy = @("__init__.py", "deps.py", "downloader.py", "version.py", "updater.py")
foreach ($f in $coreFilesToCopy) {
    $src = Join-Path $SourceDir "core\$f"
    if (-not (Test-Path $src)) {
        Write-Host "LOI: Khong tim thay '$src'. Kiem tra lai cau truc thu muc phat hanh." -ForegroundColor Red
        exit 1
    }
    Copy-Item -Path $src -Destination (Join-Path $AppDir "core") -Force
}
Write-Host "Da sao chep xong." -ForegroundColor Green

# File danh dau: app CHI tu cap nhat (tu ghi de code) khi co file nay - tranh
# ghi de thu muc ma nguon luc dang phat trien neu lo chay nham tu do.
New-Item -ItemType File -Force -Path (Join-Path $AppDir ".installed") | Out-Null

# ---------------- Buoc 3: Tao shortcut (Desktop + Start Menu) ----------------

Write-Step "Dang tao shortcut..."

function New-AppShortcut($shortcutPath) {
    $wshell = New-Object -ComObject WScript.Shell
    $shortcut = $wshell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $PythonwExe
    $shortcut.Arguments = "`"$AppDir\main.py`""
    $shortcut.WorkingDirectory = $AppDir
    $shortcut.Description = "Tai Video Youtube Theo Doan - Hoang Duc - Brightstar"
    $iconPath = Join-Path $AppDir "YoutubeSegmentDownloader.ico"
    if (Test-Path $iconPath) {
        $shortcut.IconLocation = $iconPath
    }
    $shortcut.Save()
}

$desktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "Tai Video Youtube Theo Doan.lnk"
New-AppShortcut $desktopShortcut

$startMenuDir = Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs"
$startMenuShortcut = Join-Path $startMenuDir "Tai Video Youtube Theo Doan.lnk"
New-AppShortcut $startMenuShortcut

Write-Host "Da tao shortcut tai Desktop va Start Menu." -ForegroundColor Green

Write-Step "HOAN TAT! Mo 'Tai Video Youtube Theo Doan' tu Desktop hoac Start Menu de dung."
