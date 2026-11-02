# Khách hàng Telegram, số dư và nạp tiền SePay

Mở lại bằng `Launch_HoshinoTool.bat`. Telegram có ba tab: **Người dùng và bot**, **Quản lý khách hàng**, **Thanh toán SePay**.

## Khách hàng và đơn hàng

- Chat ID riêng chưa được cấp quyền được ghi nhận tự động khi nhắn bot, kể cả gửi ảnh/tin không có chữ. Khách có thể dùng `/start`, `/id` hoặc lệnh khác để bắt đầu. Không cần thao tác thêm trong tool. ID đã thu hồi quyền cũng được đưa vào danh sách khách hàng; lịch sử quyền và job cũ được giữ.
- Bảng gồm **ID khách hàng, số dư khả dụng, số đơn đã tạo, đã hoàn thành, thất bại, lịch sử giao dịch**. Tìm theo ID/tên và bấm **Xem giao dịch / đơn** để xem chi tiết.
- ID đang được cấp quyền admin/partner luôn bị loại khỏi danh sách khách. Cấp quyền cho khách hiện có sẽ chuyển ID sang nhóm người dùng được cấp quyền, giữ lịch sử cũ; thu hồi quyền sẽ phân loại lại thành khách. Core chặn thêm/khôi phục, xóa, sửa số dư hoặc cộng nạp SePay vào ID đang được cấp quyền. Không có tùy chọn đưa admin/partner vào bảng khách.
- Mỗi đơn có mã riêng bắt đầu bằng `DH`, gắn với Chat ID và mã job. Đơn lưu mục tiêu, số bean thành công, đơn giá tại lúc tạo, tiền giữ và tiền đã thu.
- Bảng **Mochi Chat → Job đang chạy** có cột **Người tạo job**: tên người gửi và Chat ID cho job Telegram, **Local** cho job tạo trên máy. Tên được ghi nhận lúc tạo job; tên khách hàng do người gửi nhập vẫn hiển thị ở cột Khách hàng / Job.
- ID admin/partner được cấp quyền và Local tiếp tục dùng luồng tạo job hiện có. Vai trò admin/partner vẫn chỉ dùng phân loại. Thêm khách hàng không cấp quyền admin/partner.

Các lệnh dành cho khách hàng:

| Lệnh | Chức năng |
| --- | --- |
| `/start`, `/help` | Đăng ký Chat ID và hướng dẫn |
| `/id` | Xem ID khách hàng, cũng là Chat ID |
| `/sodu`, `/balance`, `/stats` | Số dư khả dụng và thống kê của chính khách |
| `/nap` | Tài khoản nhận tiền, nội dung và giới hạn nạp |
| `/job MÃ_MỜI SỐ_BEAN` | Đặt đơn theo cấu hình mail/proxy trong tool |
| `/donhang`, `/orders` | Các đơn gần nhất của chính khách |
| `/giaodich`, `/history` | Các giao dịch gần nhất của chính khách |

Bot chỉ xử lý chat riêng đúng người gửi. Nội dung nhắn hoặc ảnh chuyển khoản không được dùng để cộng tiền. Nhập Chat ID người khác trong lệnh không mở được số dư hay lịch sử của họ.

## Thêm, xóa và sửa số dư trong tool

