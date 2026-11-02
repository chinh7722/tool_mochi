# Proxy.vn: kết nối bị reset khi kiểm tra IP

Ảnh lỗi có ba endpoint `160.250.166.16:10870`, `160.250.166.16:10393`, `160.250.166.16:10802`. Phép kiểm tra chỉ đọc từ môi trường đang sửa tool cho kết quả:

- Qua từng endpoint, cả HTTP ip-api.com, HTTP ipify và HTTPS ipify đều bị ngắt kết nối: **curl 56 — Connection was reset**.
- Phép thử TCP cũng nhận **Windows 10054 — ConnectionResetError**.
- Cùng thời điểm, truy cập trực tiếp HTTP ip-api.com và HTTPS ipify trả **HTTP 200**.

Đây là kết quả tại thời điểm thử, không chứng minh nguyên nhân nằm ở máy chủ proxy hay thiết bị chặn trên đường mạng. Chưa đủ dữ kiện kết luận sai key, hết hạn gói hoặc cần xác thực IP. Cần nhà cung cấp xác nhận endpoint/port hiện tại và nguyên nhân reset. Chưa gọi API xoay IP hoặc thay đổi danh sách proxy đang lưu.

## Thay đổi trong tool

- Kiểm tra IP ghi rõ nguyên nhân như kết nối bị ngắt/reset, HTTPS/TLS, timeout và mã curl. Nếu nhiều nguồn thất bại, giữ kết quả của các nguồn thay vì chỉ hiện tên API cuối cùng ipify64.
- Khi nhận lỗi ngắt kết nối/empty reply/send/receive, thử lại **một lần** bằng session mới tới **cùng proxy**. Tổng phép kiểm tra vẫn giới hạn 30 giây; ngân sách timeout cũ vẫn giữ. Không tự gọi API xoay trong phép kiểm tra.
- `trust_env=False` cho cả hai session để HTTP(S)_PROXY/NO_PROXY của Windows không thay đổi tuyến đã chọn. Có kiểm thử proxy HTTP trên localhost với `NO_PROXY=*` để xác nhận request thực sự đi qua proxy đã nhập.
- Timeout kiểm tra IP không bị nhận nhầm thành cooldown xoay. Cooldown thật từ nhà cung cấp vẫn được dùng theo cơ chế cũ. Các kết nối vẫn lỗi không được báo thành công.
- Thông báo lỗi Telegram hiểu nguyên nhân reset/TLS mới và không chứa URL, key hay credentials thô.

Mở lại `Launch_HoshinoTool.bat`, chọn **Check all** ở bảng Proxy lỗi để thử lại. Proxy kiểm tra thành công sẽ trở về bảng hoạt động. Nếu vẫn hiện reset ở cả ba endpoint, gửi mã lỗi **curl 56 / Windows 10054** cùng endpoint/port cho nhà cung cấp để kiểm tra đường kết nối.

## Kiểm chứng

164 kiểm thử liên quan đã qua: recovery mới 12, original checker 13, logic 37, MuaProxyXoay 25, proxy GUI 12, browser/GUI 24, mua mail/proxy riêng 27, thông báo lỗi 14. Mã nguồn và bản `src` đồng bộ; không mua mail, tạo job thật, gửi Telegram hoặc đổi IP trong quá trình kiểm tra.
