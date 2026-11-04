# Thống kê, tiếp tục job và mua mail bù

## Quản lý job trên tool và web local

- **Tiếp tục** nằm cạnh **Log** và **Dừng** trong bảng Job đang chạy. Chỉ bật
  khi job đã kết thúc, còn thiếu bean và worker cũ đã dừng hẳn.
- Tiếp tục tạo một job mới cho đúng số bean còn thiếu, giữ người sở hữu và
  đơn giá cũ. Job trước giữ nguyên kết quả, chi phí và lịch sử.
- Mỗi job nguồn chỉ tạo một job tiếp tục. Nếu job tiếp tục vẫn lỗi, chọn chính
  job mới đó để tiếp tục phần còn thiếu. Cách này tránh chạy trùng khi bấm lại.
- Với đơn khách hàng, phần tiền cũ đã được chốt/hoàn; lần tiếp tục giữ tiền ví
  cho phần bean còn thiếu. Cần đủ số dư và khách chưa bị xóa. Admin/partner
  phải còn quyền và đủ API mua mail riêng.
- **Dừng / Hủy lịch** trên tool/web luôn hỏi xác nhận. Chọn Không/Hủy sẽ giữ
  job. Các thao tác dừng qua bot Telegram và dừng dịch vụ không hiện hộp thoại.
- **Log** tự lấy dòng mới từ VPS mỗi giây khi tab log đang mở, không cần bấm
  lại. Khi kết nối lại/khởi động lại VPS, màn hình lấy lại mốc log mới. Đang
  cuộn xem dòng cũ thì không ép cuộn xuống cuối.

## Mua mail bù sau lượt xử lý lỗi

- Sau khi dùng lô đã mua, nếu có lượt tài khoản xử lý lỗi, hệ thống mua mail
  mới cho số lượt lỗi còn thiếu, tiếp tục đến đủ bean.
- Nếu mail thử mã đầu tiên lỗi đăng nhập/kết nối, chỉ mua lại một mail thử.
  Sau khi thử mã thành công mới mua phần mục tiêu còn lại.
- Dừng và đưa job vào thất bại khi có **3 lượt lỗi liên tiếp** hoặc **5 lượt
  lỗi tổng cộng trong toàn bộ job**. Một lượt thành công xóa bộ đếm lỗi liên
  tiếp; tổng lỗi không bị xóa. Combo giữ tổng lỗi qua các ngày và khi VPS khởi động lại.
- Tài khoản đã nhập mã trước đó cũng là lượt xử lý không đạt, được tính vào
  ngưỡng trên. Khi đạt ngưỡng, không cấp thêm tác vụ và hủy các tác vụ còn chờ;
  các lượt đã hoàn tất giữ tiến độ và chi phí thực tế.
- Mã mời không hợp lệ, giới hạn 24 giờ, lỗi ghi bộ đếm và lỗi giao dịch shop
  cần đối soát được xử lý ngay. Combo đạt quota vẫn lưu lịch chạy tiếp.
- Tất cả mail mua bù đều được lưu cùng lô và ghi chi phí vào đúng người/job.
  Shop trả thiếu mail được báo rõ; không mua thêm để bù phần shop chưa giao.
- Chọn **Dùng lô mail đã mua** là chạy lô có sẵn; không tự mua mail mới trong
  chế độ đó. Việc thử lại request mua bị shop từ chối vẫn chờ ít nhất 10 giây
  theo chính sách đã có. Mất phản hồi giao dịch dừng để đối soát.

## Tab Thống kê

Mở **Thống kê** trên thanh bên của tool hoặc web local. Chọn tháng; dữ liệu
và các mốc ngày dùng **giờ Việt Nam (UTC+7)**. Biểu đồ toàn hệ thống gồm:

| Biểu đồ | Cách tính | Khi bấm cột |
| --- | --- | --- |
| Lợi nhuận theo ngày | Tài chính của job kết thúc trong ngày | Job theo người, bên dưới mỗi người có tổng doanh thu/chi phí/lợi nhuận |
| Số job theo ngày | Số job đã kết thúc | Tổng số job của từng người và danh sách job |
| Lượt khách hàng theo ngày | Mỗi lần kết thúc job/đợt combo = một lượt khách | Danh sách mã mời, người sở hữu, job, kết quả và giờ kết thúc |
| Tỷ lệ lý do thất bại | Tỷ lệ số đợt chạy lỗi theo nhóm lý do | Xem chú giải với số lượt và phần trăm |

Một mã mời xuất hiện ở nhiều lần kết thúc được tính nhiều lượt khách, theo
từng lần phục vụ. Đây là **lượt khách**, không phải số mã mời duy nhất hay số
tài khoản Telegram mới. Combo nhiều ngày tính mỗi đợt kết thúc riêng; biểu đồ
số job chỉ tính cả job khi kết thúc cuối cùng. Có số lượt khách hôm nay, tuần
hiện tại (thứ Hai đến hôm nay), và tháng đang chọn.

**Lịch sử job** được chuyển từ tab Mochi sang Thống kê. Trên tool mở tab con
**Lịch sử job / Chi tiết**; trên web xem bảng phía dưới các biểu đồ. Chọn một
người hoặc **Tất cả mọi người**, chọn **Cả tháng** để bỏ ngày đã chọn. Bảng có
phân trang 100 dòng; tổng theo người tính toàn bộ ngày/tháng, không chỉ trang
đang xem. Có thể xuất CSV lịch sử đầy đủ bằng nút xuất hiện có.

Tài chính dùng sổ giao dịch hiện có: doanh thu job thông thường chỉ ghi khi
hoàn thành; đơn khách dùng tiền thực tế đã chốt. Chi phí gồm toàn bộ mail của
job, kể cả mail bù/không dùng. Job chưa đủ chi phí/đơn giá để đối soát có lợi
nhuận để trống; biểu đồ và tổng lợi nhuận chỉ cộng phần đã xác định, kèm số job
còn chờ đối soát. Toàn bộ tài chính một job combo được tính vào ngày kết thúc
cuối cùng, không cộng lại ở từng đợt.

Dữ liệu job cũ đã kết thúc được dùng để tạo thống kê. Những đợt combo trung
gian hoặc lý do lỗi chưa từng được lưu trước bản cập nhật không thể khôi phục
chính xác; hệ thống không tự suy đoán. Các đợt mới được lưu bền vững và chống
đếm trùng khi nhận lại callback hoặc khởi động lại.
