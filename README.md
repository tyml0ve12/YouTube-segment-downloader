# Youtube Segment Downloader (bản Python)

Bản viết lại bằng Python của [YoutubeSegmentDownloader](https://github.com/jim60105/YoutubeSegmentDownloader),
giữ nguyên logic tải + cắt đoạn video chính xác (tải nhanh bằng `yt-dlp --downloader ffmpeg`,
sau đó cắt lại chính xác bằng `ffmpeg -sseof`), nhưng không cần cài .NET, không cần trình cài đặt.

## Yêu cầu

- Python 3.10 trở lên (có sẵn tkinter, thường đi kèm bản cài Python chuẩn từ python.org).
- Không cần cài thêm thư viện pip nào.
- Không cần cài sẵn yt-dlp/FFmpeg — chương trình tự tải về thư mục `python/bin` khi chạy lần đầu
  (cần kết nối mạng cho lần chạy đầu tiên).

## Chạy chương trình

```bash
cd python
python main.py
```

Giao diện hiện ra với các trường:

- **Link Youtube**: dán link video hoặc chỉ id video.
- **Chỉ tải một đoạn (segment)**: bật để nhập thời gian bắt đầu/kết thúc (đơn vị: giây).
  Tắt đi để tải nguyên video.
- **Thư mục lưu**: nơi lưu file mp4 kết quả.
- **Format**: để trống thì tự chọn chất lượng cao nhất khớp độ phân giải
  (giống hành vi mặc định của bản gốc). Có thể nhập format cụ thể theo
  [cú pháp format của yt-dlp](https://github.com/yt-dlp/yt-dlp#format-selection), ví dụ `303+251`.
- **Cookies từ trình duyệt**: chọn trình duyệt đang đăng nhập Youtube để tải video riêng tư/thành viên
  (đóng trình duyệt đó lại trước khi tải).

Nhấn **Tải xuống**. Log tiến trình hiển thị ở khung dưới, file kết quả được đặt tên dạng:

```
YYYYMMDD Tiêu đề video (video_id) [start_end].mp4
```

## Vì sao phải cắt 2 bước?

`ffmpeg -ss` không seek chính xác khi stream-copy, chỉ nhảy tới keyframe gần nhất trước điểm mốc.
Vì vậy chương trình tải trước một đoạn dư (từ `start` tới `end` bằng keyframe gần nhất), sau đó
dùng `ffmpeg -sseof -{duration}` để encode lại chính xác từ cuối file — y hệt cách bản C# gốc xử lý.

## Cấu trúc mã nguồn

```
python/
  main.py          # điểm khởi chạy GUI
  gui.py           # giao diện tkinter
  core/
    deps.py        # tìm & tự tải yt-dlp.exe / ffmpeg.exe
    downloader.py  # logic tải + cắt video (port từ Download.cs)
  bin/             # (tự tạo) chứa yt-dlp.exe, ffmpeg.exe, ffprobe.exe sau khi tải
```

## Giới hạn so với bản gốc

- Chưa hỗ trợ tự động parse link Youtube Clip (`youtube.com/clip/...`) thành id/start/end.
- Chưa có i18n, chỉ có tiếng Việt.
- Chưa đóng gói thành file .exe độc lập — cần cài Python để chạy
  (có thể đóng gói sau bằng `pyinstaller` nếu cần).
