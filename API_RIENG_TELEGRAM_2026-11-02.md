# API mua mail riêng cho admin và partner

Mở lại `Launch_HoshinoTool.bat` để nạp tính năng. Mỗi Chat ID admin/partner phải lưu **đủ hai API key**: **GmailVip.vn** và **1TrieuMail.com**, kể cả khi nguồn mua chung hiện chỉ chọn một shop.

## Nhập trên máy tính

1. Vào **Telegram → Người dùng và bot → Quản lý người dùng**.
2. Chọn đúng admin/partner trong bảng. Nếu chưa có, thêm người dùng rồi chọn dòng vừa thêm.
3. Nhập **API riêng GmailVip.vn** và **API riêng 1TrieuMail.com**.
4. Bấm **Lưu 2 API mua mail**. Dòng trạng thái cho biết đang sửa API của tên/Chat ID nào.

Các ô che giá trị key. Cần chọn đúng dòng và Chat ID trên biểu mẫu phải khớp trước khi lưu. Lưu thiếu một key bị từ chối, giữ nguyên cặp đã lưu; sửa tên/vai trò không xóa API. Thu hồi quyền chặn sử dụng/cập nhật key; khôi phục quyền giữ cặp key trước đó.

## Nhập qua bot

Admin/partner nhắn riêng cho bot:

```text
/mailapi KEY_GMAILVIP KEY_1TRIEUMAIL
```

Key GmailVip đứng trước, key 1TrieuMail đứng sau. Chỉ nhập giá trị API key, không dán link API/profile và không thêm Chat ID người khác vào lệnh.

Gửi `/mailapi` không có tham số để xem trạng thái **Đã nhập / Chưa nhập** của từng shop. Bot không trả lại giá trị key và chỉ cho sửa API của chính Chat ID gửi lệnh. Lệnh trong group, từ người gửi không khớp chat riêng hoặc từ ID bị thu hồi quyền không được dùng để cập nhật API. Khách hàng không có chức năng này.

Lưu key chỉ kiểm tra định dạng/đủ hai trường, không gọi shop để xác thực, kiểm tra số dư hoặc mua mail. Shop xác thực key khi job thực sự cần mua mail.

## Phân chia cấu hình

| Nội dung | Nơi quản lý |
| --- | --- |
| Hai API key của admin/partner | Riêng theo Chat ID, nhập từ tool hoặc `/mailapi` |
| Nguồn GmailVip / 1TrieuMail / Cả hai shop | Cài đặt chung trên máy tính |
| Danh sách product ID và thứ tự ưu tiên | Cài đặt chung trên máy tính |
| Số luồng, số job cùng lúc, proxy và trình duyệt | Cài đặt chung trên máy tính |
| API dùng cho job Local và đơn khách hàng | API chung trên máy tính |

Job của admin/partner không lấy API chung hoặc API của người khác khi thiếu key. Thiếu một trong hai key sẽ chặn tạo job, gồm `/job`, `/hen` và job dùng lô mail có sẵn. Người dùng đã có trước bản cập nhật cần tự nhập cặp API riêng; tool không tự sao chép key chung vào hồ sơ.

Nếu chọn **Cả hai shop**, tool thử product ID theo thứ tự chung nhưng dùng key tương ứng của người tạo. Ví dụ `gmailvip:3445, 1trieumail:3423`: job của A dùng hai key của A, job của B dùng hai key của B. Chi phí mail vẫn ghi theo số tiền thực tế shop trừ và gắn đúng chủ job.

Job chạy ngay hoặc đang xếp hàng giữ cặp key đã lấy lúc tạo worker; cập nhật key không đổi key giữa một job đang xử lý. Job hẹn giờ lấy cặp key riêng hiện tại của chủ job khi đến giờ chạy. Nếu lúc đó thiếu key, job thất bại và không dùng key chung để thay thế. Lịch/job/CSV/thống kê không chứa giá trị key.

API riêng lưu tại bảng `user_mail_keys` trong `data/telegram_users.sqlite3`, tách khỏi hồ sơ thống kê và dữ liệu lịch. Các ô API chung và QSettings không bị ghi đè khi người dùng cập nhật API qua bot hoặc tool.

## Kiểm chứng

26 ca mới kiểm tra cặp key nguyên tử, lưu/mở lại, phân quyền, không lộ key trong phản hồi/repr/thống kê, giao diện che key, Chat ID khớp, nhiều người chạy đồng thời, một/cả hai shop, fallback mua mail giả lập, luồng/ID sản phẩm chung, Local/khách hàng, cập nhật key theo vòng đời và chi phí đúng chủ job. Tổng 526 ca liên quan đã qua; 35 cặp mã nguồn gốc/`src` đồng bộ và cú pháp hợp lệ. Chưa mua mail hoặc gửi bot thật để kiểm chứng.
