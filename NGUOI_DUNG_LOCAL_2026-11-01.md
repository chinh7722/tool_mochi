# Local — các job tạo từ máy tính

Trong trang **Telegram**, bảng người dùng luôn có dòng **Local** ở đầu. Không cần thêm Chat ID hoặc bật bot để sử dụng dòng này.

- Job tạo bằng nút **Tạo & chạy job** ở Mochi Chat tự được ghi cho Local trước khi khởi chạy.
- Job tạo bằng bot vẫn thuộc người gửi Telegram tương ứng.
- Local có số job hoàn thành/thất bại, bean thực tế, doanh thu, chi phí mail và lợi nhuận. Các số liệu được lưu qua lần mở lại tool.
- Giá bán dùng giá chung VND / 100 bean đã lưu ở Telegram và được chốt lúc tạo job, giống job từ bot. Chi phí ghi cho toàn bộ mail đã mua, kể cả mail chưa dùng; dùng lại lô cũ không tính chi phí lần hai.
- Thiếu giá hoặc chi phí thì hiện Chờ đối soát và sử dụng hộp Giao dịch / đối soát hiện có.

Chọn Local rồi bấm **Xem job**, hoặc nhấp đúp dòng Local, để xem danh sách job đã lưu cùng trạng thái, bean và số tiền. Nút Xem job cũng dùng cho các người dùng Telegram khác. Thời gian trong danh sách hiển thị GMT+7.

Local là dòng hệ thống cố định, không sửa tên/vai trò hoặc thu hồi quyền. Cột Chat ID hiển thị LOCAL, cột quyền hiển thị Máy tính. Local không được đưa vào danh sách Chat ID cho bot, không nhận tin nhắn Telegram và không được tính vào thẻ số người dùng Telegram được cấp quyền. Các thẻ tổng job/bean/tài chính bao gồm cả Local.

Các job mới từ máy tính được ghi nhận từ bản cập nhật này. Không tự suy đoán lại trạng thái/giá bán của các job cục bộ cũ chưa được lưu. Các job và số tiền của người dùng Telegram đã lưu trước đây vẫn giữ nguyên.

Khi mở cơ sở dữ liệu cũ, tool sao lưu thành `telegram_users.before_local_<id>.sqlite3` rồi chuyển cấu trúc trong một giao dịch, kiểm tra các liên kết lịch sử. Dữ liệu chính vẫn ở `data/telegram_users.sqlite3`.

16 kiểm thử Local mới và tổng 162 kiểm thử liên quan đã qua, gồm chuyển dữ liệu cũ/rollback, giữ lịch sử và tiền, ghi chủ job trước worker, bot tắt, không gửi thông báo Local, tách job bot và job máy tính, bảo vệ dòng Local, xem danh sách và lỗi khởi chạy. Tất cả dùng dữ liệu/SQLite/worker giả lập; không mua mail hoặc gửi bot thật.

```powershell
py -3.14 -I tests/test_local_user.py
```

Mở lại tool bằng `Launch_HoshinoTool.bat` để áp dụng. Chưa đóng gói lại EXE.
