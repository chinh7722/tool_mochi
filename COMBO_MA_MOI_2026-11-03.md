# Nhập mã mời combo

Tại **Mochi Chat → Nhiệm vụ → Thiết lập job**, nhập mã mời và tổng bean cần,
tích **Nhập mã mời combo**, rồi bấm **Bắt đầu combo**. Có thể kết hợp hẹn giờ
để chọn thời điểm bắt đầu đợt đầu tiên.

Qua Telegram, admin/partner và khách hàng dùng:

```text
/combo VBRN5H 5500 Khách A
```

Mỗi lượt thành công tương ứng 100 bean. Mục tiêu 5.500 bean cần 55 lượt,
thường chia thành 10 + 10 + 10 + 10 + 10 + 5 khi mã chưa có lượt trong 24 giờ
gần nhất. Hạn mức 10 lượt được tính chung theo mã, kể cả lượt từ job khác.

Khi gặp giới hạn 24 giờ, job giữ cùng mã job, cộng dồn bean đã đạt và chuyển
sang **Chờ lượt combo**. VPS lưu lịch đợt tiếp theo sau **24 giờ 1 phút** tính
từ lúc đợt hiện tại báo giới hạn. Nếu bộ đếm cho biết hạn mức còn bị chặn lâu
hơn, lịch sẽ chờ thêm đến sau mốc khả dụng một phút. Đến giờ, job kiểm tra
lại hạn mức rồi vào hàng đợi chung. Đợt cuối chỉ chạy phần còn thiếu.

Tab **Job đang chạy** hiển thị tổng tiến độ và giờ tiếp tục theo giờ Việt Nam.
Bot gửi tiến độ, bean còn thiếu và lịch chạy tiếp về người tạo; `/lich` xem
lịch đang lưu. Tắt ứng dụng Windows không ảnh hưởng đến combo. Khi dịch vụ
VPS khởi động lại, lịch chờ được khôi phục; đợt bị ngắt kiểm tra lại tiến độ
đã xác nhận trước khi chạy tiếp.

Có thể bấm **Dừng** trên giao diện hoặc dùng `/huy MÃ_JOB` khi combo đang chờ.
Đối với đơn của khách hàng, tiền được tạm giữ một lần theo toàn bộ mục tiêu;
thời gian chờ giữa các đợt không quyết toán hoặc tạo đơn mới. Khi hoàn thành
hoặc hủy, hệ thống thu theo số bean đã thành công và hoàn phần chưa dùng.

Combo chỉ tự hẹn lại khi đạt giới hạn 24 giờ hoặc khi dịch vụ đang đóng có
đợt chạy bị ngắt. Lỗi mã không hợp lệ, mua mail, API, proxy hoặc đăng nhập
vẫn dừng để kiểm tra. Mail và cấu hình nguồn được lấy trên VPS; admin/partner
tiếp tục dùng hai API mua mail riêng của người tạo.

Lịch, tổng tiến độ và chủ sở hữu lưu trong `telegram_users.sqlite3`; các lượt
redeem đã xác nhận lưu trong `invite_quota.sqlite3`. Hai tệp đều thuộc thư mục
dữ liệu bền vững của VPS. Một lỗi tiến độ Qt lúc tiến trình bị ngắt được đối
chiếu lại với các lượt đã ghi trước khi cho phép tiếp tục hoặc hủy.
