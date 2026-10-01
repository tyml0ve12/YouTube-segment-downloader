"""Hộp thoại cập nhật bằng tkinter - lớp giao diện mỏng gọi core/updater.py.
Có thể thay bằng UI khác (PySide6...) mà không cần đổi core/updater.py."""
from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from core.updater import UpdateInfo, apply_update, check_for_update, restart_app


def check_for_update_async(root: tk.Tk, app_dir: Path, silent: bool = True) -> None:
    """Kiểm tra cập nhật ở luồng nền, không làm đơ giao diện. silent=True thì
    không hiện gì nếu đã là bản mới nhất (dùng cho lần tự kiểm tra lúc mở app);
    silent=False thì báo cả khi đã mới nhất (dùng khi người dùng tự bấm kiểm tra)."""

    def worker() -> None:
        info = check_for_update()
        root.after(0, lambda: _on_checked(root, app_dir, info, silent))

    threading.Thread(target=worker, daemon=True).start()


def _on_checked(root: tk.Tk, app_dir: Path, info: UpdateInfo | None, silent: bool) -> None:
    if info is None:
        if not silent:
            messagebox.showinfo("Kiểm tra cập nhật", "Bạn đang dùng phiên bản mới nhất.")
        return
    _show_update_dialog(root, app_dir, info)


def _show_update_dialog(root: tk.Tk, app_dir: Path, info: UpdateInfo) -> None:
    dialog = tk.Toplevel(root)
    dialog.title("Có bản cập nhật mới")
    dialog.resizable(False, False)
    dialog.transient(root)
    dialog.grab_set()

    frame = ttk.Frame(dialog, padding=16)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text=f"Phiên bản mới: {info.version}", font=("Segoe UI", 11, "bold")).pack(anchor="w")
    if info.notes:
        ttk.Label(frame, text="Có gì mới:", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(10, 2))
        notes_box = tk.Text(frame, width=50, height=6, wrap="word")
        notes_box.insert("1.0", info.notes)
        notes_box.configure(state="disabled")
        notes_box.pack(fill="both", expand=True)

    progress_var = tk.StringVar(value="")
    ttk.Label(frame, textvariable=progress_var, foreground="grey").pack(anchor="w", pady=(8, 0))
    progress_bar = ttk.Progressbar(frame, mode="indeterminate")

    btn_row = ttk.Frame(frame)
    btn_row.pack(fill="x", pady=(12, 0))

    def do_update() -> None:
        update_btn.configure(state="disabled")
        if not info.mandatory:
            later_btn.configure(state="disabled")
        progress_bar.pack(fill="x", pady=(4, 0))
        progress_bar.start(10)

        def log(msg: str) -> None:
            dialog.after(0, lambda: progress_var.set(msg))

        def worker() -> None:
            try:
                apply_update(info, app_dir, log_cb=log)
                dialog.after(0, lambda: (dialog.destroy(), restart_app(app_dir)))
            except Exception as exc:  # noqa: BLE001
                dialog.after(0, lambda: _on_update_failed(dialog, update_btn, later_btn, progress_bar, str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def do_later() -> None:
        dialog.destroy()

    def do_quit() -> None:
        root.destroy()

    update_btn = ttk.Button(btn_row, text="Cập nhật ngay", command=do_update)
    update_btn.pack(side="right")

    if info.mandatory:
        later_btn = ttk.Button(btn_row, text="Thoát app", command=do_quit)
    else:
        later_btn = ttk.Button(btn_row, text="Để sau", command=do_later)
    later_btn.pack(side="right", padx=(0, 8))

    if info.mandatory:
        ttk.Label(
            frame, text="Đây là bản cập nhật bắt buộc.", foreground="#b8860b",
        ).pack(anchor="w", pady=(6, 0))
        dialog.protocol("WM_DELETE_WINDOW", do_quit)


def _on_update_failed(dialog, update_btn, later_btn, progress_bar, message: str) -> None:
    progress_bar.stop()
    progress_bar.pack_forget()
    update_btn.configure(state="normal")
    later_btn.configure(state="normal")
    messagebox.showerror("Cập nhật thất bại", f"{message}\n\nCode cũ đã được giữ nguyên, app vẫn dùng được bình thường.")
