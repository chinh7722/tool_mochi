# Lịch sử job và file CSV

Mở lại bằng `Launch_HoshinoTool.bat`, vào **Mochi Chat → Nhiệm vụ → Lịch sử job**.

- Job Local và Telegram được tự lưu khi kết thúc: hoàn thành, thất bại, hủy, mã không hợp lệ hoặc đạt giới hạn.
- File mặc định: **`data/job_history.csv`** trong thư mục tool. CSV dùng UTF-8 có BOM và tiêu đề tiếng Việt để mở bằng Excel.
- Tab lịch sử giữ dữ liệu qua lần mở lại tool và khi xóa job khỏi danh sách phiên hiện tại.
- Có tìm theo mã job/mã đơn, người tạo/Chat ID, tên khách/job hoặc mã mời; có lọc kết quả. Rê chuột vào ô để xem mã đơn, thời gian và số lần thử/lỗi. Kéo thanh cuộn ngang để xem các cột bên phải.
- **Mở CSV** dùng ứng dụng mặc định của máy; **Mở thư mục** mở nơi lưu. **Làm mới** đọc lại dữ liệu và ghi tiếp các bản đang chờ.

## Nội dung được lưu

CSV có mã job, mã đơn khách hàng (nếu có), tên người tạo và Chat ID, tên khách/job, mã mời, thời gian tạo/kết thúc theo giờ Việt Nam, kết quả, số tài khoản mục tiêu/đã thử/thành công/lỗi, bean mục tiêu và bean thực tế.

Job tạo từ máy hiển thị Local. Job Telegram giữ tên người tạo tại thời điểm tạo job. Khi hủy hoặc gặp ngoại lệ, phần tiến độ thành công đã xác nhận được giữ lại trong lịch sử. Lịch sử không lưu mật khẩu mail, token đăng nhập hoặc API key; nội dung lỗi kỹ thuật vẫn nằm trong log phiên.

Mỗi mã job chỉ có một bản kết thúc; nhận lặp tín hiệu không tạo dòng trùng hoặc thay đổi snapshot đã lưu. Job đang chạy chưa được thêm vào CSV. Lần mở đầu sau cập nhật sẽ bổ sung các job đã kết thúc có trong SQLite của tool. Với lịch sử cũ chưa từng lưu tên khách/job, ô đó để trống; tên người tạo lấy từ hồ sơ đang có khi chuyển lần đầu. Job cũ chưa có kết quả cuối không bị suy đoán thành thất bại.

## Khi CSV đang mở hoặc chưa ghi được

Nếu Excel khóa file hoặc có lỗi ghi CSV, bản kết thúc được giữ ở **`data/job_history_pending/`** và tool báo tại tab lịch sử/log. Đóng CSV trong Excel rồi bấm **Làm mới**, hoặc mở lại tool để ghi tiếp. File CSV cũ được thay nguyên khối sau khi ghi thành công, tránh để lại file bị ghi dở. Hai tiến trình dùng cùng file có khóa ghi chung.

Không xóa thư mục bản đang chờ nếu cần giữ lịch sử chưa ghi được. Nếu đã tự sửa CSV làm sai tiêu đề/cấu trúc, tool giữ nguyên file và báo lỗi để kiểm tra.

## Kiểm chứng

18 kiểm thử mới và tổng 237 kiểm thử liên quan đều qua: ghi/đọc qua khởi động lại, chống trùng, ghi đồng thời ở luồng và tiến trình, file bị khóa/lỗi cấu trúc, phục hồi bản đang chờ, dữ liệu tiếng Việt và ô CSV, metadata Local/Telegram, lịch sử cũ, giữ tiến độ khi hủy/lỗi và tìm/lọc trên giao diện. Kiểm chứng dùng dữ liệu giả lập, không chạy tài khoản hoặc gửi bot thật. Đã xem giao diện ở 1280×900; 29 cặp nguồn gốc/src đồng bộ. Chưa build lại EXE.
