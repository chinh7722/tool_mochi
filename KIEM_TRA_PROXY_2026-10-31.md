# Kiểm tra proxy sau job mochi_07b47954

> Cập nhật sau job `mochi_a46f0cbb`: ngân sách đăng nhập qua proxy có phục hồi là tối đa **4 POST**, để chuỗi TLS → HTTP/2 → timeout vẫn còn lượt sau cấp lại proxy. API đi trực tiếp hoặc không có callback phục hồi vẫn tối đa 3 POST. Xem phần cập nhật cuối báo cáo.

## Kết quả

Địa chỉ `http://160.250.166.26:10307` được parse và truyền đúng vào cả hai khóa HTTP/HTTPS. Mail và Mochi đều dùng proxy tường minh, xác minh chứng chỉ và không tự chuyển API sang Direct. Xoay IP hoạt động theo log; endpoint không đổi là hành vi bình thường của gateway xoay.

Phép thử công khai lúc khoảng 22:54 giờ Việt Nam lấy IP ra `1.54.97.145`, trùng IP sau lần xoay cuối trong log:

| Đích qua proxy | Kết quả |
|---|---|
| IP qua HTTP | HTTP 200, khoảng 0,29 giây |
| IP qua HTTPS | HTTP 200, khoảng 5,13 giây |
| Trang chủ GmailVip HTTPS | HTTP 200, khoảng 0,52 giây |
| Trang chủ 1TrieuMail HTTPS | HTTP 200, khoảng 0,29 giây |
| Trang chủ Mochi HTTPS, cấu hình mặc định | HTTP 200, khoảng 0,35 giây |
| Mochi CONNECT tới IP DNS từ máy | Timeout sau khoảng 7 giây |

Đo tuần tự bổ sung trên cùng endpoint: mặc định thành công, HTTP/1.1 theo tên miền cũng thành công, lần mặc định tiếp theo timeout; hai lần theo IP DNS từ máy timeout; quay về mặc định lại nhận HTTP 200. Vì vậy không thể kết luận proxy hỏng cố định hoặc HTTP/1.1 luôn là nguyên nhân. Đường truyền có tính gián đoạn, và tuyến DNS từ máy không giúp trong các phép thử mới.

Port trả `HTTP/1.0 200 Connection established` cho HTTP CONNECT; lời chào SOCKS5 trên chính port này timeout. Không có bằng chứng để tự đổi gateway sang SOCKS5 hoặc HTTPS-proxy.

Các request chỉ là GET IP công khai và HEAD trang chủ, không có tài khoản, token, mua hàng, mã mời hay gọi provider xoay IP. Dữ liệu đo nằm ở `.tmp/check_proxy_matrix.json` và `.tmp/compare_proxy_transports.json`. HTTP 200 trang chủ không chứng minh POST đăng nhập tài khoản thật thành công.

## Những điểm đã sửa

- Trước đây, sau lỗi TLS, fetcher ép HTTP/1.1 rồi giữ chế độ này kể cả sau khi đổi IP; IP mới không có cơ hội thử cấu hình gốc. Nay curl 35 thử kết nối mới bằng profile HTTP/TLS mặc định; chỉ hạ HTTP/1.1 sau lỗi HTTP/2 cụ thể hoặc khi tuyến DNS đã được xác minh bằng cấu hình đó.
- Sau cấp lại proxy, bỏ chế độ HTTP/1.1 và route DNS cũ, thử profile gốc trên IP mới bằng cùng token. Không chèn thêm phép thử DNS ép HTTP/1.1 trước lần thử này. Các kết nối phục hồi giữ hạn kết nối 10 giây; request đầu giữ cấu hình mặc định. Ngân sách hiện tại là tối đa 4 POST khi có phục hồi proxy, như cập nhật bên dưới.
- Log IP đổi thành “Đã lấy IP qua proxy”, nói rõ kiểm tra này chưa xác nhận HTTPS tới Mochi. Nguồn đầu tiên là HTTP `ip-api.com`, nên không thể dùng kết quả đó làm bằng chứng TLS đã hoạt động.
- Sửa lỗi parse độc lập: password có `@` ở dạng `host:port:user:password` trước đây có thể bị hiểu nhầm là URL thiếu port. Giữ định dạng URL chuẩn, protocol và IPv6. Lỗi này không phải nguyên nhân của endpoint không có credentials trong log.

