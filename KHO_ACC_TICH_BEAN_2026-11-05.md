# Tạo acc tích bean và quản lý kho tài khoản

## Mở giao diện quản lý VPS

Nhấp **Mo_Giao_Dien_Quan_Ly_VPS.bat** để mở giao diện Windows. File
`main_vps_manager.py` là điểm khởi chạy riêng, dùng chung cấu hình với launcher cũ.
Mở SSH tunnel như trước rồi kết nối tại **Cài đặt chung**.

Web vẫn chạy trên máy quản lý: mở **Launch_Web_VPS_Manager.bat**. Đóng tool/web
không dừng tiến độ hoặc lịch chạy trên VPS.

## Tạo account có sẵn bean

Tại **Mochi Chat → Nhiệm vụ → Thiết lập job**, chọn **Tạo account có sẵn bean**:

1. Nhập số acc cần tạo, từ 1 đến 500.
2. Nhập tổng bean mục tiêu trong mỗi acc, bội số 100, tối đa 1.000.000.
3. Nhập product ID mua mail; để trống trên giao diện để dùng product trong Cài đặt.
   Khi chọn cả hai shop, dùng ví dụ `gmailvip:3445,1trieumail:3423`.
4. Hoặc bật **Tích bean vô hạn — 10 lượt / 24 giờ (1.000 bean)**. Khi bật, số bean
   mục tiêu không còn là điều kiện kết thúc; acc tích đến khi được dừng/xuất/xóa.
5. Bấm **Bắt đầu tạo acc tích bean**.

VPS mua một lô mail bằng số acc đã yêu cầu, lưu kết quả giao dịch, đăng nhập từng
email để tạo/đọc tài khoản Mochi, lấy mã mời và số bean ban đầu. Chỉ tài khoản đã
lấy được mã mời mới xuất hiện trong kho. Mỗi acc có job tích bean riêng, nạp phần
bean còn thiếu tính từ số dư ban đầu.

Job tích bean dùng combo và giới hạn chung 10 lượt/mã trong 24 giờ. Đạt giới hạn
thì lưu tiến độ và hẹn sau 24 giờ 1 phút. Chế độ vô hạn chạy các chu kỳ 1.000 bean,
sau mỗi chu kỳ chờ 24 giờ 1 phút trước chu kỳ mới. Các job dùng hàng đợi chung.

Product của lô được giữ trong lịch combo; API key không nằm trong lịch. Tool/web
dùng API chung; admin bot dùng API riêng của admin đó. Shop và đường proxy dùng
cấu hình hiện tại. Không tính doanh thu bán bean cho job nội bộ kho acc; chi phí
mail vẫn ghi đúng người tạo và giữ trong báo cáo giao dịch. Lô tạo acc có mã
`stock_…`; job tích bean có mã `mochi_…` như các job khác.

Mua mail bị mất phản hồi: báo **Cần đối soát**, không tự mua lại. Nếu đã nhận mail
nhưng đăng nhập lỗi, bấm **Thử tạo lại từ mail đã mua** ở **Tiến độ tạo acc**;
thao tác này không mua thêm. Shop giao thiếu không được mua bù âm thầm. Các biên
nhận/lô mail cũ vẫn giữ nội bộ để đối soát, tách khỏi kho acc đã tạo.

## Kho tài khoản

**Mochi Chat → Kho tài khoản** hiển thị tổng số acc, tổng bean, và bảng từng acc:
ô tích chọn, STT ổn định, email, mật khẩu, bean, mục tiêu, mã mời, trạng thái,
nút dừng nhập mã. Có phân trang, chọn/bỏ chọn trang, dừng/tiếp tục acc đã chọn,
bảng tiến độ tạo acc và lịch sử xuất riêng.

Bean hiển thị bằng số dư đọc khi tạo acc cộng các lượt mời đã xác nhận thành
công. Tiêu bean hoặc thay đổi số dư bên ngoài tool chưa tự đồng bộ vào số này.

**Dừng nhập mã** gửi yêu cầu dừng job và bỏ lịch chờ. Chờ trạng thái **Đã dừng**,
**Đủ bean** hoặc **Lỗi tích bean** trước khi xuất/xóa. Acc còn job bị chặn xuất/xóa.
Tool/web hỏi xác nhận trước khi dừng; Telegram thực hiện lệnh dừng trực tiếp.

**Xóa acc đã chọn** có popup xác nhận trên tool/web. Chọn Không giữ nguyên dữ
liệu. Xóa khỏi kho không xóa lịch sử job, tài chính, log, phiếu xuất cũ hoặc tài
khoản trên dịch vụ Mochi/Google.

## Xuất acc

1. Dừng acc cần giao, chờ dừng xong, rồi tích chọn.
2. Bấm **Xuất acc đã chọn**. Popup hiện mỗi acc một dòng `mail|mật khẩu`.
3. Sao chép nội dung. Đóng popup hoàn tất xuất và đưa các acc đó ra khỏi kho.

Phiếu xuất lưu trước khi mở popup; các acc đó bị khóa khỏi những lần xuất khác.
Nếu mất kết nối/đóng ứng dụng trước khi hoàn tất, mở **Lịch sử xuất acc → Xem /
Sao chép** để lấy lại và đóng phiếu. Phiếu hoàn tất vẫn mở lại được; không xuất
trùng hoặc xóa acc mới khỏi kho. Lịch sử Thống kê luôn giữ nguyên.

## Bot Telegram dành cho admin

Chỉ chat riêng của admin đang được cấp quyền mới dùng được lệnh kho. Partner
và khách không được tạo/xem/xóa/xuất kho acc. Dùng `/help` để xem lệnh.

| Lệnh | Chức năng |
|---|---|
| `/acc_create 5 5500 3445` | Tạo 5 acc, mục tiêu 5.500 bean mỗi acc |
| `/acc_create 5 infinite 3445` | Tạo 5 acc tích vô hạn, 10 lượt/ngày |
| `/accs 1` | Tổng acc/bean và danh sách trang 1; 10 acc/trang |
| `/acc_info 1` | Email, mật khẩu, bean, mã mời và trạng thái của acc |
| `/acc_batches 1` | Tiến độ các lô tạo acc |
| `/acc_stop 1,2` | Dừng nhập mã cho các STT 1 và 2 |
| `/acc_resume 1,2` | Tiếp tục tích bean |
| `/acc_delete 1,2` | Xóa acc đã dừng khỏi kho |
| `/acc_export 1,2` | Tạo phiếu xuất, gửi file TXT `mail\|mật khẩu` |
| `/acc_exports 1` | Lịch sử xuất trang 1 |
| `/acc_show MÃ_PHIẾU` | Tải lại file của phiếu đã lưu |
| `/acc_done MÃ_PHIẾU` | Xác nhận đã nhận file, đưa acc khỏi kho |
| `/acc_retry MÃ_LÔ` | Thử tạo lại từ mail đã mua nhưng đăng nhập lỗi |

Trên Telegram, `/acc_done` thay thao tác đóng popup: chạy sau khi đã nhận file.
Nếu chưa gửi được file, phiếu vẫn giữ để dùng `/acc_show`. Bot kiểm tra lại quyền
admin trước khi gửi file. STT là ID kho, không đổi theo thứ tự hiển thị. Các admin
quản lý chung kho; chi phí vẫn thuộc người tạo.
