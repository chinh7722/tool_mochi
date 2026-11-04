# Quản lý VPS bằng web local

Web dùng cùng bố cục, dữ liệu và API VPS với ứng dụng Windows. Giao diện chạy
trên máy tính của bạn tại `http://127.0.0.1:8787/`. HTML, CSS, JavaScript, logo
và biểu tượng đều nằm trong dự án; không dùng CDN, tài khoản web hoặc hosting.

## Cách mở

1. Mở `Open_VPS_Tunnel.bat`, đăng nhập SSH và giữ cửa sổ tunnel như khi dùng phần mềm.
2. Mở `Launch_Web_VPS_Manager.bat`. Trình duyệt mặc định tự mở giao diện.
3. Giữ cửa sổ **hoshino-tool - Web local quan ly VPS** mở trong lúc sử dụng web.

Web tự đọc kết nối tại `data/remote_connection.json`, dùng chung với phần mềm.
Nếu cần đổi, vào **Cài đặt chung → Hạ tầng chung → Kết nối VPS**. Địa chỉ API
mặc định là `http://127.0.0.1:8765` qua tunnel; để trống token để dùng token đã lưu.
Kết nối hợp lệ được lưu trên máy sau khi xác minh với VPS.

Nếu trình duyệt báo chưa có phiên local, mở lại bằng file launcher để tạo phiên.
Tải lại trang sau đó vẫn dùng phiên hiện tại. Không mở trực tiếp `web/index.html`.
Nếu port 8787 đang dùng, đóng cửa sổ web local cũ hoặc chạy:

```powershell
.\Launch_Web_VPS_Manager.bat --port 8788
```

Trình quản lý web chỉ cần Python và thư viện chuẩn để chạy. Môi trường `.venv`
của dự án được ưu tiên khi có sẵn. Có thể chạy trực tiếp:

```powershell
python -I main_mochi_web.py
```

## Các chức năng

- **Dashboard:** thống kê tổng quan, job gần đây và truy cập nhanh.
- **Mochi Chat:** tạo job, combo, hẹn giờ Việt Nam; theo dõi/dừng job; log và
  xuất CSV lịch sử; đưa lô JSON lên VPS và chọn lô mail; lấy mã mời tài khoản.
- **Proxy Pool / Proxy mua mail:** nhập TXT hoặc dán proxy, chọn nhà cung cấp,
  kiểm tra, xoay, xóa, tìm kiếm và xuất TXT. Tệp xuất được tải xuống trình duyệt.
- **Telegram:** cấu hình/bật/tắt bot; chọn nhóm hoặc từng người nhận thông báo;
  người dùng, quyền và hai API riêng; xem job, tài chính, đối soát mail và giá.
- **Khách hàng / SePay:** thêm/khôi phục/xóa khách, sửa số dư kèm lý do, xem
  lịch sử/đơn, chốt đơn gián đoạn, cấu hình đồng bộ và đối soát giao dịch.
- **Báo cáo CSV theo người dùng:** chọn người trong bảng Telegram, bấm
  **Xuất báo cáo CSV** rồi tải riêng lịch sử job và lịch sử mua mail.
- **Cài đặt chung:** proxy theo từng bước, nguồn mail, hai API chung, product
  IDs, đơn giá, số luồng và giới hạn job.

Danh sách tự đồng bộ khoảng 2,5 giây/lần. Có thể bấm **Đồng bộ VPS** bất cứ lúc
nào. Các ô đang nhập được giữ khi cập nhật danh sách. Job đang chạy được ưu tiên
ở đầu bảng; job vừa tạo tự mở trong tab **Job đang chạy**.

**Lưu nháp** chỉ lưu thông tin job trên trình duyệt đó. Mật khẩu tài khoản,
API key và token VPS không được lưu vào Local Storage. Token quản lý VPS được
giữ phía ứng dụng Python local; trình duyệt dùng phiên local riêng. Chat ID lớn
và số dư được chuyển chính xác giữa web và API.

## Đóng và kết nối lại

Tab **Bảng giá** lưu các mốc bean/giá và tính tỷ lệ cho job mới. **Mochi Chat →
Lịch sử tạm** có Log/Tiếp tục và xóa có xác nhận; xóa chỉ ẩn danh sách tạm,
không tác động lịch sử Thống kê. Log được lưu trên VPS qua restart. Thống kê
có thêm biểu đồ mã mời duy nhất mỗi ngày và ô Ngày xem với lựa chọn cả tháng.

Mục **Thống kê** trên thanh bên có các biểu đồ theo tháng, bấm cột để xem chi
tiết theo ngày và người. Lịch sử job đã chuyển vào đây, có bộ lọc người/tất cả.
Nút **Tiếp tục** ở từng job tạo phần bean còn thiếu; **Dừng** hỏi xác nhận.
Log tự nối dòng mới mỗi giây. Xem [hướng dẫn mới](../THONG_KE_VA_TIEP_TUC_JOB_2026-11-04.md).

Đóng trình duyệt, dừng web local hoặc mất tunnel vẫn để job, lịch combo, bot và
SePay tiếp tục chạy trên VPS. Chỉ các nút **Dừng job**, **Tắt bot**, **Dừng gửi**
hoặc **Tắt đồng bộ** mới gửi lệnh tương ứng đến VPS.

Dừng web local bằng `Ctrl+C` hoặc đóng cửa sổ launcher. Mở lại launcher để dùng
tiếp. Khi mất kết nối trong lúc gửi lệnh, kiểm tra trạng thái sau khi nối lại
trước khi thao tác lần nữa; web không tự gửi lại lệnh.

Web chỉ lắng nghe `127.0.0.1`, không mở cho mạng LAN hoặc Internet. Kết nối đến
VPS vẫn cần mạng và SSH tunnel. Yêu cầu này không biến VPS thành web server.

## Kiểm chứng ngoại tuyến

```powershell
.venv\Scripts\python.exe -I tests/test_local_web_server.py
.venv\Scripts\python.exe -I tests/test_local_web_browser.py
```

Các bộ kiểm tra dùng API VPS giả và Chromium: phiên local, CSRF/Origin/Host,
giữ token ở local, dữ liệu lớn chính xác, job combo/hẹn giờ, người nhận thông
báo, người dùng, ví, proxy, lô mail, cấu hình và trình duyệt. Không mua mail,
gửi thông báo Telegram hoặc giao dịch SePay thật để kiểm chứng.
