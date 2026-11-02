# Gửi thông báo tới toàn bộ admin và partner

Mở lại `Launch_HoshinoTool.bat` để nạp giao diện mới.

1. Vào **Telegram → Người dùng và bot** và bật bot.
2. Tại khung **Gửi thông báo đến admin / partner**, nhập nội dung cần gửi.
3. Bấm **Gửi thông báo**. Mỗi admin/partner đang được cấp quyền nhận một tin nhắn riêng từ bot đang chạy.

Người nhận được lấy từ toàn bộ danh sách được cấp quyền, không phụ thuộc dòng đang chọn hoặc bộ lọc tìm kiếm. Local, khách hàng và các ID đã bị thu hồi quyền không nằm trong danh sách này. Không cần có API mua mail riêng mới nhận được thông báo.

## Nội dung và kết quả

- Gửi nguyên văn văn bản, giữ xuống dòng; không tự chèn tiêu đề, tên người nhận hoặc định dạng HTML/Markdown.
- Có bộ đếm tối đa **4000** theo độ dài tin nhắn; một số emoji có thể tính hơn một đơn vị. Nội dung quá dài bị chặn, không tự cắt bớt.
- Trạng thái hiển thị **Thành công**, **Lỗi/chưa xác nhận**, **Bỏ qua**, **Chưa hoàn tất**. Thành công chỉ tăng khi Telegram xác nhận yêu cầu gửi; chưa phải xác nhận người nhận đã đọc.
- Danh sách người nhận được chốt lúc bấm Gửi; quyền được kiểm tra lại trước từng tin và trước mỗi lần thử lại. Người bị thu hồi quyền trong khi gửi được bỏ qua.
- Lỗi ở một người không chặn gửi tiếp tới những người còn lại. Bot không tự gửi lại khi mất phản hồi mạng vì tin trước có thể đã được nhận.
- **Dừng gửi** chỉ dừng lượt thông báo hiện tại; bot tiếp tục nhận lệnh và gửi kết quả job. Khi tắt bot/đóng tool, lượt thông báo cũng dừng. Phần chưa có kết quả được giữ ở số Chưa hoàn tất, không báo nhầm là đã gửi.

Nội dung vẫn ở ô soạn trong phiên hiện tại sau khi gửi/dừng để kiểm tra hoặc chỉnh sửa. Mỗi lần bấm Gửi sau khi lượt trước kết thúc là một lượt mới gửi tới toàn bộ người đủ quyền ở thời điểm đó. Tool không tự khôi phục/gửi lại thông báo khi khởi động lại.

## Giới hạn gửi

Tool giới hạn thấp hơn mức 4096 ký tự của `sendMessage` và gửi có giãn cách. Khi Telegram trả HTTP 429 kèm `retry_after`, tool chờ theo giá trị đó, thử tối đa ba lần cho yêu cầu bị từ chối. Không bật tùy chọn trả phí để tăng tốc. Tham khảo [Telegram Bot API — sendMessage](https://core.telegram.org/bots/api#sendmessage) và [Telegram FAQ — giới hạn gửi thông báo](https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this).

## Kiểm chứng

27 ca kiểm thử mới về người nhận đúng quyền, nội dung nguyên văn, chống lặp trong lượt, bỏ qua khi bị thu hồi, lỗi 403/mất phản hồi, chờ 429 và giới hạn retry, dừng khi đang gửi/chờ, hơn 256 người nhận, độ dài/emoji, tiến độ không tính nhầm tin mới xếp gửi và Qt thread bridge thật với API giả lập. Toàn bộ 553 ca liên quan đã qua; 36 cặp mã nguồn gốc/`src` đồng bộ, cú pháp hợp lệ. Preview dùng dữ liệu tạm, không gửi thông báo thật cho người dùng.
