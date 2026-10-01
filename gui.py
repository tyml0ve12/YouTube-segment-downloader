"""GUI tkinter, bố cục phỏng theo Form1 gốc (WinForms)."""
from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from core.deps import ensure_dependencies
from core.downloader import (
    CookieError, DownloadCancelled, DownloadError, DownloadRequest, Downloader, fetch_max_height,
)
from core.version import APP_VERSION

BROWSERS = ["( Tắt )", "coccoc", "chrome", "firefox", "edge", "brave", "opera", "vivaldi", "safari"]
ICON_PATH = Path(__file__).resolve().parent / "YoutubeSegmentDownloader.ico"

# Tên tiến trình thực thi của từng trình duyệt trên Windows - dùng để kiểm tra
# xem trình duyệt còn chạy ngầm hay không (vd sau khi đóng hết cửa sổ, nhiều
# trình duyệt Chromium vẫn giữ 1 tiến trình chạy nền cho thông báo/đồng bộ,
# khiến file cookie bị khoá không đọc được).
BROWSER_PROCESS_NAMES = {
    "coccoc": ["browser.exe"],
    "chrome": ["chrome.exe"],
    "edge": ["msedge.exe"],
    "brave": ["brave.exe"],
    "firefox": ["firefox.exe"],
    "opera": ["opera.exe", "launcher.exe"],
    "vivaldi": ["vivaldi.exe"],
}

# (nhãn hiển thị, chiều cao tối đa hoặc None = không giới hạn/giữ nguyên gốc)
RESOLUTIONS = [
    ("720p (nhanh nhất)", 720),
    ("1080p - Full HD", 1080),
    ("1440p - 2K", 1440),
    ("2160p - 4K", 2160),
    ("Giữ nguyên độ phân giải gốc (khuyến nghị)", None),
]
DEFAULT_RESOLUTION = RESOLUTIONS[-1][0]


def resolution_to_sort(height: int | None) -> str:
    """Dùng --format-sort thay vì lọc cứng: trong số các format thoả độ phân
    giải đã chọn, ưu tiên fps cao hơn (60fps nếu video gốc có), rồi ưu tiên
    nguồn AV1 - cùng độ phân giải nhưng AV1 nén hiệu quả hơn hẳn VP9/H.264
    (thường chỉ bằng 1/3 dung lượng), nên tải nhanh hơn nhiều mà chất lượng
    đầu ra không đổi vì bước cắt luôn mã hoá lại từ đầu."""
    res_part = f"res:{height}" if height else "res"
    return f"{res_part},fps,vcodec:av01"

