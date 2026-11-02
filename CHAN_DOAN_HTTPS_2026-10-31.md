# Chẩn đoán lỗi HTTPS ở job mochi_5fe09cc6

Job đã mua 10 mail và Google đăng nhập trực tiếp thành công. Lỗi curl 35 xuất hiện ở bắt tay TLS tới Mochi qua proxy. Việc lấy IP qua HTTP và phản hồi CONNECT 200 không xác nhận đường HTTPS hoạt động.

## Phép thử không xác thực

Chỉ kiểm tra TCP/CONNECT/TLS và gửi HEAD trang chủ công khai. Không mua mail, không đăng nhập Google, không gửi token/mã mời và không gọi API xoay proxy.

| Phép thử | Kết quả |
|---|---|
| HEAD Mochi trực tiếp | HTTP 200, khoảng 0,35 giây |
| HEAD Mochi qua proxy, profile chrome_android, HTTP/1.1 | Timeout kết nối sau khoảng 7 giây |
| HEAD qua cùng proxy, CONNECT tới IP DNS từ máy 43.159.108.186 | Timeout sau khoảng 7 giây |
| CONNECT tới IP từ máy, không dùng browser impersonation | Timeout sau khoảng 7 giây |
| Native OpenSSL tới example.com qua proxy | TCP và CONNECT 200 thành công; TLS timeout trong ngân sách 8 giây |
| Native OpenSSL tới Mochi qua proxy | TCP và CONNECT 200 thành công; TLS timeout trong ngân sách 8 giây |
| Native TLS 1.2 tới Mochi, cả tên miền và IP DNS từ máy | Đều timeout ở TLS trong ngân sách 8 giây |

Endpoint được thử: `http://160.250.166.26:10307`. Kết quả chỉ phản ánh thời điểm kiểm tra. Chúng xác nhận đường HTTPS qua proxy không hoàn tất ở các đích/cấu hình đã thử, chưa xác định lỗi cụ thể nằm tại gateway, upstream, nhà mạng hay đường tới máy chủ. Đổi profile hoặc ép TLS 1.2 không khắc phục được trong phép thử này.

Script kiểm tra công khai: `.tmp/probe_latest_mochi_tls.py` và `.tmp/probe_latest_tls12.py`.

## Những lỗi nguồn đã sửa

- Nhánh DNS trước đây chỉ đọc cache, nhưng luồng job không gọi hàm tạo cache. Sau lỗi thật curl 35/28, fetcher nay chủ động phân giải DNS từ máy và xác minh HEAD qua chính proxy trong tối đa 8 giây. Không lặp thêm HEAD theo tuyến tên miền vừa lỗi.
- Chỉ ghi route sau khi HTTPS xác minh thành công. `CONNECT_TO` giữ nguyên URL/Host/SNI và xác minh chứng chỉ theo tên miền gốc, theo [tài liệu libcurl](https://curl.se/libcurl/c/CURLOPT_CONNECT_TO.html).
- Khi cấp lại proxy, xóa route cũ và kiểm chứng lại cả khi endpoint không đổi nhưng IP ra đã đổi.
- Giữ cấu hình mặc định ban đầu; sau lỗi mới dùng HTTP/1.1 với bước kết nối tối đa 10 giây. Không tắt xác minh TLS hoặc tự chuyển API sang Direct. Tối đa 3 POST đăng nhập, dùng cùng token Google.
- Nếu chẩn đoán vẫn thất bại, đưa lý do HTTPS vào kết quả job. Không tuyên bố sửa mã đã làm proxy hiện tại hoạt động.

## Chạy lại mail đã thanh toán

1. Mở bằng `Launch_MochiTool.bat`.
2. Chọn **Dùng lô mail đã mua**, rồi chọn `data/mail_batches/mochi_5fe09cc6_57151d481935.json`.
3. Chọn proxy có đường HTTPS hoạt động và chạy lại mục tiêu mong muốn.

Chế độ này không yêu cầu API key shop, không gọi mua và không mua bù. Chỉ nạp mail chưa có trạng thái `success` hoặc `already_redeemed`; gộp kết quả vào file nguồn, giữ các bản ghi còn lại. Nạp lại file dưới khóa trước khi chạy để tránh dùng trạng thái cũ từ GUI. Giữ khóa theo file giữa các worker của cùng tiến trình, cả với lô vừa mua, để không có hai job dùng chung lô đang chạy. Khóa này không phối hợp các phiên ứng dụng chạy ở hai tiến trình khác nhau.

## Kiểm chứng

Bộ kiểm tra gồm 154 tình huống: 34 logic, 19 Chrome/Qt, 6 consent, 22 TLS/phục hồi, 11 preflight, 16 DNS, 13 đối chiếu bản chính và 33 mua lô/tùy chọn proxy/tái sử dụng mail. Request đăng nhập/nhập mã/mua hàng trong các kiểm thử đều được mô phỏng. Không dùng tài khoản đã mua để chứng minh API đăng nhập thật đã thành công.
