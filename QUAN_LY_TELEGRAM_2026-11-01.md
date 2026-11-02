# Quản lý người dùng Telegram và thống kê — 01/11/2026

Tab Telegram có bảng người dùng gồm **Chat ID, tên, vai trò, job hoàn thành, job thất bại, bean đã bán và quyền truy cập**. Phía trên có các thẻ tổng quan; có ô tìm theo tên/ID và các nút thêm, lưu tên/vai trò, thu hồi hoặc khôi phục quyền.

## Sử dụng trên tool

1. Nhập Chat ID, tên và chọn `partner` hoặc `admin`, rồi bấm **Thêm người dùng**.
2. Chọn dòng trong bảng để sửa tên/vai trò; bấm **Lưu tên / vai trò**. Không đổi chủ sở hữu lịch sử job khi sửa thông tin này.
3. **Thu hồi quyền** ngăn lệnh mới và phản hồi riêng tư, nhưng giữ nguyên lịch sử và thống kê. **Khôi phục quyền** cấp lại quyền cho cùng ID với tổng cũ. Sửa tên/vai trò của ID đã thu hồi không tự cấp lại quyền.
4. Bảng và các thẻ số liệu cập nhật theo tiến độ/kết quả của job. Danh sách ID cũ được chuyển một lần thành người dùng `partner`, tên mặc định `Telegram <ID>`.

Người thao tác trực tiếp trên tool vẫn có quyền quản lý toàn bộ bảng. Không tự đặt người đầu tiên trong danh sách thành admin; quản trị viên chọn vai trò trong giao diện.

## Vai trò phân loại và quyền qua bot

Theo lựa chọn của người dùng, `admin` và `partner` **chỉ là nhãn phân loại**, không cấp quyền quản trị qua bot. Việc cho phép/thu hồi truy cập vẫn dựa vào Chat ID và trạng thái được cấp quyền trong tool.

| Chức năng | partner | admin |
|---|---|---|
| Tạo job bằng `/job` | Có | Có |
| Xem số liệu của mình bằng `/stats` | Có | Có |
| Xem thống kê của người khác | Không | Không |
| Quản lý danh sách/tên/vai trò qua bot | Không | Không |

Ví dụ:

```text
/stats
/job VBRN5H 1000 Khách A
```

Mọi thao tác quản lý người dùng thực hiện trực tiếp trong tool. Bot từ chối `/users`, `/user_add`, `/user_revoke` và yêu cầu xem thống kê của người khác ở cả hai vai trò. Đổi nhãn admin/partner không chặn lệnh, job hoặc phản hồi đang chờ của một Chat ID vẫn được cấp quyền. Có thể nhập admin/partner vào ô tìm kiếm để lọc bảng mà không thay đổi quyền.

## Thông báo nguyên nhân thất bại

Admin và partner đang được cấp quyền nhận thêm dòng **Nguyên nhân** trong kết quả job do chính họ tạo. Tool lấy nguyên nhân từ tổng kết job, hoặc các lỗi tài khoản nếu tổng kết chưa có chi tiết; gộp tối đa ba nguyên nhân khác nhau. Các lỗi thường gặp gồm shop hết/thiếu mail, số dư shop không đủ, API key chưa hợp lệ, giới hạn tốc độ, proxy/cooldown/timeout/TLS, Google yêu cầu xác minh hoặc đăng nhập lỗi, mã mời sai/đạt giới hạn và tài khoản đã dùng mã.

Ví dụ:

```text
Job mochi_example: failed
Thành công: 2/10
Lỗi: 1
Kẹo đạt: 200
Nguyên nhân: Kết nối qua proxy tới dịch vụ hết thời gian chờ.
```

Lỗi khởi chạy và yêu cầu bị từ chối do cấu hình cũng có giải thích. Nguyên nhân được diễn giải theo nhóm lỗi, không gửi nguyên văn exception, email/mật khẩu mail, token, API key hoặc URL có credentials. Nếu chưa nhận diện được lỗi, bot hướng dẫn xem log job trong tool. Job hoàn thành/hủy không thêm dòng nguyên nhân thất bại. Quyền nhận kết quả vẫn được kiểm tra theo Chat ID, trạng thái cấp quyền và đúng bot; không gửi thông báo cho người dùng khác hoặc broadcast tới các admin. Luồng thông báo khách hàng giữ như hiện có.

