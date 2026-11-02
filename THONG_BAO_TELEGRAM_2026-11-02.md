# Gửi thông báo tới admin và partner được chọn

Mở lại `Launch_HoshinoTool.bat` để nạp giao diện mới.

1. Vào **Telegram → Người dùng và bot** và bật bot.
2. Tại khung **Gửi thông báo đến admin / partner**, chọn mục **Gửi đến**:
   - **Tất cả admin / partner**: chọn mọi admin và partner đang được cấp quyền.
   - **Chỉ admin**: chọn nhóm admin đang được cấp quyền.
   - **Chỉ partner**: chọn nhóm partner đang được cấp quyền.
   - **Chọn từng người**: tích tên/Chat ID muốn nhận; có **Chọn tất cả** và **Bỏ chọn tất cả**.
3. Kiểm tra số người **Đã chọn**, rồi nhập nội dung cần gửi.
4. Bấm **Gửi thông báo**. Mỗi người được chọn nhận một tin nhắn riêng từ bot đang chạy.

Mặc định là tất cả admin/partner. Lựa chọn người nhận thông báo độc lập với dòng đang chọn hoặc bộ lọc ở bảng quản lý người dùng. Local, khách hàng và các ID đã bị thu hồi quyền không nằm trong danh sách này. Không cần có API mua mail riêng mới nhận được thông báo.

Ở chế độ **Chọn từng người**, lần đầu chưa chọn ai. Nút Gửi chỉ bật khi có người nhận, nội dung hợp lệ và bot sẵn sàng. Giao diện giữ các Chat ID đã tích khi VPS cập nhật tên hoặc thứ tự danh sách; người mới không tự được tích. Người bị xóa/thu hồi quyền được bỏ khỏi lựa chọn. Nếu quyền thay đổi trước lúc VPS nhận lệnh, VPS yêu cầu làm mới và chọn lại, không tự chuyển thành gửi tất cả.

Nếu VPS chưa được cập nhật để hỗ trợ chọn người nhận, giao diện khóa nút Gửi và yêu cầu cập nhật VPS trước khi sử dụng.

## Nội dung và kết quả

- Gửi nguyên văn văn bản, giữ xuống dòng; không tự chèn tiêu đề, tên người nhận hoặc định dạng HTML/Markdown.
- Có bộ đếm tối đa **4000** theo độ dài tin nhắn; một số emoji có thể tính hơn một đơn vị. Nội dung quá dài bị chặn, không tự cắt bớt.
- Trình quản lý VPS cho phép tối đa **2000 người nhận mỗi lượt**; có thể chọn một phần danh sách để gửi riêng.
- Trạng thái hiển thị **Thành công**, **Lỗi/chưa xác nhận**, **Bỏ qua**, **Chưa hoàn tất**. Thành công chỉ tăng khi Telegram xác nhận yêu cầu gửi; chưa phải xác nhận người nhận đã đọc.
- Danh sách người nhận được chốt lúc bấm Gửi; các cập nhật danh sách sau đó không thêm người vào lượt đang chạy. Quyền được kiểm tra lại trước từng tin và trước mỗi lần thử lại. Người bị thu hồi quyền trong khi gửi được bỏ qua.
- Lỗi ở một người không chặn gửi tiếp tới những người còn lại. Bot không tự gửi lại khi mất phản hồi mạng vì tin trước có thể đã được nhận.
- **Dừng gửi** chỉ dừng lượt thông báo hiện tại; bot tiếp tục nhận lệnh và gửi kết quả job. Đóng giao diện Windows vẫn để VPS tiếp tục gửi. Khi tắt bot hoặc dừng dịch vụ VPS, lượt thông báo cũng dừng. Phần chưa có kết quả được giữ ở số Chưa hoàn tất, không báo nhầm là đã gửi.

Nội dung và lựa chọn vẫn ở ô soạn trong phiên hiện tại sau khi gửi/dừng để kiểm tra hoặc chỉnh sửa. Mỗi lần bấm Gửi sau khi lượt trước kết thúc là một lượt mới theo nhóm hoặc những người đang được chọn. Không chọn ai sẽ không gửi; tool không tự khôi phục/gửi lại thông báo khi khởi động lại.

## Giới hạn gửi

Tool giới hạn thấp hơn mức 4096 ký tự của `sendMessage` và gửi có giãn cách. Khi Telegram trả HTTP 429 kèm `retry_after`, tool chờ theo giá trị đó, thử tối đa ba lần cho yêu cầu bị từ chối. Không bật tùy chọn trả phí để tăng tốc. Tham khảo [Telegram Bot API — sendMessage](https://core.telegram.org/bots/api#sendmessage) và [Telegram FAQ — giới hạn gửi thông báo](https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this).

## Kiểm chứng

Phiên bản gửi thông báo ban đầu có 27 ca kiểm thử về người nhận đúng quyền, nội dung nguyên văn, chống lặp trong lượt, bỏ qua khi bị thu hồi, lỗi 403/mất phản hồi, chờ 429 và giới hạn retry, dừng khi đang gửi/chờ, hơn 256 người nhận, độ dài/emoji, tiến độ không tính nhầm tin mới xếp gửi và Qt thread bridge thật với API giả lập.

Cập nhật chọn người nhận bổ sung kiểm tra bốn chế độ chọn, giữ lựa chọn qua polling, từ chối danh sách rỗng/không hợp lệ/người không còn quyền, chốt đúng danh sách của từng lượt và khóa gửi khi VPS chưa hỗ trợ. Kiểm chứng dùng dữ liệu tạm và bot giả; không gửi thông báo thật cho người dùng.
