# Đếm 10 lượt nhập mã thành công trong 24 giờ

Mỗi mã mời có bộ đếm dùng chung cho mọi job Local, admin/partner và khách hàng.
Tool đếm **lượt nhập mã được Mochi xác nhận thành công**, kể cả khi job cuối cùng
thất bại hoặc bị hủy. Đăng nhập thành công nhưng chưa nhập mã, lỗi nhập mã và
`already_redeemed` không tăng bộ đếm.

## Cách hoạt động

- Đã có **10 lượt trong 24 giờ gần nhất**: trả ngay **Đạt giới hạn 24h**,
  không mua 1 mail thử, không đăng nhập Google hoặc gửi thêm yêu cầu nhập mã.
- Đã có **9 lượt**, đặt job 10 lượt: chỉ mua 1 mail; nếu nhập thành công thì
  job ghi nhận 1 lượt mới rồi kết thúc vì giới hạn. Không mua thêm 9 mail.
- Đã có **8 lượt**, đặt job 10 lượt: mua 1 mail thử, thành công mới mua thêm
  tối đa 1 mail. Tổng số mail vẫn theo phần quota còn lại.
- Mã mới chưa có lượt nào: giữ luồng mua 1 mail thử rồi mua phần còn thiếu,
  tối đa phần còn lại trong giới hạn 10 lượt.

Đây là **24 giờ trượt**, không reset lúc 0 giờ. Mỗi lượt hết hạn sau đúng24h
kể từ thời điểm thành công. Job bị chặn có log số lượt đã dùng và thời điểm
sớm nhất có lượt khả dụng trở lại, hiển thị theo giờ Việt Nam.

Số thành công/bean của job mới chỉ tính lượt mới, không cộng lượt lịch sử.
Ví dụ mã đã có9 lượt, job mới nhập thêm1 thì job mới ghi100 bean. Đơn khách
hàng chốt/hoàn tiền theo số thành công thực tế như trước; job bị chặn trước
khi chạy được hoàn toàn bộ tiền giữ.

## Nhiều job, hàng đợi và lịch hẹn

Các job cùng mã được đối chiếu tuần tự để không cùng mua mail khi chỉ còn
một lượt. Job khác mã vẫn có thể chạy đồng thời. Kiểm tra được lặp lại lúc
job thực sự bắt đầu, trước từng đợt mua và trước khi cấp thêm tác vụ.

Lịch tương lai vẫn được lưu dù hiện tại mã đã đủ lượt, vì quota có thể hết
hạn trước giờ hẹn. Đến giờ chạy mới kiểm tra lại. Job đang chờ cũng kiểm tra
lại khi được cấp lượt chạy, không giữ kết quả quota cũ lúc tạo.

## Lưu dữ liệu và lịch sử cũ

- VPS: `/var/lib/hoshino/invite_quota.sqlite3`.
- Bản local: `data/invite_quota.sqlite3`, hoặc cạnh file INI khi dùng cấu hình INI.
- Xác nhận thành công được ghi ngay trước khi đóng phiên tài khoản; lặp tín
  hiệu không làm tăng số lượt. Dữ liệu lưu mã/job, hash tài khoản và thời gian;
  không lưu mật khẩu/token hoặc email dạng rõ.
- Khởi động lại giữ bộ đếm. Không xóa bộ đếm khi job lỗi/hủy, xóa dòng job khỏi
  phiên hoặc thu hồi quyền người tạo.
- Khi cập nhật lần đầu, nhập tổng thành công từ mọi job đã lưu trong SQLite,
  bất kể trạng thái cuối. Dữ liệu cũ không có thời gian từng lượt, nên dùng
  thời điểm kết thúc/cập nhật cuối của job để tránh cho hết hạn quá sớm. Đây
  là mốc bảo thủ; các lượt mới dùng thời gian xác nhận riêng chính xác.
- Dữ liệu cũ chỉ nhập một lần; mở lại tool không cộng trùng. Phần thành công
  mới của job cũ tiếp tục cộng vào phần đã nhập.

Bộ đếm phản ánh những lượt tool đã ghi nhận. Những lượt nhập thực hiện ở nơi
khác mà tool không có dữ liệu vẫn có thể khiến Mochi báo giới hạn sớm hơn;
khi đó job dừng theo phản hồi dịch vụ như trước.

Nếu không đọc/ghi được bộ đếm, tool dừng mua/chạy tiếp để kiểm tra dữ liệu.
Lượt vừa được xác nhận thành công vẫn được giữ trong kết quả job để chốt tiền.

## Kiểm chứng

Kiểm thử offline bao phủ cửa sổ24h đúng biên, restart/idempotency/migration,
job lỗi/hủy, chặn mua probe khi đủ10, mua đúng phần còn lại, cùng mã ở nhiều
thread/event loop, hủy trong lúc chờ và hoàn giữ tiền. Không mua mail hoặc nhập
mã thật trong các kiểm thử.
