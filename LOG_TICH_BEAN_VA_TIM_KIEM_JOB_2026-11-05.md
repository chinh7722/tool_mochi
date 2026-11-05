# Log tích bean và tìm kiếm job

## Xem lỗi tích bean

Trên tool quản lý VPS hoặc web local, mở **Mochi Chat → Kho tài khoản**:

- **Log tích bean**: mở nhật ký job nhập mã mời của acc, tự nối thêm dòng mới mỗi giây khi đang xem. Vẫn mở được sau khi job thành công/thất bại hoặc VPS khởi động lại.
- **Log tạo acc**: mở nhật ký lô mua mail và tạo acc. Có thêm nút này trong bảng **Tiến độ tạo acc**, kể cả khi chưa tạo được acc nào.
- Cột **Chi tiết** giữ thông báo hiện tại của acc. Log tạo acc ghi từng bước: mua/lưu mail, chờ proxy, đăng nhập Google/Mochi, lấy mã mời/bean và chuyển sang job tích bean. Không ghi mật khẩu hoặc API key.

Job tích bean đã chạy trước bản cập nhật vẫn xem được log cũ nếu VPS đã lưu. Những thông tin đăng nhập trước đây chưa được ghi log không thể khôi phục ngược.

Nếu log báo **Giao dịch mail không được xác nhận an toàn**, job dừng trước khi nhập mã mời. Kiểm tra đơn hàng/lô mail tại shop và đối soát giao dịch trước khi thử lại để tránh mua trùng. Bản cập nhật không tự mua lại những giao dịch này.

## Tìm kiếm trong Mochi Chat

Ba phần **Job đang chạy**, **Log job** và **Lịch sử tạm** có ô tìm kiếm riêng. Nhập một phần mã mời, mã job, tên người tạo hoặc Chat ID. Tìm không phân biệt chữ hoa/thường, hỗ trợ tên tiếng Việt không dấu.

- **Job đang chạy**: lọc danh sách đã đồng bộ trong phiên VPS; xóa từ khóa để xem lại tất cả.
- **Log job**: tìm trên các job đã lưu tại VPS, kể cả job đã kết thúc hoặc đã ẩn khỏi lịch sử tạm. Nhấp đúp một dòng (tool) hoặc **Xem log** (web) để chọn nhật ký. Từ khóa tìm kiếm không cắt bỏ nội dung log đang xem.
- **Lịch sử tạm**: tìm trên toàn bộ lịch sử tạm trước khi phân trang. Từ khóa mới trở về trang đầu. Xóa lịch sử tạm vẫn chỉ ẩn danh sách, không xóa log hay lịch sử Thống kê.

Sau khi cập nhật, mở lại tool hoặc khởi động lại web local để dùng giao diện mới. Web vẫn chỉ chạy trên máy tính; công việc và log nằm trên VPS.
