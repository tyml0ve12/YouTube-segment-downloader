"""Tìm và tự động tải yt-dlp.exe / ffmpeg.exe nếu máy chưa có."""
from __future__ import annotations

import os
import shutil
import stat
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

BIN_DIR = Path(__file__).resolve().parent.parent / "bin"

YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
FFMPEG_ZIP_NAME = "ffmpeg-master-latest-win64-gpl-shared.zip"
FFMPEG_URL = f"https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/{FFMPEG_ZIP_NAME}"
DENO_ZIP_NAME = "deno-x86_64-pc-windows-msvc.zip"
DENO_URL = f"https://github.com/denoland/deno/releases/latest/download/{DENO_ZIP_NAME}"

ProgressCb = Optional[Callable[[str], None]]


def _log(cb: ProgressCb, msg: str) -> None:
    if cb:
        cb(msg)


def _find_on_path(name: str) -> Optional[Path]:
    found = shutil.which(name)
    return Path(found) if found else None


def find_ytdlp() -> Optional[Path]:
    local = BIN_DIR / "yt-dlp.exe"
    if local.exists():
        return local
    return _find_on_path("yt-dlp.exe") or _find_on_path("yt-dlp")


def find_ffmpeg() -> Optional[Path]:
    local = BIN_DIR / "ffmpeg.exe"
    if local.exists() and (BIN_DIR / "ffprobe.exe").exists():
        return local
    return _find_on_path("ffmpeg.exe") or _find_on_path("ffmpeg")


def find_deno() -> Optional[Path]:
    """yt-dlp tự nhận deno nếu nó nằm CÙNG thư mục với yt-dlp.exe (hoặc trong
    PATH) - không cần cấu hình gì thêm, xem github.com/yt-dlp/yt-dlp/wiki/EJS."""
    local = BIN_DIR / "deno.exe"
    if local.exists():
        return local
    return _find_on_path("deno.exe") or _find_on_path("deno")


def _download(url: str, dest: Path, cb: ProgressCb) -> None:
    _log(cb, f"Đang tải {url} ...")
    with urllib.request.urlopen(url) as resp, open(dest, "wb") as out:
        total = int(resp.headers.get("Content-Length", 0))
        read = 0
        chunk = 1024 * 256
        while True:
            data = resp.read(chunk)
            if not data:
                break
            out.write(data)
            read += len(data)
            if total:
                _log(cb, f"  {url.split('/')[-1]}: {read * 100 // total}%")


def download_ytdlp(cb: ProgressCb = None) -> Path:
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    dest = BIN_DIR / "yt-dlp.exe"
    _download(YTDLP_URL, dest, cb)
    dest.chmod(dest.stat().st_mode | stat.S_IEXEC)
    _log(cb, f"Đã tải yt-dlp về {dest}")
    return dest


def download_ffmpeg(cb: ProgressCb = None) -> Path:
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    archive = BIN_DIR / FFMPEG_ZIP_NAME
    _download(FFMPEG_URL, archive, cb)

    _log(cb, "Đang giải nén FFmpeg...")
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            name = info.filename
            base = os.path.basename(name)
            if not base:
                continue
            if base.endswith(".exe") or base.endswith(".dll") or "LICENSE" in base:
                target = BIN_DIR / base
                with zf.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)

    archive.unlink(missing_ok=True)

    dest = BIN_DIR / "ffmpeg.exe"
    if not dest.exists():
        raise RuntimeError("Giải nén FFmpeg xong nhưng không tìm thấy ffmpeg.exe")
    _log(cb, f"Đã cài FFmpeg vào {BIN_DIR}")
    return dest


def download_deno(cb: ProgressCb = None) -> Path:
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    archive = BIN_DIR / DENO_ZIP_NAME
    _download(DENO_URL, archive, cb)

    _log(cb, "Đang giải nén Deno...")
    with zipfile.ZipFile(archive) as zf:
        zf.extract("deno.exe", BIN_DIR)
    archive.unlink(missing_ok=True)

    dest = BIN_DIR / "deno.exe"
    if not dest.exists():
        raise RuntimeError("Giải nén Deno xong nhưng không tìm thấy deno.exe")
    _log(cb, f"Đã tải Deno về {dest}")
    return dest


def ensure_dependencies(cb: ProgressCb = None) -> tuple[Path, Path, Optional[Path]]:
    """Trả về (ytdlp_path, ffmpeg_path, deno_path), tự động tải nếu thiếu.

    deno_path có thể là None nếu tải thất bại (không chặn ứng dụng chạy tiếp -
    thiếu Deno chỉ khiến yt-dlp không giải được "n challenge" của Youtube cho
    một số video, chứ không phải lỗi nghiêm trọng như thiếu yt-dlp/FFmpeg)."""
    ytdlp = find_ytdlp()
    if ytdlp is None:
        _log(cb, "Không tìm thấy yt-dlp, bắt đầu tải...")
        ytdlp = download_ytdlp(cb)
    else:
        _log(cb, f"Đã có yt-dlp tại {ytdlp}")

    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        _log(cb, "Không tìm thấy FFmpeg, bắt đầu tải...")
        ffmpeg = download_ffmpeg(cb)
    else:
        _log(cb, f"Đã có FFmpeg tại {ffmpeg}")

    deno = find_deno()
    if deno is None:
        _log(cb, "Không tìm thấy Deno (cần để giải mã Youtube), bắt đầu tải...")
        try:
            deno = download_deno(cb)
        except Exception as exc:  # noqa: BLE001
            _log(cb, f"Tải Deno thất bại (không chặn ứng dụng, nhưng một số video có thể lỗi): {exc}")
            deno = None
    else:
        _log(cb, f"Đã có Deno tại {deno}")

    return ytdlp, ffmpeg, deno