- Khách mới được ghi nhận tự động từ bot. **Thêm / Khôi phục** theo Chat ID là thao tác tùy chọn, dùng để lưu trước hoặc lấy lại hồ sơ đã xóa; chỉ áp dụng ID chưa được cấp quyền.
- Chọn dòng khách rồi bấm **Sửa số dư**. Nhập số dư khả dụng mới bằng VND và lý do; có thể tăng, giảm hoặc đặt về 0, tối đa hai số lẻ. Khoản sửa có lịch sử riêng với số dư trước/sau và số tiền thay đổi. Sửa số dư không ghi nhận thành tiền nạp SePay hoặc doanh thu.
- Tiền đang giữ cho đơn hiện tại được giữ theo đơn. Việc sửa chỉ áp dụng phần tiền khả dụng; khi đơn kết thúc, phần chưa chạy thành công vẫn được hoàn như thường lệ.
- Nếu có giao dịch mới trong lúc mở cửa sổ sửa, tool yêu cầu đóng/mở lại để đối chiếu, tránh ghi đè tiền vừa nạp hoặc hoàn.
- **Xóa khách hàng** ẩn hồ sơ khỏi danh sách và giữ số dư, đơn cùng lịch sử tài chính. Khách còn đơn chưa chốt tiền cần xử lý xong đơn trước khi xóa.
- Hồ sơ đã xóa không tự xuất hiện lại khi khách nhắn bot; dùng **Thêm / Khôi phục** với đúng Chat ID để khôi phục. Tài khoản vẫn giữ nguyên số dư và lịch sử. Khoản SePay nhận trong thời gian hồ sơ bị xóa được giữ để đối soát sau khi khôi phục.

## Giữ tiền, thu tiền và hoàn tiền

Dùng đơn giá chung **VND / 100 bean** tại tab Người dùng và bot. Cần lưu đơn giá dương trước khi nhận đơn khách hàng.

Ví dụ giá 1.000 VND / 100 bean, đặt 1.000 bean: tool giữ 10.000 VND. Nếu kết thúc với 400 bean thành công, thu 4.000 VND và hoàn 6.000 VND, kể cả khi trạng thái cuối là thất bại/hủy. Nếu không có bean thành công thì hoàn toàn bộ. Mục tiêu được làm tròn lên theo mỗi lượt 100 bean như cơ chế job hiện tại.

- Giữ tiền và tạo đơn cùng giao dịch SQLite; không đủ số dư thì không tạo job. Nhiều đơn đồng thời không tiêu được cùng một khoản tiền.
- Chốt kết quả, ghi số tiền thu và hoàn phần còn lại cùng giao dịch SQLite. Yêu cầu/kết quả trùng không trừ hoặc hoàn tiền thêm lần nữa.
- Giá thay đổi sau khi đặt không ảnh hưởng đơn đang chạy. Tiền nạp và tiền giữ chưa phải doanh thu; doanh thu đơn khách hàng là số tiền thực tế đã chốt theo bean thành công. Chi phí mua toàn bộ lô mail vẫn được ghi vào chủ sở hữu job theo cơ chế tài chính hiện có.
- Nếu máy/tool dừng giữa chừng, đơn còn giữ tiền được giữ nguyên để đối soát. Sau khi xem log, vào lịch sử khách hàng → chọn đơn ở tab Đơn hàng → **Chốt đơn bị gián đoạn theo tiến độ đã lưu**. Tool chặn chốt thủ công khi job vẫn còn trong danh sách worker hiện tại. Không tự chạy lại đơn hoặc tự suy đoán các thao tác chưa có tiến độ được lưu.

## Cấu hình SePay

