# Hai shop mail và thứ tự sản phẩm ưu tiên

Mở lại tool bằng `Launch_HoshinoTool.bat`, vào **Cài đặt chung → Mochi Chat**.

1. Chọn **Cả hai shop (theo thứ tự ưu tiên)** ở Nguồn mua Mail.
2. Nhập API key GmailVip.vn và 1TrieuMail.com vào hai ô riêng. Mỗi ô có nút Kiểm tra số dư của đúng shop.
3. Nhập danh sách **Thứ tự ưu tiên**, ví dụ:

   ```text
   gmailvip:3445, 1trieumail:3423, gmailvip:2862
   ```

4. Bấm **Lưu thay đổi**, sau đó tạo job trên máy hoặc qua bot Telegram.

Danh sách được thử từ trái sang phải. Ví dụ trên thử sản phẩm 3445 của GmailVip, sau đó 3423 của 1TrieuMail, rồi 2862 của GmailVip. Có thể xen kẽ nhiều sản phẩm của cả hai shop. Chấp nhận dấu phẩy, dấu chấm phẩy hoặc khoảng trắng làm dấu phân cách; mục trùng shop và ID chỉ dùng lần đầu. Tên `gmailvip.vn` và `1trieumail.com` cũng được chấp nhận. Product ID phải là số nguyên dương và mỗi mục cần ghi rõ shop.

Tool yêu cầu API key cho những shop xuất hiện trong danh sách. Hai key và danh sách ưu tiên được lưu qua lần mở lại; danh sách ID riêng của từng chế độ một shop được giữ lại. Job đang chạy giữ bản cấu hình tại thời điểm tạo.

## Cách mua và chuyển sản phẩm

- Job mới mua 1 mail thử mã trước; thành công mới mua số còn thiếu. Ví dụ 10 lượt mua 1 rồi 9. Mỗi đợt mua thử sản phẩm từ trái sang phải theo cùng danh sách ưu tiên. Xem [luồng thử mã và Proxy mua mail](THU_MA_VA_PROXY_MUA_MAIL_2026-11-01.md).
- Khi shop xác nhận sản phẩm hết hàng, không đủ số lượng hoặc sản phẩm không còn hợp lệ, tool thử mục tiếp theo. Khi bật ghi chi phí, tool xác nhận số dư shop không đổi trước khi chuyển nguồn.
- Sau một lần mua thành công, tool lưu lô và bắt đầu sử dụng. Nếu shop trả thiếu, tool dùng số mail đã nhận và giữ nguyên quy tắc không tự mua bù. Nếu trả dư, toàn bộ mail được lưu, job chỉ sử dụng đủ mục tiêu.
- Khi tất cả mục đều hết hàng, job dừng và báo các mục đã thử.
- Lỗi API key, mất phản hồi giao dịch, phản hồi không rõ ràng hoặc số dư sau giao dịch chưa xác minh được sẽ dừng mua để kiểm tra lịch sử shop. Không tự đặt thêm một lô ở shop khác trong những trường hợp này.
- Giới hạn tốc độ vẫn chờ và thử lại cùng sản phẩm/lô theo cơ chế hiện có, tối đa ba lần; bật mua mail qua proxy thì callback phục hồi xoay/cấp lại proxy. Dùng proxy vừa phục hồi cho mục ưu tiên tiếp theo nếu sản phẩm sau đó báo hết hàng.

Chi phí được ghi theo đúng shop và số tiền thực tế bị trừ, gắn với người sở hữu job Local hoặc Telegram. Chọn **Dùng lô mail đã mua** vẫn đọc file JSON cũ và bỏ qua việc mua mới.

## Kiểm chứng

19 kiểm thử mới bao phủ thứ tự xen kẽ, đúng key/ID/số lượng, hết hàng ở cả hai shop, trả thiếu, phản hồi mơ hồ, debit bất thường, số dư chưa rõ, rate limit, hủy, proxy vừa xoay, lưu cấu hình và giữ riêng các chế độ một shop. Tổng 230 kiểm thử liên quan đều qua. Các kiểm thử dùng session HTTP, key và SQLite tạm; không mua mail hoặc gọi bot thật. Giao diện đã xem ở 1280 × 900. Cây nguồn gốc và `src/` đã đồng bộ; chưa build lại EXE.
