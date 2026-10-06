"""GUI tkinter, bố cục phỏng theo Form1 gốc (WinForms)."""
from __future__ import annotations

import json
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
SETTINGS_PATH = Path(__file__).resolve().parent / "settings.json"
COOKIE_EXTENSION_URL = (
    "https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc"
)

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

        self._settings = self._load_settings()
        self._build_widgets()
        self._apply_saved_settings()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._drain_log_queue)
        threading.Thread(target=self._init_dependencies, daemon=True).start()
        self.after(2000, self._auto_check_update)

    @staticmethod
    def _load_settings() -> dict:
        try:
            return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    def _save_settings(self) -> None:
        self._settings.update({
            "output_dir": self.output_var.get(),
            "browser": self.browser_var.get(),
            "cookies_file": self.cookies_file_var.get(),
            "resolution": self.resolution_var.get(),
        })
        try:
            SETTINGS_PATH.write_text(json.dumps(self._settings, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _apply_saved_settings(self) -> None:
        st = self._settings
        if st.get("output_dir") and Path(st["output_dir"]).is_dir():
            self.output_var.set(st["output_dir"])
        if st.get("browser") in BROWSERS:
            self.browser_var.set(st["browser"])
        cookies = st.get("cookies_file", "")
        if cookies and Path(cookies).is_file():
            self.cookies_file_var.set(cookies)
            self.cookies_file_display_var.set(f"✓ Dùng file: {Path(cookies).name}")
        if st.get("resolution") in dict(RESOLUTIONS):
            self.resolution_var.set(st["resolution"])

    def _auto_check_update(self) -> None:
        from update_ui import check_for_update_async

        check_for_update_async(self, Path(__file__).resolve().parent, silent=True)

    def _manual_check_update(self) -> None:
        from update_ui import check_for_update_async

        check_for_update_async(self, Path(__file__).resolve().parent, silent=False)

    def _on_close(self) -> None:
        self._save_settings()
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
        ttk.Button(cookie_row, text="?", width=3, command=lambda: self._show_help(0)).grid(
            row=0, column=2, padx=(4, 0)
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

        progress_frame = ttk.Frame(root)
        progress_frame.grid(row=2, column=0, sticky="ew", pady=(10, 4))
        progress_frame.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(progress_frame, mode="determinate", maximum=100)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.progress_text_var = tk.StringVar(value="")
        ttk.Label(progress_frame, textvariable=self.progress_text_var, font=("Segoe UI", 10, "bold")).grid(
            row=1, column=0, sticky="w", pady=(4, 0)
        )

        # -- log --
        log_frame = ttk.Frame(root)
        self._log_frame = log_frame
        log_frame.grid(row=3, column=0, sticky="nsew")
        root.rowconfigure(3, weight=1)
        self.log_text = tk.Text(log_frame, state="disabled", wrap="word", bg="#1e1e1e", fg="#d4d4d4")
        self.log_text.pack(fill="both", expand=True, side="left")
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scroll.set)
        log_frame.grid_remove()  # log kỹ thuật chỉ hiện khi tick "Hiện log chi tiết"

        # -- bottom bar --
        bottom = ttk.Frame(root)
        bottom.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        self.verbose_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            bottom, text="Hiện log chi tiết", variable=self.verbose_var, command=self._toggle_log
        ).grid(row=0, column=0, sticky="w")
        self.redownload_btn = ttk.Button(bottom, text="Tải lại công cụ", command=self._redownload_dependencies)
        self.redownload_btn.grid(row=0, column=1, padx=8)
        self.redownload_btn.grid_remove()  # chỉ hiện khi chuẩn bị công cụ thất bại
        ttk.Button(bottom, text="Hướng dẫn", command=lambda: self._show_help(0)).grid(
            row=0, column=2, padx=8
        )
        self.driver_btn = ttk.Button(bottom, text="Cập nhật driver GPU", command=self._open_driver_page)
        self._driver_url: str | None = None
        # Ẩn cho tới khi biết máy có cần cập nhật driver hay không.

        version_link = ttk.Label(
            bottom, text=f"v{APP_VERSION} · Kiểm tra cập nhật", foreground="#3391ff", cursor="hand2",
        )
        version_link.grid(row=0, column=4, sticky="e")
        bottom.columnconfigure(4, weight=1)
        version_link.bind("<Button-1>", lambda _e: self._manual_check_update())

        self.dep_status_var = tk.StringVar(value="Đang chuẩn bị, vui lòng chờ...")
        ttk.Label(root, textvariable=self.dep_status_var, foreground="grey").grid(
            row=5, column=0, sticky="w", pady=(4, 0)
        )

        self.gpu_status_var = tk.StringVar(value="")
        ttk.Label(root, textvariable=self.gpu_status_var, foreground="#b8860b", wraplength=740, justify="left").grid(
            row=6, column=0, sticky="w", pady=(2, 0)
        )

        self._toggle_segment()

    def _toggle_log(self) -> None:
        if self.verbose_var.get():
            self._log_frame.grid()
        else:
            self._log_frame.grid_remove()

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
                self._update_progress_text(msg)
        except queue.Empty:
            pass
        self.after(100, self._drain_log_queue)

    def _update_progress_text(self, msg: str) -> None:
        if msg.startswith(("Đang tải...", "Đang cắt...", "Đang lấy thông tin")):
            self.progress_text_var.set(msg)
        elif msg.startswith("Hoàn tất"):
            self.progress_text_var.set("✓ Hoàn tất!")
        elif msg.startswith("Đã hủy"):
            self.progress_text_var.set("Đã hủy.")
        elif msg.startswith(("Lỗi:", "Lỗi cookie", "Lỗi không mong muốn")):
            self.progress_text_var.set("✗ Có lỗi xảy ra.")

    def _set_progress(self, pct: float) -> None:
        self.progress.after(0, lambda: self.progress.configure(value=pct))

    # ---------- dependency handling ----------
    def _init_dependencies(self) -> None:
        try:
            ytdlp, ffmpeg, deno = ensure_dependencies(self._log)
            self._ytdlp_path, self._ffmpeg_path = ytdlp, ffmpeg
            deno_status = str(deno) if deno else "chưa có (một số video có thể lỗi)"
            self.dep_status_var.set("✓ Sẵn sàng tải video")
            self.after(0, self.redownload_btn.grid_remove)
            self._check_gpu_status(ffmpeg)
        except Exception as exc:  # noqa: BLE001
            self._log(f"Lỗi khi chuẩn bị phụ thuộc: {exc}")
            self.dep_status_var.set("Chuẩn bị thất bại, hãy kiểm tra mạng rồi bấm nút Tải lại công cụ.")
            self.after(0, self.redownload_btn.grid)

    def _check_gpu_status(self, ffmpeg_path: Path) -> None:
        from core.downloader import describe_gpu_status

        status, driver_url = describe_gpu_status(ffmpeg_path)
        self._log(status)
        self.after(0, lambda: self._apply_gpu_status(status, driver_url))

    def _apply_gpu_status(self, status: str, driver_url: str | None) -> None:
        self.gpu_status_var.set(status)
        self._driver_url = driver_url
        if driver_url:
            self.driver_btn.grid(row=0, column=3, padx=8)
            self._prompt_driver_update()
        else:
            self.driver_btn.grid_remove()

    def _prompt_driver_update(self) -> None:
        if self._settings.get("driver_prompt_dismissed"):
            return
        win = tk.Toplevel(self)
        win.title("Nên cập nhật driver card đồ họa (GPU)")
        win.transient(self)
        win.grab_set()
        frame = ttk.Frame(win, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text="Máy bạn có card đồ họa nhưng driver còn cũ, nên app đang phải xuất video bằng CPU (chậm hơn nhiều).\n\n"
                 "Hãy cập nhật driver GPU để tải và xuất video NHANH NHẤT:\n"
                 "1. Bấm \"Mở trang tải driver\" bên dưới.\n"
                 "2. Tải bản mới nhất, chạy file vừa tải và bấm Next cho tới khi xong.\n"
                 "3. KHỞI ĐỘNG LẠI máy rồi mở lại app.",
            justify="left", wraplength=480,
        ).pack(anchor="w")
        never = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Đừng nhắc lại", variable=never).pack(anchor="w", pady=(10, 0))
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(10, 0))

        def close() -> None:
            if never.get():
                self._settings["driver_prompt_dismissed"] = True
                self._save_settings()
            win.destroy()

        def open_page() -> None:
            self._open_driver_page()
            close()

        ttk.Button(row, text="Để sau", command=close).pack(side="right")
        ttk.Button(row, text="Mở trang tải driver", command=open_page).pack(side="right", padx=(0, 8))
        win.protocol("WM_DELETE_WINDOW", close)

    def _show_cookie_dialog(self, detail: str) -> None:
        win = tk.Toplevel(self)
        win.title("Cần đăng nhập YouTube (cookies)")
        win.transient(self)
        win.grab_set()
        frame = ttk.Frame(win, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text="YouTube đang yêu cầu xác nhận bạn không phải máy tự động.\nLàm theo 4 bước sau (chỉ làm 1 lần, dùng được nhiều ngày):",
            justify="left", font=("Segoe UI", 10, "bold"), wraplength=520,
        ).pack(anchor="w")
        steps = tk.Text(
            frame, width=70, height=13, wrap="word", relief="flat",
            borderwidth=0, background=win.cget("background"), font=("Segoe UI", 9),
        )
        steps.tag_configure("bold", font=("Segoe UI", 9, "bold"))
        steps.tag_configure("link", foreground="#0b5fd6", underline=True)
        steps.tag_bind("link", "<Button-1>", lambda _e: webbrowser.open(COOKIE_EXTENSION_URL))
        steps.tag_bind("link", "<Enter>", lambda _e: steps.configure(cursor="hand2"))
        steps.tag_bind("link", "<Leave>", lambda _e: steps.configure(cursor=""))
        keyword = "Get cookies.txt LOCALLY"
        before, _, after = self._HELP_COOKIE_STEPS.partition(keyword)
        steps.insert("end", before)
        steps.insert("end", keyword, ("bold", "link"))
        steps.insert("end", after)
        steps.configure(height=13, state="disabled")
        steps.pack(anchor="w", pady=(10, 6))
        ttk.Label(frame, text=f"Chi tiết lỗi: {detail}", foreground="grey", justify="left", wraplength=520).pack(
            anchor="w", pady=(0, 10)
        )
        row = ttk.Frame(frame)
        row.pack(fill="x")

        def pick() -> None:
            win.destroy()
            self._pick_cookies_file()

        ttk.Button(row, text="Mở YouTube", command=lambda: webbrowser.open("https://www.youtube.com")).pack(side="left")
        ttk.Button(row, text="Tải tiện ích cookies", command=lambda: webbrowser.open(COOKIE_EXTENSION_URL)).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(row, text="Đóng", command=win.destroy).pack(side="right")
        ttk.Button(row, text="Tôi đã có file, chọn file cookies.txt", command=pick).pack(side="right", padx=(0, 8))

    _HELP_COOKIE_STEPS = (
        "1. Bấm \"Mở YouTube\" bên dưới, ĐĂNG NHẬP tài khoản (nên dùng trình duyệt Chrome / Cốc Cốc / Edge).\n"
        "2. Bấm vào chữ \"Get cookies.txt LOCALLY\" (hoặc nút \"Tải tiện ích cookies\") rồi bấm \"Add to Chrome\" để cài.\n"
        "3. Ở trang youtube.com, bấm biểu tượng tiện ích đó rồi bấm \"Export\" để lưu file cookies.txt.\n"
        "4. Bấm \"Tôi đã có file, chọn file cookies.txt\" bên dưới, chọn file vừa lưu, rồi bấm \"Tải xuống\" lại.\n\n"
        "Lưu ý: file cookies.txt chứa thông tin đăng nhập, đừng gửi cho người khác."
    )

    _HELP_COOKIE = (
        "KHI NÀO CẦN?\n"
        "Khi app báo lỗi \"Cần cookie đăng nhập\" hoặc \"Sign in to confirm you're not a bot\".\n\n"
        "CÁCH LÀM (làm 1 lần, dùng được nhiều ngày):\n"
        "1. Mở trình duyệt (Chrome / Cốc Cốc / Edge), vào YouTube và ĐĂNG NHẬP tài khoản.\n"
        "2. Mở link này và bấm \"Add to Chrome\" để cài tiện ích \"Get cookies.txt LOCALLY\":\n"
        f"   {COOKIE_EXTENSION_URL}\n"
        "3. Mở lại trang youtube.com, bấm biểu tượng tiện ích đó, bấm \"Export\" (Xuất)\n"
        "   để lưu file cookies.txt (nhớ chỗ lưu, ví dụ Desktop).\n"
        "4. Quay lại app, bấm nút 📄 cạnh ô \"Cookie từ trình duyệt\", chọn file cookies.txt.\n"
        "5. Bấm \"Tải xuống\" như bình thường.\n\n"
        "LƯU Ý:\n"
        "- File cookies.txt chứa thông tin đăng nhập của bạn, đừng gửi cho người khác.\n"
        "- Nếu hết hạn (lại báo lỗi), chỉ cần xuất file mới và chọn lại.\n"
        "- Dùng tài khoản phụ nếu bạn lo lắng cho tài khoản chính."
    )
    _HELP_DRIVER = (
        "VÌ SAO CẦN?\n"
        "Card đồ họa (GPU) giúp xuất video nhanh hơn nhiều. Driver quá cũ thì app không dùng\n"
        "được GPU và phải chạy chậm bằng CPU.\n\n"
        "CÁCH LÀM:\n"
        "1. Nếu thấy nút \"Cập nhật driver GPU\" ở dưới cùng cửa sổ chính, bấm vào nút đó:\n"
        "   app mở trang tải driver của hãng (NVIDIA / AMD / Intel) phù hợp máy bạn.\n"
        "2. Tải bản mới nhất, chạy file vừa tải và bấm Next / Cài đặt theo hướng dẫn.\n"
        "3. KHỞI ĐỘNG LẠI máy tính sau khi cài xong.\n"
        "4. Mở lại app, xem dòng trạng thái GPU ở trên.\n\n"
        "Không thấy nút đó nghĩa là driver đã ổn, không cần làm gì."
    )

    def _show_help(self, tab: int = 0) -> None:
        win = tk.Toplevel(self)
        win.title("Hướng dẫn")
        win.transient(self)
        notebook = ttk.Notebook(win)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)
        for title, text in (("Nhập Cookie", self._HELP_COOKIE), ("Cập nhật driver GPU", self._HELP_DRIVER)):
            frame = ttk.Frame(notebook, padding=10)
            box = tk.Text(
                frame, width=75, height=text.count("\n") + 3, wrap="word", relief="flat", borderwidth=0,
                background=win.cget("background"), font=("Segoe UI", 9),
            )
            box.tag_configure("link", foreground="#0b5fd6", underline=True)
            box.tag_bind("link", "<Button-1>", lambda _e: webbrowser.open(COOKIE_EXTENSION_URL))
            box.tag_bind("link", "<Enter>", lambda _e, b=box: b.configure(cursor="hand2"))
            box.tag_bind("link", "<Leave>", lambda _e, b=box: b.configure(cursor=""))
            before, sep, after = text.partition(COOKIE_EXTENSION_URL)
            box.insert("end", before)
            if sep:
                box.insert("end", COOKIE_EXTENSION_URL, "link")
            box.insert("end", after)
            box.configure(state="disabled")
            box.pack(anchor="w")
            notebook.add(frame, text=title)
        notebook.select(tab)
        ttk.Button(win, text="Đóng", command=win.destroy).pack(pady=(0, 10))

    def _open_driver_page(self) -> None:
        if self._driver_url:
            webbrowser.open(self._driver_url)

    def _redownload_dependencies(self) -> None:
        self.dep_status_var.set("Đang tải lại công cụ, vui lòng chờ...")
        self.redownload_btn.grid_remove()
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
            self.dep_status_var.set("✓ Sẵn sàng tải video")
            self.after(0, self.redownload_btn.grid_remove)
            self._check_gpu_status(ffmpeg)
        except Exception as exc:  # noqa: BLE001
            self._log(f"Lỗi khi tải lại phụ thuộc: {exc}")
            self.dep_status_var.set("Tải lại thất bại, hãy kiểm tra mạng rồi thử lại.")
            self.after(0, self.redownload_btn.grid)

    # ---------- download ----------
    def _start_download(self) -> None:
        if str(self.download_btn["state"]) == "disabled" or self._active_downloader is not None:
            return  # đang tải rồi, tránh bấm đúp chạy 2 lần
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

        self._save_settings()
        self.download_btn.configure(state="disabled")
        self.progress.configure(value=0)
        self.progress_text_var.set("Đang bắt đầu...")
        threading.Thread(target=self._run_download, args=(request,), daemon=True).start()

    def _on_download_done(self, output_path) -> None:
        if messagebox.askyesno("Xong", f"Đã lưu video tại:\n{output_path}\n\nMở thư mục chứa video?", parent=self):
            try:
                os.startfile(str(Path(output_path).parent))
            except OSError:
                pass

    _RETRYABLE_ERRORS = ("Tải video thất bại", "Không lấy được thông tin video", "Không đọc được dữ liệu video")
    _MAX_ATTEMPTS = 3

    def _run_download(self, request: DownloadRequest) -> None:
        try:
            output_path = None
            for attempt in range(1, self._MAX_ATTEMPTS + 1):
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
                    break
                except DownloadError as exc:
                    retryable = (
                        not isinstance(exc, (DownloadCancelled, CookieError))
                        and str(exc).startswith(self._RETRYABLE_ERRORS)
                    )
                    if not retryable or attempt == self._MAX_ATTEMPTS:
                        raise
                    self._log(f"Lỗi: {exc}")
                    self._log(f"Đang thử lại lần {attempt + 1}/{self._MAX_ATTEMPTS}...")
                    self._set_progress(0)
                    time.sleep(3)
            self._log("Hoàn tất!")
            self.after(0, lambda: self._on_download_done(output_path))
        except DownloadCancelled:
            self._log("Đã hủy.")
        except CookieError as exc:
            detail = str(exc)
            self._log(f"Lỗi cookie: {detail}")
            self.after(0, lambda: self._show_cookie_dialog(detail))
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