1. Kết nối tài khoản ngân hàng tại SePay và tạo API Token. Tích hợp dùng [SePay API v2](https://developer.sepay.vn/vi/sepay-api/v2/giao-dich/danh-sach), xác thực Bearer và đọc giao dịch định kỳ.
2. Vào **Telegram → Thanh toán SePay**, nhập API Token, **Bank Account ID dạng UUID của SePay v2**, số tài khoản nhận, tên ngân hàng và tên chủ tài khoản. UUID khác số tài khoản ngân hàng; có thể lấy từ danh sách tài khoản ngân hàng của SePay API v2.
3. Bấm **Lưu cấu hình**, rồi **Bật đồng bộ**. Có tùy chọn tự bật khi mở tool. Đồng bộ chạy mỗi 30 giây, độc lập với bot và proxy của job, khi tool đang mở.
4. Khách gửi `/start` trước, sau đó `/nap` để xem hướng dẫn.
5. Mỗi lần chuyển **10.000–100.000 VND**, nội dung **chỉ gồm Chat ID của khách**, ví dụ `123456789`.

Lần lưu cấu hình đầu tiên chốt mốc bắt đầu nhận tiền và hiển thị trong tab. Chỉ giao dịch từ mốc này được tự cộng; không tự cộng lịch sử ngân hàng trước khi cấu hình. Lưu lại cùng tài khoản không đổi mốc. Mã giao dịch cuối đã đồng bộ được lưu để lấy tiếp khi mở tool lại.

Tool kiểm tra tiền vào, đúng UUID và số tài khoản nhận, số tiền trong giới hạn, nội dung khớp đúng một khách hàng đã lưu. Mỗi UUID giao dịch và mã tham chiếu ngân hàng chỉ được cộng một lần, kể cả đồng bộ lại hoặc lỗi mạng giữa các trang. Tiền nạp được cộng từ dữ liệu lấy trực tiếp qua API SePay đã xác thực.

Giới hạn nạp là điều kiện ghi có vào tool; ngân hàng vẫn có thể nhận khoản chuyển ngoài giới hạn. Các khoản sai nội dung, Chat ID chưa có, sai tài khoản hoặc ngoài giới hạn được ghi **Cần kiểm tra** trong bảng SePay, kèm lý do.

Để xử lý khoản tiền vào hợp lệ nhưng chưa khớp Chat ID: xác minh người chuyển, yêu cầu khách nhắn bot để ghi nhận hồ sơ (hoặc dùng Thêm / Khôi phục), chọn giao dịch, nhập Chat ID và bấm **Cộng giao dịch đã chọn vào Chat ID**. Thao tác ghi lịch sử đối soát thủ công và chống cộng trùng. Không cho phép dùng đối soát để vượt giới hạn tiền nạp hoặc bỏ qua kiểm tra tài khoản/tiền vào/mốc nhận tiền.

Không cần mở cổng nhận webhook trên máy. Cách lấy giao dịch và chống trùng tham khảo tài liệu [danh sách giao dịch](https://developer.sepay.vn/vi/sepay-api/v2/giao-dich/danh-sach) và [đối soát của SePay](https://developer.sepay.vn/vi/sepay-webhooks/doi-soat-giao-dich).

## Lưu trữ và kiểm chứng

Dữ liệu khách, đơn, ví và giao dịch SePay nằm trong `data/telegram_users.sqlite3`, cùng sổ job/chi phí hiện có. Schema mới thêm bảng, giữ nguyên dữ liệu và chủ sở hữu cũ. Sao lưu file dữ liệu khi chuyển máy. API Token lưu bằng cấu hình tool, không đưa vào lịch sử giao dịch hoặc phản hồi bot.

37 kiểm thử mới bao phủ giới hạn nạp, giao dịch trùng, sai nội dung/ngân hàng/hướng chuyển, phân trang/API lỗi, chống lộ token, số dư đồng thời, giữ/hoàn tiền nguyên khối, doanh thu thực tế và toàn bộ chi phí mail, job lỗi một phần, khởi chạy lỗi, lưu lại cấu hình, phân quyền chat và dừng worker. Tổng 298 kiểm thử liên quan đã qua qua các lượt xác minh. Kiểm chứng bằng dữ liệu giả; chưa kết nối tài khoản SePay hoặc nhận tiền thật. Giao diện đã xem ở 1280×900, nguồn gốc và `src/` đồng bộ. Chưa build lại EXE.

Cập nhật quản lý khách: thêm 20 kiểm thử cho tự ghi nhận khách, loại admin/partner, sửa số dư có đối chiếu, sửa đồng thời, tiền đang giữ, xóa/khôi phục, chuyển schema cũ và giữ chống trùng SePay. Tổng 195 kiểm thử liên quan chạy trong vòng cập nhật này đều qua; 27 cặp nguồn gốc/src đồng bộ.
