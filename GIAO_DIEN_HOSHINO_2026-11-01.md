# Giao diện hoshino-tool

Mở `Launch_HoshinoTool.bat` để dùng giao diện mới. `Launch_MochiTool.bat` vẫn trỏ tới cùng ứng dụng. Đây là thay đổi mã Python; EXE cũ chưa được đóng gói lại.

## Các trang

- **Dashboard:** số job trong phiên, đang chạy, hoàn thành, thất bại, bean thành công thực tế, proxy sẵn sàng và job gần đây. Nhấp đúp một job để mở log. Các tổng theo phiên giảm khi xóa job khỏi danh sách; thống kê Telegram lưu SQLite vẫn giữ lịch sử.
- **Mochi Chat → Nhiệm vụ:** biểu mẫu hai cột; lưu nháp; các tab Thiết lập job, Job đang chạy và Log job. Nút Log trong bảng mở đúng nhật ký của job. Tạo account có sẵn bean được đánh dấu chưa hỗ trợ.
- **Mochi Chat → Kho tài khoản:** dùng lại file trong `data/mail_batches/`, kiểm tra số mail chưa hoàn tất; bật Dùng lô mail đã mua để job tiếp theo không mua thêm mail.
- **Proxy Pool:** giữ nguyên trang, bảng, import/export, kiểm tra và xoay proxy hiện có.
- **Cập nhật Proxy TXT:** có Nhập TXT, Lưu TXT, Xuất TXT và Dán proxy; [hướng dẫn định dạng mỗi dòng một proxy](PROXY_TXT_2026-11-01.md).
- **Telegram:** cấu hình bot; quản lý tên, Chat ID, nhãn admin/partner, quyền truy cập và số liệu thực tế đã lưu. Vai trò chỉ để phân loại.
- **Cài đặt chung → Hạ tầng chung:** nguồn proxy, key xoay, bốn tùy chọn đường mạng và số luồng/proxy.
- **Cài đặt chung → Mochi Chat:** nhà cung cấp mail, API key, ID sản phẩm, số luồng Direct và chế độ trình duyệt ẩn. Bấm Lưu thay đổi để lưu các ô đã nhập.

Không có mục MiraiMind hoặc License trong giao diện mới. Giữ namespace cấu hình cũ để dữ liệu proxy, mail và Telegram tiếp tục được nạp. Telegram và nút tạo job dùng chung các ô cấu hình, không có bản cấu hình riêng cho mỗi trang.

Ô Tổng tiền (VND) là thông tin giá trị job, không thay đổi phí mua mail của shop. Giá trị xuất hiện khi trỏ chuột vào ô tên/job trong danh sách phiên. Lưu nháp chỉ lưu biểu mẫu, không mua mail hoặc khởi chạy job.

## Thêm tài nguyên hình ảnh

Bấm **Mở thư mục hình ảnh** ở cuối thanh bên, hoặc mở:

```text
D:\TTMTool (1) - Copy\gui\assets\branding\
```

Chép file theo tên sau và mở lại tool:

| Tên file | Dùng cho |
| --- | --- |
| `logo.png` hoặc `logo.svg` | Logo trên thanh bên |
| `app.ico` | Icon cửa sổ Windows |
| `sidebar-background.png` hoặc `.jpg` | Nền thanh bên |
| `dashboard-banner.png` hoặc `.jpg` | Ảnh Dashboard |
| `nav-dashboard.svg` hoặc `.png` | Icon Dashboard |
| `nav-mochi.svg` hoặc `.png` | Icon Mochi Chat |
| `nav-proxy.svg` hoặc `.png` | Icon Proxy Pool trên thanh bên |
| `nav-telegram.svg` hoặc `.png` | Icon Telegram |
| `nav-settings.svg` hoặc `.png` | Icon Cài đặt chung |

[README trong thư mục ảnh](gui/assets/branding/README.md) có kích thước gợi ý và thứ tự ưu tiên định dạng. Không cần có đủ ảnh; thiếu file thì dùng giao diện chữ/màu/icon mặc định. Không cần sao chép tài nguyên tùy chỉnh sang `src/gui/assets/`. Tài nguyên bên trong trang Proxy tiếp tục dùng thư mục cũ.

## Kiểm chứng

Kiểm thử dùng cấu hình INI và SQLite tạm, bot và worker giả lập. Không dùng mail thật, mua mail, gửi Telegram hay nhập mã mời khi kiểm tra giao diện.

Đã qua 133 kiểm thử: 17 giao diện hoshino, 24 browser/GUI, 28 Telegram bot, 15 Telegram user UI, 16 vai trò và 33 mail batch. 17 cặp file nguồn gốc/`src` giống nhau và biên dịch hợp lệ. Đã xem ảnh các trang ở 1280 × 900 và biểu mẫu job ở 1120 × 760.

```powershell
py -3.14 -I tests/test_hoshino_ui.py
py -3.14 -I tests/test_browser_gui.py
py -3.14 -I tests/test_telegram_bot.py
py -3.14 -I tests/test_telegram_user_ui.py
py -3.14 -I tests/test_telegram_roles.py
py -3.14 -I tests/test_mail_batches.py
```