class TimeEntry(ttk.Entry):
    """Ô nhập giờ bắt buộc dạng hh:mm:ss.

    Click vào giờ/phút/giây rồi gõ số liên tục: mỗi ô 2 chữ số tự đẩy dịch
    trái->phải (giống MaskedTextBox), gõ đủ 2 số thì tự nhảy sang ô kế tiếp
    và tự thêm dấu ':' - không cần tự gõ dấu ':'. Không thể xoá mất dấu ':'
    vì nó không phải ký tự do người dùng gõ ra.
    """

    _SEG_STARTS = (0, 3, 6)

    def __init__(self, master, **kwargs) -> None:
        self.var = tk.StringVar()
        kwargs.setdefault("justify", "center")
        kwargs.setdefault("font", ("Consolas", 12))
        kwargs.setdefault("width", 10)
        super().__init__(master, textvariable=self.var, **kwargs)
        self._segs = ["00", "00", "00"]
        self._active = 0
        self._typed = 0
        self._rebuild()
        self.bind("<Key>", self._on_key)
        self.bind("<Button-1>", self._on_click)

    def _rebuild(self) -> None:
        self.var.set(f"{self._segs[0]}:{self._segs[1]}:{self._segs[2]}")

    def _clamp(self, seg_idx: int) -> None:
        value = int(self._segs[seg_idx])
        value = min(value, 59) if seg_idx in (1, 2) else min(value, 99)
        self._segs[seg_idx] = f"{value:02d}"

    def _goto(self, seg_idx: int) -> None:
        self._clamp(self._active)
        self._active = max(0, min(2, seg_idx))
        self._typed = 0
        self._rebuild()
        self.icursor(self._SEG_STARTS[self._active])

    def _on_click(self, event: tk.Event | None = None) -> None:
        # Tính vị trí trực tiếp từ toạ độ chuột (event.x) thay vì đọc "insert",
        # vì FocusIn (khi ô chưa có focus) có thể xảy ra sau click và từng
        # làm sai lệch nếu dựa vào "insert" hay reset lại ở FocusIn.
        idx = self.index(f"@{event.x}") if event is not None else self.index("insert")
        self._clamp(self._active)
        self._active = 0 if idx < 3 else (1 if idx < 6 else 2)
        self._typed = 0
        # Không "break": giữ hành vi focus mặc định của Tk khi click. Đặt lại
        # con trỏ hiển thị sau khi Tk xử lý xong click, để không bị ghi đè.
        self.after_idle(lambda: self.icursor(self._SEG_STARTS[self._active]))

    def _on_key(self, event: tk.Event):
        keysym = event.keysym
        if keysym in ("Tab", "ISO_Left_Tab"):
            return None
        if keysym in ("Left", "colon") or event.char == ":":
            self._goto(self._active - 1) if keysym == "Left" else self._goto(self._active + 1)
            return "break"
        if keysym == "Right":
            self._goto(self._active + 1)
            return "break"
        if keysym == "Home":
            self._goto(0)
            return "break"
        if keysym == "End":
            self._goto(2)
            return "break"
        if keysym == "BackSpace":
            seg = self._segs[self._active]
            self._segs[self._active] = "0" + seg[0]
            self._typed = max(0, self._typed - 1)
            self._rebuild()
            self.icursor(self._SEG_STARTS[self._active] + 2)
            return "break"
        if keysym == "Delete":
            self._segs[self._active] = "00"
            self._typed = 0
            self._rebuild()
            self.icursor(self._SEG_STARTS[self._active] + 2)
            return "break"
        if event.char and event.char.isdigit():
            seg = self._segs[self._active]
            self._segs[self._active] = seg[1] + event.char
            self._typed += 1
            if self._typed >= 2 and self._active < 2:
                self._goto(self._active + 1)
            else:
                self._rebuild()
                self.icursor(self._SEG_STARTS[self._active] + self._typed)
            return "break"
        return "break"

    def get_seconds(self) -> float:
        self._clamp(self._active)
        hours, minutes, seconds = (int(s) for s in self._segs)
        return hours * 3600 + minutes * 60 + seconds

    def set_seconds(self, total_seconds: float) -> None:
        total = max(0, int(total_seconds))
        hours, rem = divmod(total, 3600)
        minutes, seconds = divmod(rem, 60)
        self._segs = [f"{min(hours, 99):02d}", f"{minutes:02d}", f"{seconds:02d}"]
        self._rebuild()


