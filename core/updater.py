"""Logic tự cập nhật qua GitHub Releases - không phụ thuộc giao diện (dùng
chung được cho tkinter hay bất kỳ UI nào khác). Xem HUONG_DAN_BUILD_VA_UPDATE.md
để biết đầy đủ quy ước.

Luồng: app đọc `latest.json` cố định tại
https://github.com/<repo>/releases/latest/download/latest.json (link này luôn
trỏ release mới nhất, không bị giới hạn API như GitHub API thường) -> so sánh
version -> nếu mới hơn thì tải `app_update.zip`, kiểm tra SHA256, sao lưu code
cũ, thay code mới, khởi động lại app. Lỗi ở bất kỳ bước nào -> khôi phục bản cũ,
không bao giờ để app ở trạng thái dở dang.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

from core.version import APP_VERSION, GITHUB_REPO

# Các file/thư mục hợp thành "code" của app - duoc thay the khi cap nhat.
# KHONG bao gom bin/ (ffmpeg/yt-dlp/deno, nang, giu nguyen tren may) hay
# logs/.installed (trang thai rieng cua tung may).
CODE_ITEMS = ["main.py", "gui.py", "update_ui.py", "YoutubeSegmentDownloader.ico", "core"]

ProgressCb = Optional[Callable[[str], None]]


class UpdateInfo:
    def __init__(self, data: dict) -> None:
        self.version: str = str(data.get("version", ""))
        self.notes: str = str(data.get("notes", ""))
        self.url: str = str(data.get("url", ""))
        self.sha256: str = str(data.get("sha256", ""))
        self.mandatory: bool = bool(data.get("mandatory", True))


def _version_tuple(v: str) -> tuple[int, ...]:
    parts = []
    for p in v.strip().split("."):
        digits = "".join(c for c in p if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def is_newer(remote_version: str, local_version: str = APP_VERSION) -> bool:
    return _version_tuple(remote_version) > _version_tuple(local_version)


def check_for_update(timeout: int = 10) -> Optional[UpdateInfo]:
    """Trả về UpdateInfo nếu có bản mới hơn, None nếu đã mới nhất hoặc không
    kiểm tra được (không mạng, repo chưa có release, v.v. - KHÔNG raise lỗi,
    vì không được phép chặn app mở bình thường khi offline)."""
    if not GITHUB_REPO:
        return None
    url = f"https://github.com/{GITHUB_REPO}/releases/latest/download/latest.json"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None

    info = UpdateInfo(data)
    if not info.version or not is_newer(info.version):
        return None
    return info


def _is_installed_app(app_dir: Path) -> bool:
    """Chỉ cho phép tự ghi đè code khi app được cài qua CAI DAT.bat (có file
    đánh dấu .installed) - tránh ghi đè thư mục mã nguồn lúc đang phát triển."""
    return (app_dir / ".installed").exists()


def apply_update(info: UpdateInfo, app_dir: Path, log_cb: ProgressCb = None) -> None:
    """Tải + áp dụng bản cập nhật. Raise Exception nếu thất bại ở bất kỳ bước
    nào (caller nên bắt lỗi và báo người dùng), nhưng LUÔN khôi phục code cũ
    trước khi raise - không bao giờ để app ở trạng thái code dở dang."""

    def log(msg: str) -> None:
        if log_cb:
            log_cb(msg)

    if not _is_installed_app(app_dir):
        raise RuntimeError(
            "Thư mục này không phải bản đã cài qua CAI DAT.bat (thiếu file .installed) - "
            "từ chối tự ghi đè để tránh mất code đang phát triển."
        )
    if not info.url or not info.sha256:
        raise RuntimeError("Thông tin bản cập nhật thiếu URL hoặc SHA256.")

    with tempfile.TemporaryDirectory(prefix="ysd_update_") as tmp:
        tmp_dir = Path(tmp)
        zip_path = tmp_dir / "app_update.zip"

        log("Đang tải bản cập nhật...")
        _download_with_progress(info.url, zip_path, log)

        log("Đang kiểm tra SHA256...")
        actual_sha = _sha256_of(zip_path)
        if actual_sha.lower() != info.sha256.lower():
            raise RuntimeError(f"SHA256 không khớp (tải bị lỗi/bị can thiệp). Mong đợi {info.sha256}, nhận {actual_sha}.")

        staging_dir = tmp_dir / "staging"
        staging_dir.mkdir()
        log("Đang giải nén...")
        _safe_extract(zip_path, staging_dir)

        backup_dir = app_dir.parent / f"{app_dir.name}_backup"
        if backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)
        backup_dir.mkdir()

        log("Đang sao lưu code hiện tại...")
        for item in CODE_ITEMS:
            src = app_dir / item
            if src.exists():
                dst = backup_dir / item
                if src.is_dir():
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)

        try:
            log("Đang thay code mới...")
            for item in CODE_ITEMS:
                target = app_dir / item
                source = staging_dir / item
                if not source.exists():
                    continue  # item nay khong doi trong ban cap nhat nay
                if target.exists():
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                if source.is_dir():
                    shutil.copytree(source, target)
                else:
                    shutil.copy2(source, target)
        except Exception:
            log("Lỗi khi thay code - đang khôi phục bản cũ...")
            _restore_backup(app_dir, backup_dir)
            raise

    log(f"Cập nhật thành công lên phiên bản {info.version}.")


def _restore_backup(app_dir: Path, backup_dir: Path) -> None:
    for item in CODE_ITEMS:
        target = app_dir / item
        source = backup_dir / item
        if not source.exists():
            continue
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)


def _download_with_progress(url: str, dest: Path, log_cb: ProgressCb) -> None:
    with urllib.request.urlopen(url) as resp, open(dest, "wb") as out:
        total = int(resp.headers.get("Content-Length", 0))
        read = 0
        chunk = 1024 * 256
        last_pct = -1
        while True:
            data = resp.read(chunk)
            if not data:
                break
            out.write(data)
            read += len(data)
            if total:
                pct = read * 100 // total
                if pct != last_pct:
                    last_pct = pct
                    if log_cb:
                        log_cb(f"  Đang tải: {pct}%")


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 256), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_extract(zip_path: Path, dest: Path) -> None:
    """Giải nén nhưng chặn path traversal (entry chứa '..' thoát ra ngoài
    thư mục đích) - phòng trường hợp file zip bị can thiệp."""
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            member_path = (dest / member).resolve()
            if not str(member_path).startswith(str(dest.resolve())):
                raise RuntimeError(f"File zip chứa đường dẫn không an toàn: {member}")
        zf.extractall(dest)


def restart_app(app_dir: Path) -> None:
    """Khởi động lại app bằng pythonw.exe hiện tại (giữ nguyên hành vi không
    hiện console), rồi thoát tiến trình hiện tại."""
    pythonw = Path(sys.executable)
    if pythonw.name.lower() == "python.exe":
        candidate = pythonw.with_name("pythonw.exe")
        if candidate.exists():
            pythonw = candidate

    creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    subprocess.Popen(
        [str(pythonw), str(app_dir / "main.py")],
        cwd=str(app_dir),
        creationflags=creationflags,
    )
    os._exit(0)
