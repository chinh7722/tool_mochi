# Thêm proxy muaproxyxoay.net

Mở `Launch_HoshinoTool.bat`, vào **Proxy Pool**:

1. Chọn **Nhà cung cấp khi nhập → muaproxyxoay.net**.
2. Bấm **Nhập TXT…** hoặc **Dán proxy**. Hộp dán cũng có lựa chọn nhà cung cấp riêng.
3. Nhập mỗi proxy một dòng:

```text
IP:PORT:USER:PASS|KEY_XOAY
```

Ví dụ minh họa trong [file mẫu](examples/proxy_muaproxyxoay_mau.txt):

```text
192.0.2.10:32001:demo_user:demo_pass|PACKAGE_KEY_1
192.0.2.11:32002:demo_user:demo_pass|PACKAGE_KEY_2
```

Thay địa chỉ, tài khoản và key bằng thông tin proxy của bạn. Các địa chỉ trong file mẫu không phải proxy thật. Với job, vào **Cài đặt chung → Hạ tầng chung**, bật Sử dụng proxy, chọn Dùng Proxy Pool và các bước cần đi qua proxy.

Mỗi dòng nhớ nhà cung cấp trong cấu hình tool. Đổi lựa chọn nhập sau đó không đổi nhà cung cấp của các dòng đã có. Dữ liệu Proxy.vn cũ tiếp tục được đọc là Proxy.vn. Nếu trước bản cập nhật đã nhập nhầm key của dịch vụ mới thành Proxy.vn, xóa dòng nhập nhầm rồi nhập lại với lựa chọn muaproxyxoay.net.

## API đổi IP

Cài đặt theo tài liệu trong ảnh đã cung cấp:

```text
GET https://api.muaproxyxoay.net/user/package/changeip?package_api_key=PACKAGE_API_KEY
```

`app_id` không bắt buộc; không mặc định gán `1000`. Nếu có app ID do nhà cung cấp cấp, có thể đặt link API đầy đủ ở phía sau dấu `|`:

```text
192.0.2.10:32001:demo_user:demo_pass|https://api.muaproxyxoay.net/user/package/changeip?package_api_key=PACKAGE_KEY_1&app_id=1000
```

Đường gọi API điều khiển dùng mạng trực tiếp. Kết nối tới app/shop qua proxy vẫn sử dụng địa chỉ và user/password của proxy đã chọn.

## Cách hoạt động

- Khi đã có endpoint, cấp địa chỉ hiện tại cho job; không gọi API đổi IP chỉ vì nhập/cấp proxy.
- Nút xoay IP và cơ chế phục hồi sau lỗi request dùng provider muaproxyxoay.net. Cơ chế phục hồi khi shop giới hạn tốc độ mua cũng dùng đúng provider của proxy.
- Nhận `Status: Success`: đọc `Data`, chọn `public_ip` (hoặc `public_origin_ip` nếu thiếu public_ip) và cổng tương ứng `http_port` / `https_port` / `socks_port`. Giữ nguyên giao thức, username và password từ dòng đã nhập. Nếu response có nhiều dòng, tìm dòng khớp cổng/địa chỉ hiện tại; không tự chọn ngẫu nhiên một endpoint khác.
- Nhận `Status: Error`, `Message: Vui lòng chờ sau 180 giây`: coi là cooldown, chờ theo thời gian trả về và cập nhật tiến độ. Không coi chờ xoay là key hỏng. Proxy hiện tại vẫn có thể được cấp khi không yêu cầu đổi IP bắt buộc.
- Sau lỗi mạng cần đổi IP bắt buộc, không cấp lại IP đang lỗi khi chưa đổi thành công. Hết thời gian chờ thì trả lỗi; không tiếp tục job bằng kết quả cooldown như thể đã đổi IP.
- Lỗi key/hết hạn hoặc HTTP 401/403 được trả về rõ ràng. HTTP 429 có Retry-After dạng số thì chờ theo giá trị đó. Có giới hạn thời gian và hỗ trợ hủy.
- Nút xoay trong GUI chờ tối đa 300 giây với muaproxyxoay.net, đủ cho phản hồi yêu cầu chờ 180 giây. Job dùng thời hạn cấp proxy hiện tại của Broker.
- Khóa dùng chung có tên nhà cung cấp và key gói: key giống chuỗi giữa hai dịch vụ không lẫn trạng thái. Không xoay cùng gói khi job khác đang giữ lease của gói đó. Một key gói trùng chỉ được tính một nhóm slot, kể cả nhập lặp nhiều dòng.

Proxy có user/password nên nhập qua Proxy Pool để tool có đủ thông tin đăng nhập. Nhập chỉ key/link ở chế độ nhanh không cung cấp được mật khẩu mà response API không trả về.

## Lưu và xuất TXT

Mỗi proxy vẫn chiếm một dòng. Khi xuất muaproxyxoay.net, phần sau dấu `|` là link API có `package_api_key`, để lần nhập sau nhận ra nhà cung cấp mà không cần đoán từ chuỗi key. File có lẫn hai nhà cung cấp sẽ dùng link API cho cả hai loại. Link có app_id được giữ lại; file chỉ có Proxy.vn giữ định dạng key cũ.

## Kiểm chứng

25 kiểm thử mới kiểm tra API, cooldown 180 giây bằng đồng hồ giả lập, credentials, cổng HTTP/SOCKS, lỗi/hủy, nhập/dán/lưu TXT, dữ liệu cũ, slot đồng thời và tích hợp Broker/GUI. Tổng 208 kiểm thử liên quan đã qua. Không sử dụng key thật, gọi API đổi IP thật, mua mail hoặc chạy job thật trong vòng kiểm chứng này.

```powershell
py -3.14 -I tests/test_muaproxyxoay.py
```

22 cặp mã nguồn gốc/`src` đã đồng bộ và biên dịch hợp lệ. Chưa đóng gói lại EXE.
