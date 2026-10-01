# Cách phát hành tool cho máy khác (không dùng exe PyInstaller)

## Vì sao đổi cách này

Bản build bằng PyInstaller (`.exe`) dễ bị Windows Smart App Control chặn ở máy
lạ vì đây là 1 file exe "vô danh" (không chữ ký số, chưa có "reputation" nào
trên hệ thống Microsoft) — bất kể file có sạch hay không.

Cách này né hẳn vấn đề: không phát hành file `.exe` tự build. Thay vào đó cài
1 bản Python chính chủ (python.org, đã ký số bởi Python Software Foundation,
có sẵn reputation toàn cầu) rồi chạy trực tiếp code `.py` qua `pythonw.exe` —
Windows không coi đây là "unknown app" nữa.

## Cấu trúc gửi cho người dùng cuối (thư mục `dist/TaiVideoYoutubeTheoDoan/`)

```
TaiVideoYoutubeTheoDoan/         <- nén thư mục này (hoặc dùng sẵn dist/TaiVideoYoutubeTheoDoan.zip)
├── CAI DAT.bat                  <- CHỈ file này người dùng cần thấy/bấm đúp
└── _files/                      <- người dùng KHÔNG cần mở vào đây
    ├── main.py
    ├── gui.py
    ├── YoutubeSegmentDownloader.ico
    ├── core/
    │   ├── __init__.py
    │   ├── deps.py
    │   └── downloader.py
    └── install.ps1
```

## Quy trình đóng gói (dành cho người phát triển)

Sau khi sửa code trong thư mục gốc dự án (`python/`), chạy:

```
powershell -ExecutionPolicy Bypass -File make_release.ps1
```

Script này tự copy code mới nhất + `install.ps1` + `CAI DAT.bat` vào
`dist/TaiVideoYoutubeTheoDoan/`, rồi nén thành
`dist/TaiVideoYoutubeTheoDoan.zip` — chỉ cần gửi file zip này.

## Gửi cho người dùng

1. Giải nén zip ra bất kỳ đâu.
2. Bấm đúp **`CAI DAT.bat`**.
3. Chờ vài phút (lần đầu, nếu máy chưa có Python nào, cần tải ~25MB từ
   python.org; nếu máy đã có sẵn Python kèm tkinter thì bỏ qua bước này).
4. Xong — mở "Tai Video Youtube Theo Doan" từ Desktop hoặc Start Menu để dùng.

## Lưu ý

- Cần internet ở bước cài đặt lần đầu (tải Python nếu máy chưa có) và mỗi lần
  tải video (bản thân tool cũng tự tải yt-dlp.exe/ffmpeg.exe lần chạy đầu).
- Không cần quyền admin (`InstallAllUsers=0`).
- Khi có code mới, chạy lại `make_release.ps1`, gửi lại zip, người dùng chỉ
  cần bấm lại `CAI DAT.bat` — tự phát hiện đã có Python nên bỏ qua tải lại,
  chỉ copy code mới + tạo lại shortcut.