## Quy tắc thống kê hiện tại

- **Job hoàn thành:** số job có kết quả `completed`, không phải số tài khoản thành công.
- **Job thất bại:** mỗi job kết thúc lỗi tính một lần, gồm `failed`, mã mời không hợp lệ, chạm giới hạn và ngoại lệ. Ví dụ một job có 20 tài khoản lỗi vẫn chỉ là một job thất bại.
- **Job hủy:** lưu riêng, không cộng vào job hoàn thành hoặc thất bại. Job chưa có kết quả không bị tự đoán là thành công/thất bại khi mở lại tool.
- **Bean đã bán:** theo lựa chọn đã xác nhận, tính theo bean thực tế đã chạy thành công: số lượt nhập mã thành công × 100. Giữ cả phần bean đã chạy trong job lỗi/hủy và tiến độ đã xác nhận của job đang chạy. Đây là số lượng đã thực hiện, không phải xác nhận thu tiền.
- Ví dụ: một job hoàn thành 1.000 bean và một job lỗi sau khi chạy được 300 bean → 1 job hoàn thành, 1 job thất bại, tổng 1.300 bean đã bán.
- Chỉ tính các job có chủ sở hữu Telegram được ghi nhận. Không gán job tạo thủ công hay các job cũ chưa có dữ liệu chủ sở hữu cho một người dựa vào tên. Không tự tạo số liệu lịch sử không xác minh được.

## Lưu dữ liệu và tránh cộng trùng

Lưu mặc định tại `data/telegram_users.sqlite3`, tách khỏi Bot Token trong QSettings. Khi dùng cấu hình INI riêng, file SQLite được đặt cạnh file INI. Sao lưu file này để giữ tên, vai trò và lịch sử thống kê. Không đặt lại số liệu khi đổi tên, đổi role, tắt bot hoặc thu hồi quyền.

Mỗi job có một bản ghi gắn với Chat ID. Chủ sở hữu và yêu cầu Telegram được ghi trước khi worker khởi chạy; nếu không lưu được thì không khởi chạy phần việc có thể mua mail. Ràng buộc job ID và cặp bot/update ID ngăn tạo trùng từ cùng yêu cầu. Tiến độ được lưu tăng dần; kết quả kết thúc chỉ được chốt một lần. Thống kê được tính từ lịch sử job, không cộng lặp vào các biến đếm khi nhận lại tín hiệu.

Job đang chạy vẫn được cập nhật vào dữ liệu khi bot tắt hoặc chủ đã bị thu hồi quyền. Quyền nhận tin nhắn và việc ghi nhận kết quả là hai việc riêng. Sau lỗi đọc dữ liệu, không thực thi lệnh tạo job/quản trị khi chưa kiểm tra được quyền.

## Kiểm chứng

Các kiểm tra mới:

```powershell
py -3.14 -I tests/test_telegram_users.py
py -3.14 -I tests/test_telegram_user_ui.py
py -3.14 -I tests/test_telegram_roles.py
```

46 kiểm tra chuyên biệt qua: 15 SQLite/lưu trữ, 15 GUI/thống kê và 16 vai trò phân loại/quyền truy cập. Sau xác nhận vai trò chỉ để phân loại, đã chạy lại 74 kiểm tra Telegram liên quan, gồm 28 kiểm tra bot cũ. Các bộ Chrome/Qt, đồng thời proxy và mua mail theo lô cũng đã qua ở vòng tích hợp. Đã xem trước giao diện bằng dữ liệu giả, không dùng số liệu/người dùng thật.

Nguồn chỉnh ở gốc và `src/` được đồng bộ. Không gửi tin Telegram thật, không mua mail, đăng nhập Google hoặc nhập mã thật trong kiểm chứng. Mở lại `Launch_MochiTool.bat` để dùng bản nguồn mới; chưa đóng gói lại EXE.