class PlaceholderEntry(ttk.Entry):
    """Entry hiển thị chữ mờ (placeholder) khi rỗng, giống combobox '(...)' của bản gốc."""

    def __init__(self, master, placeholder: str, **kwargs) -> None:
        super().__init__(master, **kwargs)
        self._placeholder = placeholder
        self._has_placeholder = False
        self.bind("<FocusIn>", self._clear_placeholder)
        self.bind("<FocusOut>", self._set_placeholder)
        self._set_placeholder()

    def _clear_placeholder(self, _event=None) -> None:
        if self._has_placeholder:
            self.delete(0, "end")
            self.configure(foreground="")
            self._has_placeholder = False

    def _set_placeholder(self, _event=None) -> None:
        if not self.get():
            self.insert(0, self._placeholder)
            self.configure(foreground="grey")
            self._has_placeholder = True

    def real_value(self) -> str:
        return "" if self._has_placeholder else self.get().strip()


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Tải Video Youtube Theo Đoạn")
        self.geometry("760x520")
        self.minsize(680, 460)
        try:
            self.iconbitmap(str(ICON_PATH))
        except tk.TclError:
            pass

        self._log_queue: "queue.Queue[str]" = queue.Queue()
        self._ytdlp_path: Path | None = None
        self._ffmpeg_path: Path | None = None
        self._active_downloader: Downloader | None = None

        self._build_widgets()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._drain_log_queue)
        threading.Thread(target=self._init_dependencies, daemon=True).start()
        self.after(2000, self._auto_check_update)

    def _auto_check_update(self) -> None:
        from update_ui import check_for_update_async

        check_for_update_async(self, Path(__file__).resolve().parent, silent=True)

    def _manual_check_update(self) -> None:
        from update_ui import check_for_update_async

        check_for_update_async(self, Path(__file__).resolve().parent, silent=False)

    def _on_close(self) -> None:
        if self._active_downloader is not None:
            self._active_downloader.cancel()
        self.destroy()

    # ---------- UI ----------
    def _build_widgets(self) -> None:
        ttk.Label(
            self, text="Hoàng Đức - Brightstar",
            font=("Segoe UI", 9, "italic"), foreground="grey", anchor="e",
        ).pack(fill="x", padx=10, pady=(6, 0))

        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)

        # -- Output Directory --
        top = ttk.Frame(root)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Thư mục lưu", width=16).grid(row=0, column=0, sticky="w", pady=3)
        self.output_var = tk.StringVar(value=str(Path.home() / "Downloads"))
        ttk.Entry(top, textvariable=self.output_var).grid(row=0, column=1, sticky="ew", padx=(4, 4))
        ttk.Button(top, text="\U0001F4C1", width=3, command=self._pick_folder).grid(row=0, column=2)

        # -- Youtube Link --
        ttk.Label(top, text="Link Youtube", width=16).grid(row=1, column=0, sticky="w", pady=3)
        self.url_entry = PlaceholderEntry(top, placeholder="https://youtu.be/......")
        self.url_entry.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(4, 4))
        self.url_entry.bind("<FocusOut>", self._on_url_focus_out, add="+")
        self._checked_url = ""

        # -- middle: Segment | format+cookies | Start button --
        middle = ttk.Frame(root)
        middle.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        middle.columnconfigure(0, weight=2)
        middle.columnconfigure(1, weight=3)
        middle.columnconfigure(2, weight=0)

        segment_box = ttk.LabelFrame(middle, text="Cắt đoạn")
        segment_box.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        self.segment_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            segment_box, text="Bật", variable=self.segment_var, command=self._toggle_segment
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(6, 4))

        ttk.Label(segment_box, text="Bắt đầu").grid(row=1, column=0, sticky="w", padx=8)
        ttk.Label(segment_box, text="Kết thúc").grid(row=1, column=1, sticky="w", padx=8)

        self.start_entry = TimeEntry(segment_box)
        self.end_entry = TimeEntry(segment_box)
        self.start_entry.grid(row=2, column=0, sticky="w", padx=8, pady=(0, 8))
        self.end_entry.grid(row=2, column=1, sticky="w", padx=8, pady=(0, 8))

        settings_box = ttk.Frame(middle)
        settings_box.grid(row=0, column=1, sticky="nsew", padx=(0, 8))
        settings_box.columnconfigure(0, weight=1)

        res_box = ttk.LabelFrame(settings_box, text="Độ phân giải")
        res_box.pack(fill="x")
        self.resolution_var = tk.StringVar(value=DEFAULT_RESOLUTION)
        self.resolution_combo = ttk.Combobox(
            res_box, textvariable=self.resolution_var,
            values=[label for label, _ in RESOLUTIONS], state="readonly",
        )
        self.resolution_combo.pack(fill="x", padx=8, pady=6)

        format_box = ttk.LabelFrame(settings_box, text="Định dạng tải (ghi đè, ưu tiên hơn Độ phân giải)")
        format_box.pack(fill="x", pady=(8, 0))
        self.format_entry = PlaceholderEntry(format_box, placeholder="( Để trống )")
        self.format_entry.pack(fill="x", padx=8, pady=6)

        cookie_box = ttk.LabelFrame(settings_box, text="Cookie từ trình duyệt")
        cookie_box.pack(fill="x", pady=(8, 0))

        # Dropdown chon trinh duyet + nut nho nhap file cookies.txt thu cong
        # ngay canh (dung khi dropdown bi loi - nhieu trinh duyet Chromium doi
        # moi nhu Chrome/Edge/Brave/CocCoc khong tu doc cookie duoc nua, xem
        # github.com/yt-dlp/yt-dlp/issues/10927). Da chon file thi UU TIEN
        # dung file, bo qua dropdown.
        cookie_row = ttk.Frame(cookie_box)
        cookie_row.pack(fill="x", padx=8, pady=6)
        cookie_row.columnconfigure(0, weight=1)
        self.browser_var = tk.StringVar(value=BROWSERS[0])
        ttk.Combobox(
            cookie_row, textvariable=self.browser_var, values=BROWSERS, state="readonly"
        ).grid(row=0, column=0, sticky="ew")
        ttk.Button(cookie_row, text="📄", width=3, command=self._pick_cookies_file).grid(
            row=0, column=1, padx=(4, 0)
        )

        self.cookies_file_var = tk.StringVar(value="")  # duong dan that, dung de tai
        self.cookies_file_display_var = tk.StringVar(value="")  # chi de hien thi
        ttk.Label(cookie_box, textvariable=self.cookies_file_display_var, foreground="grey").pack(
            fill="x", padx=8, pady=(0, 6)
        )

        self.download_btn = tk.Button(
            middle, text="Tải xuống", font=("Segoe UI", 14), relief="raised",
            command=self._start_download,
        )
        self.download_btn.grid(row=0, column=2, sticky="nsew", ipadx=10)

        self.progress = ttk.Progressbar(root, mode="determinate", maximum=100)
        self.progress.grid(row=2, column=0, sticky="ew", pady=(10, 4))

        # -- log --
        log_frame = ttk.Frame(root)
        log_frame.grid(row=3, column=0, sticky="nsew")
        root.rowconfigure(3, weight=1)
        self.log_text = tk.Text(log_frame, state="disabled", wrap="word", bg="#1e1e1e", fg="#d4d4d4")
        self.log_text.pack(fill="both", expand=True, side="left")
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scroll.set)

        # -- bottom bar --
        bottom = ttk.Frame(root)
        bottom.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        self.verbose_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bottom, text="Log chi tiết", variable=self.verbose_var).grid(row=0, column=0, sticky="w")
        ttk.Button(bottom, text="Tải lại yt-dlp / FFmpeg", command=self._redownload_dependencies).grid(
            row=0, column=1, padx=8
        )
        self.driver_btn = ttk.Button(bottom, text="Cập nhật driver GPU", command=self._open_driver_page)
        self._driver_url: str | None = None
        # Ẩn cho tới khi biết máy có cần cập nhật driver hay không.

        version_link = ttk.Label(
            bottom, text=f"v{APP_VERSION} · Kiểm tra cập nhật", foreground="#3391ff", cursor="hand2",
        )
        version_link.grid(row=0, column=2, sticky="e")
        bottom.columnconfigure(2, weight=1)
        version_link.bind("<Button-1>", lambda _e: self._manual_check_update())

        self.dep_status_var = tk.StringVar(value="Đang kiểm tra yt-dlp / FFmpeg...")
        ttk.Label(root, textvariable=self.dep_status_var, foreground="grey").grid(
            row=5, column=0, sticky="w", pady=(4, 0)
        )

        self.gpu_status_var = tk.StringVar(value="")
        ttk.Label(root, textvariable=self.gpu_status_var, foreground="#b8860b", wraplength=740, justify="left").grid(
            row=6, column=0, sticky="w", pady=(2, 0)
        )

        self._toggle_segment()

    def _toggle_segment(self) -> None:
        state = "normal" if self.segment_var.get() else "disabled"
        self.start_entry.configure(state=state)
        self.end_entry.configure(state=state)

    def _pick_folder(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.output_var.get() or ".")
        if folder:
            self.output_var.set(folder)

    def _pick_cookies_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Chọn file cookies.txt", filetypes=[("Cookies text", "*.txt"), ("Tất cả", "*.*")]
        )
        if path:
            self.cookies_file_var.set(path)
            self.cookies_file_display_var.set(f"✓ Dùng file: {Path(path).name}")
            self._log(f"Đã chọn file cookies.txt: {path}")

    # ---------- kiểm tra độ phân giải thật của video ----------
    def _on_url_focus_out(self, _event=None) -> None:
        url = self.url_entry.real_value()
        if not url or url == self._checked_url or not self._ytdlp_path:
            return
        self._checked_url = url
        threading.Thread(target=self._check_max_resolution, args=(url,), daemon=True).start()

    def _browser_arg(self) -> str:
        browser = self.browser_var.get()
        if browser == BROWSERS[0]:
            return ""
        # CocCoc la trinh duyet dua tren Chromium nhung yt-dlp khong biet ten
        # nay - tro thang "chrome" toi dung thu muc du lieu cua CocCoc (yt-dlp
        # chap nhan duong dan tuyet doi lam PROFILE, se tu suy ra dung thu muc
        # chua "Local State" tu do). Nguoi dung khong can biet chi tiet nay.
        if browser == "coccoc":
            coccoc_profile = Path(os.environ.get("LOCALAPPDATA", "")) / "CocCoc" / "Browser" / "User Data" / "Default"
            return f"chrome:{coccoc_profile}"
        return browser

    @staticmethod
    def _is_process_running(exe_name: str) -> bool:
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {exe_name}"],
                capture_output=True, text=True, creationflags=creationflags,
            )
            return exe_name.lower() in result.stdout.lower()
        except OSError:
            return False

    def _ensure_browser_closed(self) -> bool:
        """Nếu người dùng chọn lấy cookie từ 1 trình duyệt và trình duyệt đó
        vẫn còn chạy (kể cả chạy ngầm sau khi đã đóng hết cửa sổ), hỏi xác
        nhận trước khi tự đóng - KHÔNG tự ý đóng để tránh mất việc đang làm dở
        của người dùng. Trả về False nếu người dùng từ chối (huỷ tải)."""
        browser = self.browser_var.get()
        exe_names = BROWSER_PROCESS_NAMES.get(browser)
        if not exe_names:
            return True

        running = [name for name in exe_names if self._is_process_running(name)]
        if not running:
            return True

        confirmed = messagebox.askyesno(
            "Cần đóng trình duyệt",
            f"Cần đóng hẳn trình duyệt \"{browser}\" để đọc được cookie đăng nhập "
            f"(kể cả khi bạn tưởng đã đóng, nó vẫn có thể đang chạy ngầm).\n\n"
            f"Hãy LƯU lại mọi việc đang làm dở trên trình duyệt đó trước.\n\n"
            f"Đóng \"{browser}\" ngay bây giờ để tiếp tục tải?",
            icon="warning",
        )
        if not confirmed:
            self._log("Đã huỷ: cần đóng trình duyệt để đọc cookie.")
            return False

        for name in running:
            try:
                creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
                subprocess.run(["taskkill", "/IM", name, "/F", "/T"], capture_output=True, creationflags=creationflags)
            except OSError:
                pass
        # Doi mot chut de Windows kip giai phong khoa file cookie truoc khi
        # yt-dlp doc, tranh loi "could not copy cookie database" do race
        # condition ngay sau khi vua kill tien trinh.
        time.sleep(1.0)
        self._log(f"Đã đóng trình duyệt \"{browser}\" để đọc cookie.")
        return True

    def _check_max_resolution(self, url: str) -> None:
        browser = self._browser_arg()
        try:
            max_height = fetch_max_height(self._ytdlp_path, self._ffmpeg_path, url, browser)
        except Exception:  # noqa: BLE001
            max_height = None
        self.after(0, lambda: self._apply_resolution_limit(max_height))

    def _apply_resolution_limit(self, max_height: int | None) -> None:
        if max_height:
            self._log(f"Video này có độ phân giải cao nhất: {max_height}p")
            values = [label for label, h in RESOLUTIONS if h is None or h <= max_height]
        else:
            values = [label for label, _ in RESOLUTIONS]

        self.resolution_combo.configure(values=values)
        if self.resolution_var.get() not in values:
            self.resolution_var.set(values[-1])

    # ---------- logging / threading plumbing ----------
    def _log(self, msg: str) -> None:
        self._log_queue.put(msg)

    def _drain_log_queue(self) -> None:
        try:
            while True:
                msg = self._log_queue.get_nowait()
                self.log_text.configure(state="normal")
                self.log_text.insert("end", msg + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._drain_log_queue)

    def _set_progress(self, pct: float) -> None:
        self.progress.after(0, lambda: self.progress.configure(value=pct))

    # ---------- dependency handling ----------
    def _init_dependencies(self) -> None:
        try:
            ytdlp, ffmpeg, deno = ensure_dependencies(self._log)
            self._ytdlp_path, self._ffmpeg_path = ytdlp, ffmpeg
            deno_status = str(deno) if deno else "chưa có (một số video có thể lỗi)"
            self.dep_status_var.set(f"yt-dlp: {ytdlp}   |   FFmpeg: {ffmpeg}   |   Deno: {deno_status}")
            self._check_gpu_status(ffmpeg)
        except Exception as exc:  # noqa: BLE001
            self._log(f"Lỗi khi chuẩn bị phụ thuộc: {exc}")
            self.dep_status_var.set("Chuẩn bị phụ thuộc thất bại, xem log.")

    def _check_gpu_status(self, ffmpeg_path: Path) -> None:
        from core.downloader import describe_gpu_status

        status, driver_url = describe_gpu_status(ffmpeg_path)
        self._log(status)
        self.after(0, lambda: self._apply_gpu_status(status, driver_url))

    def _apply_gpu_status(self, status: str, driver_url: str | None) -> None:
        self.gpu_status_var.set(status)
        self._driver_url = driver_url
        if driver_url:
            self.driver_btn.grid(row=0, column=2, padx=8)
        else:
            self.driver_btn.grid_remove()

    def _open_driver_page(self) -> None:
        if self._driver_url:
            webbrowser.open(self._driver_url)

    def _redownload_dependencies(self) -> None:
        self.dep_status_var.set("Đang tải lại yt-dlp / FFmpeg...")
        threading.Thread(target=self._force_redownload, daemon=True).start()

    def _force_redownload(self) -> None:
        from core.deps import download_deno, download_ffmpeg, download_ytdlp

        try:
            ytdlp = download_ytdlp(self._log)
            ffmpeg = download_ffmpeg(self._log)
            self._ytdlp_path, self._ffmpeg_path = ytdlp, ffmpeg
            try:
                deno = download_deno(self._log)
            except Exception as exc:  # noqa: BLE001
                self._log(f"Tải Deno thất bại (không chặn ứng dụng): {exc}")
                deno = None
            deno_status = str(deno) if deno else "chưa có (một số video có thể lỗi)"
            self.dep_status_var.set(f"yt-dlp: {ytdlp}   |   FFmpeg: {ffmpeg}   |   Deno: {deno_status}")
            self._check_gpu_status(ffmpeg)
        except Exception as exc:  # noqa: BLE001
            self._log(f"Lỗi khi tải lại phụ thuộc: {exc}")
            self.dep_status_var.set("Tải lại phụ thuộc thất bại, xem log.")

    # ---------- download ----------
    def _start_download(self) -> None:
        if not self._ytdlp_path or not self._ffmpeg_path:
            messagebox.showwarning("Chưa sẵn sàng", "yt-dlp/FFmpeg chưa sẵn sàng, vui lòng đợi hoặc thử lại.")
            return

        url = self.url_entry.real_value()
        if not url:
            messagebox.showwarning("Thiếu link", "Vui lòng nhập link Youtube.")
            return

        start = self.start_entry.get_seconds() if self.segment_var.get() else 0.0
        end = self.end_entry.get_seconds() if self.segment_var.get() else 0.0

        if self.segment_var.get() and end <= start:
            messagebox.showwarning("Sai khoảng thời gian", "Thời gian kết thúc phải lớn hơn thời gian bắt đầu.")
            return

        cookies_file = self.cookies_file_var.get()
        if not cookies_file and not self._ensure_browser_closed():
            return

        output_dir = Path(self.output_var.get().strip() or ".")
        browser = "" if cookies_file else self._browser_arg()

        custom_fmt = self.format_entry.real_value()
        if custom_fmt:
            fmt, sort = custom_fmt, ""
        else:
            height = dict(RESOLUTIONS)[self.resolution_var.get()]
            fmt, sort = "", resolution_to_sort(height)

        request = DownloadRequest(
            url_or_id=url,
            start=start,
            end=end,
            output_dir=output_dir,
            fmt=fmt,
            sort=sort,
            browser=browser,
            cookies_file=cookies_file,
        )

        self.download_btn.configure(state="disabled")
        self.progress.configure(value=0)
        threading.Thread(target=self._run_download, args=(request,), daemon=True).start()

    def _run_download(self, request: DownloadRequest) -> None:
        downloader = Downloader(
            request,
            self._ytdlp_path,  # type: ignore[arg-type]
            self._ffmpeg_path,  # type: ignore[arg-type]
            log_cb=self._log,
            progress_cb=self._set_progress,
        )
        self._active_downloader = downloader
        try:
            output_path = downloader.run()
            self._log("Hoàn tất!")
            self.after(0, lambda: messagebox.showinfo("Xong", f"Đã lưu video tại:\n{output_path}"))
        except DownloadCancelled:
            self._log("Đã hủy.")
        except CookieError as exc:
            message = (
                f"{exc}\n\n"
                "Hãy dùng nút \U0001F4C4 cạnh dropdown \"Cookie từ trình duyệt\" để nhập file cookies.txt "
                "(xuất bằng extension trình duyệt như \"Get cookies.txt LOCALLY\"), rồi thử lại."
            )
            self._log(f"Lỗi cookie: {message}")
            self.after(0, lambda: messagebox.showerror("Cần cookie đăng nhập", message))
        except DownloadError as exc:
            message = str(exc)
            self._log(f"Lỗi: {message}")
            self.after(0, lambda: messagebox.showerror("Lỗi", message))
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            self._log(f"Lỗi không mong muốn: {message}")
            self.after(0, lambda: messagebox.showerror("Lỗi", message))
        finally:
            self._active_downloader = None
            self.after(0, lambda: self.download_btn.configure(state="normal"))



def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
