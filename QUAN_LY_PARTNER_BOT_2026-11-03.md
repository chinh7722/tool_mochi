# Quản lý partner trên bot Telegram — 03/11/2026

Tài khoản có vai trò **admin** và đang được cấp quyền có thể thêm, xóa quyền,
xem danh sách và xem thông tin partner trong chat riêng với bot. Chọn vai trò
admin tại **Telegram → Quản lý người dùng** trong ứng dụng quản lý Windows;
VPS và bot cần đang chạy.

| Lệnh | Chức năng |
|---|---|
| `/partner_add CHAT_ID TÊN` | Thêm partner mới hoặc khôi phục/cập nhật tên partner đã có |
| `/partner_remove CHAT_ID` | Xóa quyền truy cập của partner, giữ dữ liệu lịch sử |
| `/partners [TRANG]` | Xem danh sách partner, 10 người mỗi trang |
| `/partner CHAT_ID` | Xem thông tin và thống kê của một partner |

Ví dụ:

```text
/partner_add 123456789 Partner A
/partners
/partners 2
/partner 123456789
/partner_remove 123456789
```

Partner có thể nhắn `/id` để lấy Chat ID và gửi ID đó cho admin. Sau khi được
thêm, partner dùng `/mailapi KEY_GMAILVIP KEY_1TRIEUMAIL` để lưu đủ hai API
mua mail riêng rồi mới tạo job bằng `/job` hoặc `/combo`.

Danh sách gồm partner đang được phép và đã xóa quyền, có ghi trạng thái từng
người. Admin, Local và bản ghi giữ chỗ của đơn khách hàng không được đưa vào
danh sách partner. Thông tin chi tiết gồm tên, Chat ID, quyền truy cập, job
hoàn thành/thất bại, bean thực tế và bean yêu cầu, doanh thu, chi phí mail và
lợi nhuận hoặc tình trạng chờ đối soát. Kết quả không chứa API key, token hay
mật khẩu.

Xóa partner là thu hồi quyền truy cập. Job, bean, tài chính và hai API riêng
vẫn được lưu; thêm lại đúng Chat ID sẽ khôi phục quyền cùng lịch sử cũ. Lệnh
quản lý chỉ tác động partner, không tự nâng quyền, thay đổi/xóa admin hoặc sửa
Local. Partner dùng `/stats` để xem số liệu của mình; không có quyền quản lý
hay xem thông tin của partner khác.

Hệ thống kiểm tra lại quyền admin trong dữ liệu khi thực thi. Phản hồi có
thông tin partner đang chờ gửi bị chặn nếu người nhận đã mất quyền admin.
Tính năng mới đã qua **33 kiểm thử**: 13 lưu trữ và 20 bot/quyền/GUI bằng dữ
liệu tạm và bot giả, không gửi tin Telegram thật để kiểm chứng.