## Chạy lại

Mở `Launch_MochiTool.bat`, chọn **Dùng lô mail đã mua** và file `data/mail_batches/mochi_07b47954_f6d173fb4ac3.json`. Chế độ này dùng 7 mail đã thanh toán và không mua thêm. Không tự chạy job thật trong quá trình kiểm tra.

## Kiểm chứng nguồn

Chạy các bộ logic, TLS, proxy preflight, DNS, đối chiếu bản chính và mua lô: 132 kiểm tra qua. Ba kiểm tra mới dành cho parser credentials; kiểm tra TLS hiện xác nhận cấu hình gốc được khôi phục sau rotation, không giữ HTTP/1.1 hoặc CONNECT_TO của IP trước. Nguồn chỉnh sửa đồng bộ ở gốc và `src/`.

## Cập nhật sau job mochi_a46f0cbb

Log mới có chuỗi curl `35 → 16 → 28`. Giới hạn cũ 3 POST dùng hết vào: request ban đầu, tạo kết nối TLS mới, hạ HTTP/1.1. Vì vậy callback cấp lại proxy chưa được chạy ở bước API, dù log kết thúc nói chung về các lần phục hồi. Đây là lỗi ngân sách thử lại trong nguồn, không phải bằng chứng provider từ chối đổi IP.

Đã sửa:

- Có tối đa 4 POST nếu API dùng proxy và có callback phục hồi; giữ tối đa một lần tạo lại TLS, một lần hạ HTTP/1.1 và một lần cấp lại proxy. Không đặt lại các cờ này khi đổi IP, nên không thể lặp vô hạn. Chuỗi lỗi mới đi qua cấp lại proxy giữa POST 3 và POST 4.
- Dùng cùng token, chỉ đăng nhập Google một lần. Đổi IP xong thử profile gốc. Không mua thêm mail hoặc tự lặp request nhập mã.
- Log mỗi lần gửi API ghi số lượt/giới hạn; kết quả cuối ghi số POST và đã yêu cầu cấp lại proxy hay chưa. Giữ nguyên các ngoại lệ cho chứng chỉ, lỗi nghiệp vụ và hủy tác vụ.

Phép thử API công khai ngày 31/10/2026 khoảng 23:52: gửi JSON rỗng `{}` tới `/api/auth/google`, không có Google token hoặc dữ liệu tài khoản. Trực tiếp nhận HTTP 400 sau khoảng 0,99 giây, xác nhận đường HTTPS/API có phản hồi từ chối hợp lệ. Qua proxy, cả cấu hình mặc định và HTTP/1.1 timeout kết nối sau khoảng 7 giây; HEAD trang chủ qua proxy cũng timeout. Không gửi lại token thật hoặc thực hiện đăng nhập thành công. Script và dữ liệu: `.tmp/probe_mochi_api_transport.py`, `.tmp/probe_mochi_api_transport.json`.

Đã chạy 154 kiểm tra liên quan trong vòng này: 37 logic, 25 TLS/phục hồi, 11 preflight, 16 DNS, 13 đối chiếu bản chính, 33 mua lô và 19 Chrome/Qt. Ba kiểm tra TLS mới tái hiện chính chuỗi `35 → 16 → 28`, cả thành công/thất bại sau đổi IP và trường hợp API đi trực tiếp không xoay proxy. Lô đã thanh toán `mochi_a46f0cbb_121711d17a3d.json` được giữ nguyên để chọn bằng **Dùng lô mail đã mua**.
