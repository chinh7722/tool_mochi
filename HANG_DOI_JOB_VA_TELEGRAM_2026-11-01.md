# Số job chạy cùng lúc và thông báo Telegram

> Bổ sung 02/11: [hẹn giờ job](HEN_GIO_JOB_2026-11-02.md). Lịch chưa chạy được giữ khi đóng/mở lại, gồm cả lịch đã đến giờ nhưng còn chờ slot. Job chạy ngay trong hàng đợi giữ cơ chế hủy khi đóng tool như mô tả dưới đây.

Mở lại bằng `Launch_HoshinoTool.bat` để nạp mã nguồn mới.

## Nhập proxy

Ở **Proxy Pool** hoặc **Proxy mua mail**, chọn nhà cung cấp trước khi dán danh sách hoặc nhập TXT:

- **Proxy.vn:** `ip:port|key_xoay`, ví dụ `192.0.2.10:32001|KEY_XOAY_1`.
- **muaproxyxoay.net:** `ip:port:user:pass|key_xoay`.

Mỗi proxy một dòng. Khung nhập đổi ví dụ theo nhà cung cấp đã chọn. Proxy.vn không cần thêm `user:pass`. Tab mua mail chỉ nhận proxy có key xoay. Các địa chỉ ví dụ chỉ để minh họa.

## Giới hạn số job

Vào **Cài đặt chung → Mochi Chat → Số job chạy cùng lúc** và nhập từ 1 đến 100.

Đây là giới hạn tổng số job của cả Local, admin, partner và khách hàng. Ví dụ đặt 2 rồi tạo 5 job: 2 job xử lý, 3 job chờ. Job chờ chưa chạy worker và chưa mua mail. Khi một worker kết thúc và dọn dẹp xong, job tiếp theo được chạy theo thứ tự tạo.

Giới hạn này áp dụng ngay và được lưu. Tăng số job sẽ chạy thêm các job chờ; giảm số job không ngắt những job đã chạy, chỉ ngừng cấp thêm tới khi số đang chạy thấp hơn giới hạn. Số luồng tài khoản trong mỗi job vẫn dùng cấu hình luồng/proxy hiện có.

Trang **Mochi Chat → Job đang chạy** hiển thị tổng đang xử lý/đang chờ và trạng thái từng job. Bấm **Dừng** ở job chờ để hủy: job không mua mail, được ghi CSV và hoàn toàn bộ tiền tạm giữ nếu là đơn khách hàng. Hãy dừng trước khi xóa job khỏi danh sách.

Đóng tool bình thường hủy các job đang chờ. Hàng đợi thực thi hoạt động trong phiên mở tool; trạng thái được ghi SQLite nhưng chưa tự chạy lại sau khi tiến trình bị ngắt đột ngột. Có thể kiểm tra/chốt đơn bị gián đoạn trong lịch sử khách hàng như trước.

## Thông báo Telegram

Admin, partner và khách hàng đều được nhận trạng thái cho **job chính mình tạo**:

1. Sau khi tạo: **Đang xử lý** nếu có chỗ, hoặc **Đang chờ (vị trí …)** nếu phải xếp hàng.
2. Khi rời hàng đợi: **Đang xử lý**, kèm mã job; khách hàng có thêm mã đơn.
3. Khi kết thúc: **Đã hoàn thành**, **Thất bại** hoặc **Đã hủy**, kèm số tài khoản thành công và bean thực tế. Khách hàng tiếp tục nhận tiền đã thu và số dư; admin/partner tiếp tục nhận lý do thất bại đã lọc thông tin nhạy cảm.

`/donhang` của khách hiển thị Đang chờ/Đang xử lý theo trạng thái đã lưu. Không gửi trạng thái trùng hoặc gửi sang bot khác; ID admin/partner bị thu hồi không còn nhận thông báo riêng.

## Kiểm chứng

- 16 kiểm thử mới về FIFO, giới hạn chung cho bốn nguồn job, tăng/giảm giới hạn, giữ chỗ trước `QThread.start`, chờ thread thực sự thoát, hủy/đóng tool, CSV, giữ/hoàn tiền, lỗi khởi chạy muộn, đúng người nhận và định dạng Proxy.vn ở hai tab.
- Bộ kiểm thử liên quan gồm 445 ca đã qua; mã nguồn gốc và `src` đồng bộ, cú pháp hợp lệ.
- Preview giao diện dùng cấu hình tạm, không khởi chạy bot, mua mail hoặc đổi IP thật.
