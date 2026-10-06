"""Logic tải + cắt đoạn video, port từ Download.cs (bản C#)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

PROGRESS_RE = re.compile(r"^\[download\]\s+(\d+\.\d+)%")
FFMPEG_TIME_RE = re.compile(r"time=(-?)(\d+):(\d+):(\d+\.\d+)")
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

LogCb = Optional[Callable[[str], None]]
ProgressCb = Optional[Callable[[float], None]]

# Thử lần lượt GPU (NVIDIA/AMD/Intel) trước, cuối cùng mới rơi về CPU (libx264).
# Chỉ là danh sách ứng viên: máy không có GPU tương ứng (hoặc driver không khớp
# bản FFmpeg) thì lệnh sẽ lỗi ngay và tự động rơi xuống ứng viên kế tiếp trong
# _cut_with_ffmpeg - nvenc (GPU rời NVIDIA) được thử trước AMD/Intel vì đó
# thường là GPU rời trên máy có nhiều GPU. Ưu tiên preset nhanh nhất của từng
# encoder vì bước này chỉ cắt vài chục giây, tốc độ quan trọng hơn nén tối ưu.
_ENCODER_CANDIDATES: list[tuple[str, list[str]]] = [
    ("h264_nvenc", ["-preset", "p1", "-tune", "hq", "-rc", "vbr", "-cq", "18", "-b:v", "0"]),
    ("h264_amf", ["-quality", "speed", "-rc", "cqp", "-qp_i", "18", "-qp_p", "18"]),
    ("h264_qsv", ["-preset", "veryfast", "-global_quality", "18"]),
    ("libx264", ["-preset", "veryfast", "-crf", "18"]),
]

_available_encoders_cache: dict[str, set[str]] = {}


def _gpu_cache_file(ffmpeg_path: Path) -> Path:
    return Path(ffmpeg_path).parent / "gpu_check_cache.json"


def _load_gpu_disk_cache(ffmpeg_path: Path) -> Optional[set[str]]:
    """Đọc kết quả dò GPU đã lưu từ lần mở app trước - chỉ dùng lại nếu
    ffmpeg.exe không đổi (so mtime+size) kể từ lúc lưu, để tự làm mới khi
    ffmpeg được tải lại (vd sau khi bấm "Tải lại yt-dlp / FFmpeg")."""
    cache_file = _gpu_cache_file(ffmpeg_path)
    try:
        stat = Path(ffmpeg_path).stat()
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        if data.get("mtime") == stat.st_mtime and data.get("size") == stat.st_size:
            return set(data.get("working_encoders", []))
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return None


def _save_gpu_disk_cache(ffmpeg_path: Path, working: set[str]) -> None:
    try:
        stat = Path(ffmpeg_path).stat()
        cache_file = _gpu_cache_file(ffmpeg_path)
        cache_file.write_text(
            json.dumps({"mtime": stat.st_mtime, "size": stat.st_size, "working_encoders": sorted(working)}),
            encoding="utf-8",
        )
    except OSError:
        pass


_ENCODER_VENDOR = {"nvenc": "nvidia", "qsv": "intel", "amf": "amd"}


def _encoder_vendor(encoder_name: str) -> str:
    for key, vendor in _ENCODER_VENDOR.items():
        if key in encoder_name:
            return vendor
    return ""  # CPU (libx264) - không thuộc hãng GPU nào


def _list_compiled_encoders(ffmpeg_path: Path) -> set[str]:
    """Encoder có trong bản FFmpeg này (chỉ nghĩa là ffmpeg biết TÊN encoder,
    không có nghĩa là chạy được trên máy - GPU không tồn tại hoặc driver quá
    cũ vẫn có thể khiến nó thất bại khi chạy thật, xem _encoder_actually_works)."""
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        result = subprocess.run(
            [str(ffmpeg_path), "-hide_banner", "-encoders"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15, creationflags=creationflags,
        )
        return {codec for codec, _ in _ENCODER_CANDIDATES if codec in result.stdout}
    except (OSError, subprocess.TimeoutExpired):
        return set()


def _detect_gpu_vendors() -> set[str]:
    """Dò các hãng GPU THỰC SỰ có trên máy (qua WMI), không chỉ dựa vào việc
    FFmpeg có biên dịch sẵn encoder đó hay không - bản FFmpeg tải sẵn thường có
    cả 3 encoder GPU (nvenc/qsv/amf) bất kể máy có phần cứng tương ứng hay
    không, nên phải lọc trước để tránh phí thời gian thử encoder của hãng GPU
    không tồn tại trên máy."""
    vendors: set[str] = set()
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-CimInstance Win32_VideoController).Name"],
            capture_output=True, text=True, timeout=10, creationflags=creationflags,
        )
        names = (result.stdout or "").lower()
        if "nvidia" in names:
            vendors.add("nvidia")
        if "intel" in names:
            vendors.add("intel")
        if "amd" in names or "radeon" in names:
            vendors.add("amd")
    except (OSError, subprocess.TimeoutExpired):
        pass
    return vendors


def _encoder_actually_works(ffmpeg_path: Path, encoder_name: str) -> bool:
    """Thử render THẬT 4 khung hình nhỏ (256x256, không nhỏ hơn vì NVENC từ
    chối khung hình dưới một ngưỡng tối thiểu) bằng encoder này, thay vì chỉ
    tin FFmpeg "có biên dịch encoder" hay "máy có GPU hãng X". Đây là cách duy
    nhất phát hiện đúng các lỗi chỉ xảy ra khi chạy thật: driver GPU quá cũ so
    với NVENC/QSV cần, thiếu runtime DLL... Chỉ mất ~0.2-0.5 giây/encoder."""
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        result = subprocess.run(
            [str(ffmpeg_path), "-hide_banner", "-v", "error",
             "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.2",
             "-frames:v", "4", "-c:v", encoder_name, "-f", "null", "-"],
            capture_output=True, text=True, timeout=15, creationflags=creationflags,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _detect_working_encoders(ffmpeg_path: Path) -> set[str]:
    """Danh sách encoder THỰC SỰ chạy được trên máy này (đã cache theo đường
    dẫn ffmpeg) - kết hợp cả biên dịch sẵn, hãng GPU có thật, và test render
    thật để bắt được các lỗi driver/API-version khác nhau giữa từng máy."""
    key = str(ffmpeg_path)
    if key in _available_encoders_cache:
        return _available_encoders_cache[key]

    from_disk = _load_gpu_disk_cache(ffmpeg_path)
    if from_disk is not None:
        _available_encoders_cache[key] = from_disk
        return from_disk

    compiled = _list_compiled_encoders(ffmpeg_path)
    gpu_vendors = _detect_gpu_vendors()
    vendor_filtered = {
        name for name in compiled
        if not _encoder_vendor(name) or _encoder_vendor(name) in gpu_vendors
    }

    gpu_candidates = [n for n in vendor_filtered if _encoder_vendor(n)]
    cpu_only = vendor_filtered - set(gpu_candidates)
    working_gpu: set[str] = set()
    if gpu_candidates:
        with ThreadPoolExecutor(max_workers=len(gpu_candidates)) as pool:
            results = pool.map(lambda n: (n, _encoder_actually_works(ffmpeg_path, n)), gpu_candidates)
            working_gpu = {name for name, ok in results if ok}

    found = cpu_only | working_gpu
    _available_encoders_cache[key] = found
    _save_gpu_disk_cache(ffmpeg_path, found)
    return found


_VENDOR_LABEL = {"nvidia": "NVIDIA", "amd": "AMD", "intel": "Intel"}

# Trang tải driver chính thức của từng hãng - dùng để đưa link 1-click cập
# nhật driver ngay trên giao diện khi phát hiện GPU nhưng driver chưa đủ mới.
_VENDOR_DRIVER_URL = {
    "nvidia": "https://www.nvidia.com/Download/index.aspx",
    "amd": "https://www.amd.com/en/support",
    "intel": "https://www.intel.com/content/www/us/en/support/detect.html",
}


def describe_gpu_status(ffmpeg_path: Path) -> tuple[str, Optional[str]]:
    """Câu trạng thái GPU ngắn gọn để hiện ngay khi mở app (không cần đợi tải
    video) - áp dụng được cho MỌI máy, vì luôn dựa trên test render thật thay
    vì giả định phần cứng/driver giống nhau giữa các máy.

    Trả về (thông_điệp, link_tải_driver_hoặc_None)."""
    working = _detect_working_encoders(ffmpeg_path)
    gpu_vendors = _detect_gpu_vendors()
    working_gpu_encoders = [n for n in working if _encoder_vendor(n)]

    if working_gpu_encoders:
        vendor = _encoder_vendor(working_gpu_encoders[0])
        return f"✓ Tăng tốc GPU: {_VENDOR_LABEL.get(vendor, vendor)} ({working_gpu_encoders[0]})", None

    if gpu_vendors:
        names = ", ".join(_VENDOR_LABEL.get(v, v) for v in sorted(gpu_vendors))
        message = (f"⚠ Card đồ họa {names} của bạn cần cập nhật driver để tải video nhanh hơn. "
                   f"Hiện app vẫn chạy bình thường nhưng chậm hơn.")
        primary_vendor = sorted(gpu_vendors)[0]
        return message, _VENDOR_DRIVER_URL.get(primary_vendor)

    return "ℹ Không phát hiện GPU rời trên máy này - dùng CPU.", None


class DownloadError(Exception):
    pass


class DownloadCancelled(DownloadError):
    pass


class CookieError(DownloadError):
    """Lỗi liên quan tới việc đọc/xác thực cookie trình duyệt - GUI bắt riêng
    lỗi này để chủ động đề nghị người dùng nhập file cookies.txt thủ công."""


@dataclass
class DownloadRequest:
    url_or_id: str
    start: float = 0.0
    end: float = 0.0
    output_dir: Path = Path(".")
    fmt: str = ""
    sort: str = ""
    browser: str = ""
    cookies_file: str = ""


def fetch_max_height(ytdlp_path: Path, ffmpeg_path: Path, url_or_id: str, browser: str = "") -> Optional[int]:
    """Trả về độ phân giải (chiều cao, px) cao nhất mà video gốc thực sự có, hoặc None nếu không xác định được."""
    link = url_or_id if "/" in url_or_id else f"https://youtu.be/{url_or_id}"
    args = [
        str(ytdlp_path), "-j", "--no-check-certificates",
        "--extractor-args", "youtube:skip=dash",
        "--ffmpeg-location", str(Path(ffmpeg_path).parent),
    ]
    if browser:
        args += ["--cookies-from-browser", browser]
    args.append(link)

    creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=creationflags, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None

    try:
        data = json.loads(result.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return None

    heights = [f.get("height") for f in data.get("formats", []) if f.get("height")]
    if heights:
        return max(heights)
    return data.get("height")


def _move_file(src: Path, dst: Path) -> None:
    """Path.replace() (= os.rename) lỗi WinError 17 nếu đích khác ổ đĩa với
    thư mục tạm (vd thư mục tạm ở C:, thư mục lưu ở D:) - dùng shutil.move
    (tự copy rồi xoá khi khác ổ đĩa) để luôn hoạt động bất kể ổ đĩa nào."""
    shutil.move(str(src), str(dst))


class Downloader:
    def __init__(
        self,
        request: DownloadRequest,
        ytdlp_path: Path,
        ffmpeg_path: Path,
        log_cb: LogCb = None,
        progress_cb: ProgressCb = None,
    ) -> None:
        self.req = request
        self.ytdlp_path = Path(ytdlp_path)
        self.ffmpeg_path = Path(ffmpeg_path)
        self.log_cb = log_cb
        self.progress_cb = progress_cb
        self.output_file_path: Optional[Path] = None
        self.succeeded = False
        self._current_proc: Optional[subprocess.Popen] = None
        self._cancelled = False

    def _log(self, msg: str) -> None:
        if self.log_cb:
            self.log_cb(msg)

    @property
    def link(self) -> str:
        value = self.req.url_or_id
        return value if "/" in value else f"https://youtu.be/{value}"

    def _cookie_args(self) -> list[str]:
        # File cookies.txt (nếu người dùng đã nhập) luôn được ưu tiên hơn đọc
        # trực tiếp từ trình duyệt - vì đây là cách duy nhất hoạt động chắc
        # chắn trên các trình duyệt Chromium đời mới (Chrome/Edge/Brave/CocCoc)
        # đã đổi cơ chế mã hoá cookie khiến yt-dlp không giải mã được nữa.
        if self.req.cookies_file:
            return ["--cookies", self.req.cookies_file]
        if self.req.browser:
            return ["--cookies-from-browser", self.req.browser]
        return []

    @staticmethod
    def _raise_if_cookie_error(stdout: str) -> None:
        lowered = stdout.lower()
        if "sign in to confirm" in lowered or "not a bot" in lowered:
            raise CookieError(
                "YouTube yêu cầu xác minh \"không phải bot\" cho video này."
            )
        if "could not copy" in lowered or "database is locked" in lowered:
            raise CookieError(
                "Không đọc được file cookie của trình duyệt (đang bị khoá)."
            )
        if "failed to decrypt" in lowered or "dpapi" in lowered:
            raise CookieError(
                "Trình duyệt bạn chọn dùng cách mã hoá cookie mới mà công cụ tải chưa hỗ trợ giải mã được."
            )

    _FRIENDLY_ERRORS = (
        (("private video",), "Video này ở chế độ riêng tư nên không tải được."),
        (("video unavailable", "this video is not available", "has been removed", "no longer available",
          "account associated with this video has been terminated"),
         "Video không còn tồn tại hoặc đã bị xóa. Hãy kiểm tra lại link."),
        (("not available in your country", "blocked it in your country", "who has blocked it"),
         "Video bị chặn ở quốc gia của bạn nên không tải được."),
        (("members-only", "join this channel"), "Đây là video chỉ dành cho thành viên của kênh nên không tải được."),
        (("unsupported url", "is not a valid url"), "Link chưa đúng. Hãy dán link video YouTube (dạng youtube.com/watch... hoặc youtu.be/...)."),
        (("premieres in", "live event will begin", "this live event"),
         "Video này chưa phát sóng hoặc đang phát trực tiếp. Hãy thử lại sau khi video đã kết thúc."),
    )

    @classmethod
    def _raise_friendly_error(cls, stdout: str) -> None:
        lowered = stdout.lower()
        if "confirm your age" in lowered or "age-restricted" in lowered or "inappropriate for some users" in lowered:
            raise CookieError("Video giới hạn độ tuổi, cần đăng nhập YouTube để tải.")
        for keywords, message in cls._FRIENDLY_ERRORS:
            if any(k in lowered for k in keywords):
                raise DownloadError(message)

    def cancel(self) -> None:
        """Hủy tiến trình tải/cắt đang chạy - dùng khi người dùng đóng app,
        diệt cả tiến trình con (VD ffmpeg do yt-dlp sinh ra) bằng taskkill /T
        thay vì chỉ terminate() tiến trình cha, vì Windows không tự dọn tiến
        trình con khi tiến trình cha bị kill."""
        self._cancelled = True
        proc = self._current_proc
        if proc is None or proc.poll() is not None:
            return
        creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, creationflags=creationflags,
            )
        except OSError:
            try:
                proc.kill()
            except OSError:
                pass

    def _run(self, args: list[str], on_line: Optional[Callable[[str], None]] = None) -> subprocess.CompletedProcess:
        creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        self._current_proc = proc
        lines: list[str] = []
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.rstrip("\n")
                lines.append(line)
                if on_line:
                    on_line(line)
            proc.wait()
        finally:
            self._current_proc = None

        if self._cancelled:
            raise DownloadCancelled("Đã hủy theo yêu cầu người dùng.")
        return subprocess.CompletedProcess(args, proc.returncode, "\n".join(lines), "")

    def _fetch_info(self) -> dict:
        self._log("Đang lấy thông tin video...")
        args = [
            str(self.ytdlp_path),
            "-j",
            "--no-check-certificates",
            "--extractor-args", "youtube:skip=dash",
            "--ffmpeg-location", str(self.ffmpeg_path.parent),
        ]
        args += self._cookie_args()
        args.append(self.link)

        result = self._run(args)
        if result.returncode != 0 and "could not copy" in result.stdout.lower():
            # File cookie moi vua bi khoa/chua giai phong ngay sau khi dong
            # trinh duyet (race condition) - doi 1 chut roi thu lai 1 lan.
            self._log("File cookie tạm thời chưa đọc được, thử lại sau 1.5s...")
            time.sleep(1.5)
            result = self._run(args)

        if result.returncode != 0:
            self._log(result.stdout)
            self._raise_if_cookie_error(result.stdout)
            self._raise_friendly_error(result.stdout)
            raise DownloadError("Không lấy được thông tin video. Hãy kiểm tra lại link và kết nối mạng, rồi thử lại.")

        try:
            data = json.loads(result.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError) as exc:
            raise DownloadError(f"Không đọc được dữ liệu video: {exc}") from exc

        duration = data.get("duration") or 0
        self._log(f"Tiêu đề: {data.get('title')}")
        self._log(f"Thời lượng: {duration}s")

        if duration and duration < self.req.start:
            raise DownloadError("Thời gian bắt đầu/kết thúc phải nhỏ hơn thời lượng video.")

        return data

    def _calculate_path(self, title: Optional[str], upload_date: Optional[str], video_id: Optional[str]) -> Path:
        title = title or ""
        title = INVALID_FILENAME_CHARS.sub("", title).replace(".", "")
        if len(title) > 80:
            self._log("Tiêu đề quá dài, cắt còn 80 ký tự.")
            title = title[:80]

        if upload_date:
            try:
                date = datetime.strptime(upload_date, "%Y%m%d")
            except ValueError:
                date = datetime.now()
        else:
            date = datetime.now()

        filename = f"{date:%Y%m%d} {title} ({video_id or self.req.url_or_id}) [{self.req.start}_{self.req.end}].mp4"
        path = Path(self.req.output_dir) / filename
        self._log(f"Đường dẫn file kết quả: {path}")
        return path

    def _download_video(self, temp_path: Path, expected_duration: float) -> None:
        self._log("Bắt đầu tải video...")
        args = [
            str(self.ytdlp_path),
            "--no-check-certificates",
            "--extractor-args", "youtube:skip=dash",
            "--ffmpeg-location", str(self.ffmpeg_path.parent),
            "--merge-output-format", "mp4",
            "-o", str(temp_path),
            "--no-part",
        ]

        if self.req.fmt:
            args += ["-f", self.req.fmt]
        if self.req.sort:
            args += ["-S", self.req.sort]
        elif not self.req.fmt:
            args += ["-S", "res"]

        args += self._cookie_args()

        if self.req.end != 0:
            args += ["--downloader", "ffmpeg",
                     "--downloader-args", f"ffmpeg_i:-ss {self.req.start} -to {self.req.end}"]

        args.append(self.link)

        last_progress = -1.0

        def on_line(line: str) -> None:
            nonlocal last_progress

            match = PROGRESS_RE.match(line)
            if match:
                pct = float(match.group(1))
            else:
                time_match = FFMPEG_TIME_RE.search(line)
                if not time_match or time_match.group(1) or expected_duration <= 0:
                    # Không phải dòng tiến trình (hoặc mốc thời gian âm) -> log nguyên văn
                    self._log(line)
                    return
                _, hh, mm, ss = time_match.groups()
                current = int(hh) * 3600 + int(mm) * 60 + float(ss)
                pct = max(0.0, min(100.0, current / expected_duration * 100))

            if pct - last_progress < 0.5 and pct < 99.9:
                return
            last_progress = pct
            self._log(f"Đang tải... {pct:.0f}%")
            if self.progress_cb:
                self.progress_cb(pct)

        result = self._run(args, on_line=on_line)
        if result.returncode != 0:
            self._log(result.stdout)
            self._raise_if_cookie_error(result.stdout)
            self._raise_friendly_error(result.stdout)
            raise DownloadError("Tải video thất bại. Hãy kiểm tra kết nối mạng rồi thử lại.")
        self._log("Tải video xong.")

    def _cut_with_ffmpeg(self, input_path: Path, output_path: Path) -> None:
        duration = self.req.end - self.req.start
        working = _detect_working_encoders(self.ffmpeg_path)
        candidates = [c for c in _ENCODER_CANDIDATES if c[0] == "libx264" or c[0] in working]
        self._log(f"===> Encoder khả dụng trên máy này: {', '.join(c[0] for c in candidates)}")

        for codec, codec_args in candidates:
            self._log(f"===> Đang thử cắt bằng encoder: {codec} ...")
            args = [
                str(self.ffmpeg_path),
                "-sseof", f"-{duration}",
                "-i", str(input_path),
                "-c:v", codec, *codec_args,
                "-c:a", "aac", "-b:a", "192k",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                "-y",
                str(output_path),
            ]
            lines: list[str] = []
            last_progress = -1.0

            def collect(line: str, _lines=lines) -> None:
                nonlocal last_progress
                _lines.append(line)

                time_match = FFMPEG_TIME_RE.search(line)
                if time_match and not time_match.group(1) and duration > 0:
                    _, hh, mm, ss = time_match.groups()
                    current = int(hh) * 3600 + int(mm) * 60 + float(ss)
                    pct = max(0.0, min(100.0, current / duration * 100))
                    if pct - last_progress >= 1 or pct >= 99.9:
                        last_progress = pct
                        self._log(f"Đang cắt... {pct:.0f}%")
                        if self.progress_cb:
                            self.progress_cb(pct)
                    return

                self._log(line)

            result = self._run(args, on_line=collect)
            if result.returncode == 0:
                self._log(f"===> Cắt video thành công bằng encoder: {codec}")
                return
            reason = _friendly_gpu_failure_reason(lines) if codec != "libx264" else "\n".join(lines[-8:])
            self._log(f"===> Encoder {codec} THẤT BẠI, lý do:\n{reason}\n===> Thử phương án kế tiếp...")

        raise DownloadError("Cắt video bằng FFmpeg thất bại (đã thử cả GPU lẫn CPU).")

    def run(self) -> Path:
        self._log("Bắt đầu quá trình tải...")
        self.succeeded = False

        with tempfile.TemporaryDirectory(prefix="ysd_") as tmp_dir:
            temp1 = Path(tmp_dir) / "part1.mp4"
            temp2 = Path(tmp_dir) / "part2.mp4"

            info = self._fetch_info()
            self.output_file_path = self._calculate_path(
                info.get("title"), info.get("upload_date"), info.get("id")
            )
            self.output_file_path.parent.mkdir(parents=True, exist_ok=True)

            if self.req.end != 0:
                expected_duration = self.req.end - self.req.start
            else:
                expected_duration = info.get("duration") or 0
            self._download_video(temp1, expected_duration)

            if self.req.end == 0:
                self._log(f"Di chuyển file tới {self.output_file_path}")
                _move_file(temp1, self.output_file_path)
            else:
                self._cut_with_ffmpeg(temp1, temp2)
                _move_file(temp2, self.output_file_path)

            self._log("Tải hoàn tất:")
            self._log(str(self.output_file_path))
            self.succeeded = True
            return self.output_file_path
