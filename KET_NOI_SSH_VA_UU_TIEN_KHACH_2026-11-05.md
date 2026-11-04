# Kết nối VPS bằng SSH key và ưu tiên job khách

## API mua mail được phép trùng nhau

API cho **Tạo account có sẵn bean** và API mua mail nhập mã mời có thể có cùng
một giá trị. Nhập cùng key vào hai phần nếu muốn dùng chung tài khoản shop.
Hai phần vẫn áp dụng đúng công đoạn: tạo acc dùng API ở biểu mẫu hoặc
`/acc_mailapi`; nhập mã mời dùng API Cài đặt chung hoặc `/mailapi` của chủ job bot.
Không tự sao chép/ghi đè API của phần còn lại khi lưu một phần.

## File quản lý kết nối có giao diện

Mở **Mo_Quan_Ly_Ket_Noi_VPS.bat** hoặc chạy `main_vps_connection.py`.
Đã chuẩn bị hồ sơ **Mochi VPS sv50**, host `sv50.pcrender.net`, cổng SSH `20052`,
user `root1`, cổng API `8765`, kèm SSH key được lưu cục bộ. Public key đã được
cài vào VPS và đã kiểm tra đăng nhập/tunnel bằng key, không dùng mật khẩu.

1. Chọn hồ sơ **Mochi VPS sv50**.
2. Bấm **Kết nối VPS**, chờ thông báo đã mở tunnel.
3. Bấm **Mở tool quản lý VPS**, hoặc mở web local như trước.
4. Giữ cửa sổ kết nối mở trong khi quản lý. **Ngắt kết nối** hoặc đóng cửa sổ chỉ
   ngắt tunnel của cửa sổ này; các job vẫn chạy trên VPS.

Nếu cổng `8765` đang được tunnel cũ sử dụng, đóng tunnel cũ do bạn mở hoặc chọn
cổng local khác. Giao diện kết nối cập nhật địa chỉ localhost cho tool/web và
giữ nguyên token API đã lưu. Token quản lý vẫn cấu hình trong tool/web như trước.

Các chức năng trong cửa sổ:

- **Lưu kết nối**: lưu tên, host, cổng, user và đường dẫn key; có nhiều hồ sơ.
- **Chọn SSH key**: dùng private key đã có tại đường dẫn đã chọn.
- **Lưu bản sao key**: chép key vào `data/ssh_keys`, bảo vệ quyền đọc cho user hiện tại.
- **Tạo key ed25519**: tạo cặp key cục bộ mới; cần cài public key vào tài khoản VPS
  tương ứng trước khi kết nối.
- **Xem / Sao chép public key**: lấy nội dung public key để cài vào VPS.
- **Kiểm tra / Tin cậy máy chủ**: xem fingerprint; đối chiếu với VPS rồi xác nhận
  để lưu host key. Kết nối luôn kiểm tra host key đã lưu.

Hồ sơ nằm tại `data/vps_connections.json`; key của hồ sơ hiện tại ở
`data/ssh_keys/mochi_sv50_root1_ed25519`. Private key không đưa lên GitHub hoặc
VPS; VPS chỉ nhận public key. Không lưu mật khẩu SSH trong hồ sơ. Nếu chọn key
có passphrase, nạp key vào `ssh-agent` trước khi kết nối.

Máy cần **OpenSSH Client** của Windows. Có thể tiếp tục dùng
`Open_VPS_Tunnel.bat` nếu muốn mở tunnel theo cách cũ; tránh dùng trùng cổng.

## Ưu tiên nạp bean cho khách

Mọi job nhập mã mời cho khách, từ tool, web hoặc bot, được ưu tiên trước job
tích bean của kho. Điều này cũng áp dụng khi job hẹn giờ/combo của khách đến
giờ chạy tiếp. Lịch của khách chưa đến giờ không chặn công việc nền.

Khi có job khách:

1. Job tích bean đang chạy nhận yêu cầu tạm dừng, không bắt đầu lượt mới.
2. Request đang gửi được hoàn tất để xác nhận kết quả và tránh mua/nhập trùng.
   Request mua mail đã gửi cũng được chờ kết quả, không hủy mù giao dịch.
3. Tiến độ, số lỗi và mail đã mua chưa dùng được lưu. Job chuyển sang
   **Tạm dừng ưu tiên khách**, không bị tính thành thất bại.
4. Job khách được chạy trước. Các job nền còn chờ không được khởi chạy.
5. Khi hết job khách đang chạy/chờ/đến hạn, job tích bean tự tiếp tục cùng job ID,
   dùng mail đã mua trước, chỉ mua thêm phần còn thiếu. Không chờ thêm 24 giờ
   chỉ vì tạm dừng ưu tiên; giới hạn quota 24 giờ của mã vẫn áp dụng bình thường.

Tạo/đăng nhập acc nền cũng nhường cho khách. Nếu thao tác đăng nhập bị ngắt để
nhường, mail đã mua được giữ để tạo tiếp sau đó.

**Dừng nhập mã** thủ công vẫn có hiệu lực lâu dài: acc do bạn dừng sẽ không tự
bật lại khi hết job khách. Log và lịch sử Thống kê được giữ. Nếu không ghi được
lô mail/tiến độ khi tạm dừng, job báo lỗi để kiểm tra thay vì tự chạy lại có nguy
cơ dùng trùng mail.
