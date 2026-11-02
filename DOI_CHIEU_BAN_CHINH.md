# Đối chiếu cách xử lý proxy với bản chính — 31/10/2026

Nguồn đối chiếu: `D:\TTMTool (1)\TTMTool.exe.iou_bak`, bản EXE Nuitka được cung cấp cùng bộ cài. Đọc tĩnh bằng decoder hiện có; không chạy EXE hoặc thay đổi cơ chế license. Bản chính không có file Python Mochi rời, nên phần triển khai được dựng lại theo constants, chữ ký hàm, thuộc tính và docstring đọc được; không tuyên bố đã sao chép nguyên thân hàm.

## Hành vi đã xác nhận và áp dụng

| Phần | Dữ liệu đọc được từ bản chính | Bản local sau sửa |
|---|---|---|
| HTTP Mochi | `AsyncSession(proxies=..., impersonate="chrome_android", timeout=45.0)` | Giữ cấu hình mặc định này; xác minh TLS và proxy tường minh vẫn bật |
| Giới hạn kết nối | Không có `CONNECTTIMEOUT_MS=10000` trong constructor fetcher | Bỏ giới hạn kết nối 10 giây thêm ở bản local; dùng timeout 45 giây |
| HTTP/DNS mặc định | Không có ép HTTP/1.1 hoặc `CONNECT_TO` trong constructor | Không áp dụng HTTP/DNS override trước request đầu tiên; phần tương thích chỉ dùng sau lỗi request |
| Mô hình proxy | `proxy_url`, `proxies`, `check_info` | Thêm các thuộc tính/hàm tương thích; hai khóa HTTP/HTTPS cùng trỏ tới URL proxy có credentials được mã hóa |
| Check proxy | `ProxyModel.check_info(timeout=10000, max_timeout_retries=2)` | Kiểm tra IP ra, tối đa hai timeout giữa các nguồn; giới hạn tổng 30 giây |
| Nguồn kiểm tra | `ip-api.com`, `ipwho.is`, `ipinfo.io`, `ip2location.io`, `ipapi.co`, `ipify`, `ipify64` | Giữ đúng thứ tự URL đọc được; kiểm tra IP hợp lệ, chuyển nguồn khi API lỗi, dừng khi HTTP 407/lỗi gateway |
| Điều kiện Mochi HEAD | Không thấy bước bắt buộc HEAD Mochi trong runner gốc | Bỏ điều kiện này khỏi chạy job và Check proxy; chẩn đoán HTTPS riêng vẫn có thể dùng khi cần |
| Xoay Proxy.vn | GET `https://proxyxoay.shop/api/get.php`, `key`, timeout 30 giây | Mặc định gửi key; giữ tham số nhà mạng/tỉnh nếu người dùng chủ động chọn |

Yêu cầu vận hành của người dùng tiếp tục được giữ: dùng endpoint hiện có, chỉ tự xoay sau lỗi request; Google có thể đăng nhập trực tiếp trong khi API Mochi vẫn qua proxy. Nhánh phục hồi dùng cùng tài khoản/token, có giới hạn và dừng trước khi mua tiếp nếu phục hồi thất bại.

## Dấu vết để kiểm tra lại

- `analysis_mochi/binary_format/decoded_mochi_constants.txt`: stream `core.platforms.mochichat.fetcher`, constructor và tuple `proxies/impersonate/timeout`.
- `.tmp/original-proxy-constants.json`: constants đọc trực tiếp từ bản EXE gốc cho `proxy_core.schemas`, `proxy_core.proxy_info`, `core.proxy.checker`, fetcher và broker.
- Fetcher stream SHA256: `f3ba69d11993a40f271d3688976e31e560baef6f752111720c518dfba1951c48`.
- Kiểu phản hồi IP được đối chiếu với [ip-api](https://ip-api.com/docs/api:json), [ipwho.is](https://ipwhois.io/documentation) và [ipify](https://www.ipify.org/).

## Kiểm chứng và giới hạn

- Bộ kiểm tra có thêm 13 tình huống cho cách xử lý bản chính: credentials, thứ tự nguồn, timeout, HTTP 407, dữ liệu không hợp lệ, `check_info` milliseconds, session mặc định và runner không gọi HEAD Mochi.
- Kiểm tra thực tế không xác thực trên proxy trong log: kiểm tra IP thành công qua `ip-api.com`; session mới dùng cấu hình gốc nhận HTTP 200 từ trang chủ Mochi.
- So sánh thư viện riêng trong `.tmp`: `curl_cffi 0.16.3` của bản chính cũng có một lần gặp curl 35 sau khoảng 25 giây, trong khi bản 0.17.0 hiện cài nhận HTTP 200 ở phép thử tương ứng. Điều này không chứng minh phiên bản thư viện là nguyên nhân; giữ thư viện hệ thống hiện tại, không tự hạ phiên bản.
- Không mua mail, đăng nhập Google hoặc gửi mã mời thật trong kiểm chứng. HTTP 200 của trang chủ xác nhận đường HTTPS ở lần thử đó, không chứng minh toàn bộ luồng nhập mã đã thành công.

Chạy bằng `Launch_MochiTool.bat` hoặc `py -3.14 -I main_mochi_gui.py`; chưa đóng gói lại EXE.
