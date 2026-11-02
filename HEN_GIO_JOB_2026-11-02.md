# Hẹn giờ job trên tool và Telegram

> Cập nhật API riêng: admin/partner cần lưu đủ key của cả hai shop bằng `/mailapi KEY_GMAILVIP KEY_1TRIEUMAIL` hoặc tại Quản lý người dùng trước khi đặt lịch. Khi đến giờ, lịch lấy API riêng hiện tại của người tạo; nguồn mua/product ID/luồng vẫn lấy cấu hình trên máy tính. Xem [hướng dẫn API riêng](API_RIENG_TELEGRAM_2026-11-02.md).

Mở lại bằng `Launch_HoshinoTool.bat` để nạp tính năng mới. Lịch chạy **một lần**, theo **giờ Việt Nam (UTC+7)**.

## Trên tool

1. Mở **Mochi Chat → Nhiệm vụ → Thiết lập job**, nhập mã mời và số bean.
2. Tích **Hẹn giờ chạy**, chọn ngày/giờ rồi bấm **Tạo lịch job**.
3. Xem lịch tại **Job đang chạy**: trạng thái **Hẹn giờ**, cột thời gian hiển thị giờ đã hẹn; rê chuột xem đủ ngày/giờ và thời điểm tạo.
4. Bấm **Hủy** trên dòng lịch hoặc **HỦY LỊCH** ở biểu mẫu để hủy trước khi chạy. Lịch bị hủy được ghi vào CSV/lịch sử như các job khác.

Job chưa đến giờ không tạo worker, không giữ slot job/proxy và không mua mail. Đến giờ, tool dùng cấu hình mail/proxy/trình duyệt hiện tại và đưa job vào hàng đợi chung. Nếu hết slot, job chuyển sang **Đang chờ** rồi **Đang xử lý** khi có chỗ. Không tự tăng giới hạn số job để chạy lịch.

Nếu đặt lịch khi đang chọn lô mail đã mua, đường dẫn lô được giữ riêng cho lịch đó và chỉ đọc khi đến giờ. Không chuyển sang mua mail mới chỉ vì sau đó đổi lựa chọn Kho tài khoản. Nếu lô không đủ mail hoặc cấu hình khi đến giờ không hợp lệ, job thất bại và đơn khách được hoàn phần chưa dùng theo sổ đơn hàng.

## Trên Telegram

Admin, partner và khách hàng đều dùng:

```text
/hen MÃ_MỜI SỐ_BEAN DD/MM/YYYY HH:MM [TÊN_KHÁCH]
/lich
/huy MÃ_JOB
```

Ví dụ, thay mã mời và ngày/giờ bằng lịch muốn chạy:

```text
/hen ABC123 1000 03/11/2026 08:30 Khách A
```

- `/hen`: tạo lịch, trả mã job và giờ hẹn. Khách hàng có thêm mã đơn và khoản tạm giữ. Tên khách là tùy chọn.
- `/lich`: xem tối đa 20 job hẹn giờ chưa kết thúc của chính người gửi, kèm trạng thái hiện tại.
- `/huy MÃ_JOB`: hủy job hẹn giờ hoặc đang chờ của chính người gửi. Job đã bắt đầu chạy không hủy qua lệnh này; quản lý việc dừng trong tool.
- `/job ...` tiếp tục tạo job chạy ngay; lựa chọn hẹn giờ trên biểu mẫu máy tính không áp vào lệnh `/job` của bot.

Bot kiểm tra chat riêng, người gửi, Chat ID và bot tạo lịch. Không xem/hủy lịch của ID khác. Admin/partner bị thu hồi quyền trước khi lịch đến giờ không được chạy lịch đó. Khi bot đang bật, chủ job nhận các trạng thái hẹn giờ, chờ, xử lý và kết quả như luồng job hiện có.

## Lưu lịch và tiền

- Lịch lưu trong `data/telegram_users.sqlite3`; metadata không chứa API key shop, key proxy hay mật khẩu mail. Không cần thêm file lịch riêng.
- Đóng tool bình thường giữ lịch chưa chạy, kể cả lịch đã đến giờ nhưng đang chờ slot. Mở lại khôi phục lịch; lịch đã quá giờ vào hàng đợi ở lần kiểm tra tiếp theo.
- Tool cần đang mở để thực hiện lịch. Tool không tự bật máy hoặc tự mở ứng dụng. Bật bot nếu muốn nhận thông báo Telegram lúc lịch chạy.
- Đơn giá và tiền tạm giữ chốt lúc tạo lịch. Mở lại hoặc chạy lịch không trừ tiền tạm giữ lần nữa; đổi đơn giá chung sau đó không đổi giá đơn đã hẹn.
- Hủy trước khi chạy hoàn toàn bộ tiền tạm giữ của khách. Khi có kết quả, thu theo bean thành công và hoàn phần còn lại như trước.
- Job đã bắt đầu chạy rồi bị ngắt đột ngột vẫn cần kiểm tra lô mail/lịch sử đơn theo cơ chế cũ; không tự chạy lại để tránh xử lý trùng. Khóa nhận lịch trong SQLite ngăn hai phiên tool cùng nhận một lịch để chạy.

## Kiểm chứng

30 kiểm thử mới về ngày giờ/UTC+7, lệnh bot và quyền sở hữu, chưa đến giờ không mua mail, hàng đợi, QTimer thực tế, lưu/mở lại, giữ giá/tiền, hủy/hoàn tiền, lỗi dữ liệu, source batch, hai luồng nhận lịch đồng thời và phục hồi lịch đang chờ khi đóng tool. Tổng 500 ca liên quan đã qua; 34 cặp mã nguồn gốc/`src` đồng bộ và cú pháp hợp lệ. Preview ở 1280×900 dùng cấu hình tạm và mock worker; không tạo đơn, mua mail hay gửi Telegram thật.
