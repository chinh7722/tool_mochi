# Thử mã mời trước và Proxy mua mail riêng

Mở lại tool bằng `Launch_HoshinoTool.bat` để nạp thay đổi.

## Mua mail theo hai đợt

Job mua mail tự động dùng **1 tài khoản để thử mã mời trước**. Chỉ khi tài khoản đó nhập mã thành công mới mua phần còn thiếu.

- Mục tiêu 1.000 bean / 10 lượt: mua **1 mail**, nhập mã thành công rồi mua **9 mail**. Lượt thử được tính luôn vào kết quả, không chạy thành 11 lượt.
- Mục tiêu 1 lượt chỉ mua 1 mail. Job đã có 3/10 lượt thành công thì thử 1 mail mới, thành công mới mua thêm 6.
- Thử bị giới hạn 24 giờ, mã sai, đăng nhập lỗi, tài khoản đã dùng mã hoặc lỗi mạng không phục hồi được: dừng job và không mua phần còn lại.
- Sau lần thử thành công, các tài khoản còn lại chạy theo tổng luồng/proxy đã cấu hình. Chờ mua mail không giữ slot proxy tài khoản.
- Không tự mua bù khi lô còn lại trả thiếu hoặc tài khoản lỗi. Mail shop giao dư được lưu; phần dư từ lần thử được tính vào số cần mua tiếp.
- Dùng lô JSON đã mua giữ luồng dùng mail có sẵn và không mua thêm. Tùy chọn này không áp dụng lần mua thử mới.

Hai đợt mua được lưu trong cùng file tại `data/mail_batches/`, giữ trạng thái lần thử thành công và mail còn lại. Cả hai shop và danh sách ưu tiên xen kẽ vẫn được áp dụng riêng cho từng đợt.

## Cấu hình Proxy mua mail

1. Vào tab **Proxy mua mail** ở thanh bên.
2. Chọn đúng nhà cung cấp **Proxy.vn** hoặc **muaproxyxoay.net** khi nhập raw key.
3. Dán proxy hoặc Nhập TXT, mỗi dòng một proxy có key xoay, ví dụ:

   ```text
   127.0.0.1:31001:user:pass|KEY_XOAY_1
   127.0.0.1:31002:user:pass|KEY_XOAY_2
   ```

   Thay dữ liệu mẫu bằng proxy thật. Nhận cả endpoint không có mật khẩu và link API xoay theo định dạng đã hỗ trợ.
4. Vào **Cài đặt chung → Hạ tầng chung**, bật **Sử dụng proxy** và **Mua mail qua Proxy mua mail riêng**, rồi lưu. Từng lựa chọn Google/đăng nhập Mochi/nhập mã vẫn điều khiển việc dùng proxy tài khoản ở Proxy Pool.

Danh sách, nhà cung cấp và đường dẫn file TXT của Proxy mua mail được lưu riêng với Proxy Pool. Có Nhập TXT, Lưu TXT, Xuất TXT; không dùng proxy thường không có key vì không thể tự đổi IP sau mỗi lần mua. Khi chọn mua mail qua proxy mà danh sách riêng trống, job báo cấu hình thiếu trước khi tạo job/mua mail. Bỏ chọn mua mail qua proxy hoặc tắt Sử dụng proxy thì bước mua dùng mạng trực tiếp.

## Quy tắc xoay IP

- Một proxy chỉ xử lý một giao dịch mua tại một thời điểm, dùng chung trạng thái giữa các job.
- Sau mỗi **POST yêu cầu mua** (gồm cả báo hết hàng hoặc giới hạn tốc độ), proxy được đưa vào vòng xoay IP nền. Nhập TXT/kiểm tra IP không được tính là mua mail.
- Proxy đang xoay/chờ cooldown được bỏ qua khi có proxy khác sẵn sàng. Chỉ khi tất cả đều bận/xoay thì job chờ proxy đầu tiên sẵn sàng; thời gian chờ cấp proxy tối đa 300 giây.
- Mỗi lần mua giữ cùng IP cho đọc số dư trước → POST → đọc số dư sau → lưu biên nhận. Xong giao dịch mới xoay; không làm lệch phép tính chi phí.
- Tôn trọng thời gian chờ nhà cung cấp proxy và shop. Giới hạn tốc độ chỉ thử lại giao dịch bị từ chối rõ ràng, cùng sản phẩm/số lượng, tối đa ba lượt. Mất phản hồi hoặc giao dịch mơ hồ vẫn dừng để đối soát.
- Endpoint mới và trạng thái cần xoay được giữ tại `data/mail_proxy_state/`, không ghi key/password vào cache này. Lần mở lại sẽ hoàn tất việc xoay đang dở trước khi dùng IP đó mua tiếp.
- Nếu trùng gói xoay giữa hai danh sách, khóa bảo vệ ngăn đổi IP khi đang có tác vụ sử dụng. Nên dành các gói riêng cho Proxy mua mail để các bước tài khoản chạy độc lập.

## Chi phí và số liệu

Mỗi lần shop trừ tiền đều ghi vào chủ sở hữu job Local/Telegram, kể cả mail thử không nhập mã được. Đơn khách giữ tiền theo mục tiêu, chốt theo bean thực tế thành công và hoàn phần còn lại như hiện có. Lịch sử CSV và thông báo lỗi vẫn ghi kết quả của toàn bộ job.

## Kiểm chứng

27 kiểm thử mới cho hai giai đoạn mua, giới hạn 24 giờ/invalid/auth/network, mục tiêu 1, tiếp tục job, giao dư/thiếu, giữ đủ 5 slot sau thử, hai shop ưu tiên, chi phí cả hai lần, proxy xoay nền/chuyển proxy/chờ tất cả, chia sẻ giữa job, đồng nhất IP số dư, phục hồi endpoint qua restart, dừng/đóng HTTP và namespace giao diện. Tổng 429 kiểm thử liên quan đều qua; không mua mail, nhập mã hoặc đổi IP thật trong kiểm chứng. Đã xem tab mới ở 1280×900, 32 cặp nguồn gốc/src đồng bộ. Chưa build EXE.
