# Lấy mã mời và thông tin tài khoản Mochi

Mở lại bằng `Launch_HoshinoTool.bat`, vào **Mochi Chat → Lấy mã mời**.

## Cách dùng

1. Nhập **email Google** và **mật khẩu Google** của tài khoản muốn tra cứu.
2. Bấm **Đăng nhập & lấy mã mời**.
3. Khi thành công, bảng cập nhật **Email**, **Tên hiển thị**, **Mã mời**, **Beans**, **ID Mochi** và **thời gian cập nhật theo giờ Việt Nam**.
4. Chọn dòng rồi bấm **Sao chép mã mời**, hoặc **Xuất CSV** để lưu danh sách đã thu thập.

Mỗi lần thực hiện một tài khoản. Có thể nhập tài khoản tiếp theo sau khi phiên trước đã đóng xong. Nút **Hủy tra cứu** hủy lần hiện tại và dọn phiên trình duyệt/kết nối.

Mật khẩu được che khi nhập và xóa khỏi ô sau khi bắt đầu chạy. Kết quả lưu tại `data/mochi_account_profiles.sqlite3`; không lưu mật khẩu, Google ID Token hay token Mochi vào dữ liệu này, CSV hoặc phần tiến độ. Mỗi email có một kết quả mới nhất; tra cứu lại thành công sẽ cập nhật dòng đó. Lần tra cứu lỗi giữ dữ liệu thành công trước đó và thời gian cập nhật cũ.

**Làm mới danh sách** đọc lại kết quả đã lưu. Để cập nhật số dư beans từ server, thực hiện lại đăng nhập/tra cứu. Nếu lưu dữ liệu tạm thời bị lỗi, kết quả vẫn ở bảng trong phiên hiện tại để sao chép/xuất CSV; làm mới sẽ thử lưu lại và giữ kết quả chưa lưu. Xuất CSV UTF-8 BOM có đủ sáu trường, bảo vệ ô văn bản khỏi bị đọc thành công thức bảng tính.

## Đường mạng và trình duyệt

Nút **Cài đặt đường mạng** mở Cài đặt chung. Tính năng dùng các lựa chọn đang có:

- **Sử dụng proxy** là công tắc chung.
- **Login Google qua proxy** áp dụng cho bước Google.
- **Đăng nhập Mochi qua proxy** áp dụng cho đăng nhập API và hai API đọc thông tin.
- **Chạy ẩn** áp dụng cho trình duyệt đăng nhập Google. Có thể bỏ chọn để quan sát/xử lý yêu cầu xác minh Google.

Nguồn Proxy Pool hoặc key proxy dùng như cấu hình tài khoản hiện có. Nếu cần proxy, lần tra cứu giữ một slot chung với các tác vụ tài khoản khác và chờ khi slot đang bận. Không lấy proxy từ pool mua mail. Những lựa chọn mua mail/nhập mã mời không quyết định tuyến mạng của phần tra cứu này.

Tính năng không yêu cầu API mua mail và không tạo job/đơn hàng hoặc doanh thu/chi phí trong bảng Telegram. Nó đăng nhập tài khoản được nhập rồi đọc hồ sơ và mã mời của chính phiên đó.

## Đối chiếu với kết quả phân tích và XAPK

Đã đối chiếu SHA-256 XAPK/base APK/Hermes bundle với [sha256.json](mochi-api-analysis/sha256.json). Android manifest xác nhận `com.yuedong.mochi`, phiên bản **1.3.2**, build **38**.

| Dữ liệu | API đã xác định | Trường trong dữ liệu trả về |
| --- | --- | --- |
| ID tài khoản | `GET /api/user/me` | `id` |
| Tên hiển thị | `GET /api/user/me` | `nickname`, dùng `Mochi User` khi tên trống như app |
| Số dư beans | `GET /api/user/me` | `remainingQuota`, giữ số thập phân, không chia/nhân đơn vị |
| Mã mời của tài khoản | `GET /api/invites/my-code` | `code` |

Hai API đọc dùng Bearer token lấy được sau đăng nhập Mochi bằng Google. Phản hồi phải có HTTP thành công và envelope `{ok: true, data: {...}}`. Số dư 0 hợp lệ; thiếu/sai `remainingQuota` báo lỗi, không ghi một số 0 giả. Các trường ví chuyển tiền không được dùng thay số beans.

Chứng cứ tĩnh: [API_CATALOG.md](mochi-api-analysis/API_CATALOG.md) và `bundle.pseudo.js` tại `516316` (getData), `688452–688469` (id/tên), `690164–690186` (số dư hiển thị), `693900` (định dạng số), `1082456–1082462` (mã mời), `1145580` (Google web client ID). Cấu hình phiên bản/build/chứng chỉ được giới hạn cho phần tra cứu mới.

## Kiểm chứng và giới hạn

36 kiểm thử backend và 27 kiểm thử Qt mới đã qua; tổng 657 ca liên quan bao gồm các chức năng hiện có, TLS và DNS đều qua. 38 cặp mã nguồn gốc/`src` đồng bộ và cú pháp hợp lệ. Có kiểm tra HTTP/auth/schema, số dư thập phân/0, dữ liệu thiếu, hủy, dọn phiên, trả proxy, bảo vệ mật khẩu/token, CSV, lưu/mở lại và xuất khi kho dữ liệu không mở được.

Các phép thử dùng auth/API giả lập và dữ liệu tạm, chưa đăng nhập Google/Mochi thật. Kết quả phân tích XAPK là chứng cứ của phiên bản được cung cấp; nếu server thay cấu trúc hoặc yêu cầu xác minh bổ sung, tool hiển thị lỗi để kiểm tra thay vì tự đoán thông tin tài khoản.
