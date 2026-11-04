# Bảng giá, khách hàng theo mã mời và lịch sử tạm

## Bảng giá trên tool và web local

1. Mở **Bảng giá** trên thanh bên.
2. Nhập mốc bean và giá VND, ví dụ **2.000 bean / 15.000 VND**.
3. Bấm **Thêm / Cập nhật**, sau đó **Lưu bảng giá** để lưu trên VPS.
4. Có thể sửa/xóa từng mốc; thay đổi chỉ áp dụng sau khi bấm Lưu.

Giá được **tính theo tỷ lệ mốc bean**, theo lựa chọn của người quản lý:

| Mốc đã khai báo | Bean của job | Giá tính được |
| --- | --- | --- |
| 2.000 bean = 15.000 VND | 1.000 bean | 7.500 VND |
| 2.000 bean = 15.000 VND | 2.000 bean | 15.000 VND |
| 2.000 bean = 15.000 VND | 3.000 bean | 22.500 VND |

Nếu có nhiều mốc, dùng mốc cao nhất không vượt số bean tính tiền; nếu nhỏ hơn
mọi mốc thì dùng mốc nhỏ nhất. Bean tính tiền vẫn làm tròn lên từng lượt
100 bean như trước. Tổng giá làm tròn một lần đến 0,01 VND, không làm tròn
đơn giá từng tài khoản rồi nhân ngược lại.

Bảng rỗng dùng lại đơn giá **VND / 100 bean** ở Cài đặt chung. Mốc bean phải
là bội số 100; giá phải lớn hơn 0. Bảng giá dùng chung cho Local, admin,
partner và đơn khách qua bot. Ô dự tính tiền trên tool/web dùng cùng quy tắc.

**Giá được chốt khi tạo job**, kể cả job hẹn giờ/combo; sửa bảng không đổi giá
của job đã tạo. Khách giữ tiền theo tổng giá đã chốt; khi chốt đơn, thu phần
tương ứng bean đã thành công và hoàn phần còn lại. Tiếp tục job giữ phần giá
còn thiếu từ job nguồn. CSV có cột **Giá chốt theo bảng giá (VND)** để đối chiếu.

## Hai cách đếm khách trong Thống kê

- **Lượt phục vụ**: mỗi lần kết thúc job/đợt combo được tính một lượt, như trước.
- **Khách hàng theo ngày**: mỗi **mã mời duy nhất trong ngày** là một khách.
  Cùng mã chạy nhiều job, nhiều đợt hoặc do nhiều người nhập trong cùng ngày
  chỉ tính một khách. Mã đó xuất hiện vào ngày khác sẽ tính một khách của ngày đó.

Biểu đồ mới dựa trên các job/đợt đã kết thúc trong từng ngày, theo UTC+7.
Chọn tháng và bấm cột để xem các mã mời, số lượt phục vụ và người sở hữu trong
ngày. Biểu đồ lượt phục vụ được giữ riêng để không trộn hai cách đếm.

Trong **Thống kê → Lịch sử job / Chi tiết**, ô **Ngày xem** cho phép chọn
**Tất cả các ngày trong tháng** hoặc một ngày cụ thể. Trên web ô này nằm ngay
trên bảng lịch sử. Bộ lọc người dùng vẫn áp dụng; có thể dùng nút **Cả tháng**
ở phía trên để bỏ ngày vừa chọn từ biểu đồ.

## Lịch sử job tạm ở Mochi Chat

Mở **Mochi Chat → Nhiệm vụ → Lịch sử tạm**. Danh sách dùng chung trên VPS,
có phân trang và tự cập nhật, bao gồm job đã kết thúc trước và sau khi mở lại tool.

- Bấm **Log** để xem log của job cũ.
- Job thất bại còn thiếu bean, chưa có lần tiếp tục, có nút **Tiếp tục**.
- Bấm **Xóa lịch sử tạm** sẽ hỏi xác nhận. Chọn Không/Hủy không thay đổi gì.
- Xác nhận chỉ xóa danh sách hiển thị tạm của các job đã kết thúc lúc đó.
  Những job đang chạy không bị dừng; khi chúng kết thúc hoặc có job mới kết
  thúc sau đó, lịch sử tạm tiếp tục hiển thị các job ấy.
- Xóa tạm **không xóa job trong sổ, lịch sử Thống kê, tiền, đợt combo hoặc log**.
  Lịch sử Thống kê luôn được giữ; không có nút xóa thống kê.

## Giữ log combo qua các ngày và restart

Log mới được lưu từng dòng trên VPS vào kho nhật ký riêng. Mọi đợt của cùng
job combo nối tiếp vào cùng log, giữ thứ tự khi cập nhật dịch vụ hoặc VPS khởi
động lại. Khi mở Log, client tải log đã lưu theo từng trang rồi tiếp tục nối
dòng mới, không chỉ lấy phần cuối gần nhất.

Khi triển khai, giữ lại log mà tiến trình cũ vẫn đang có và có thể phục hồi
thêm warning/error từ nhật ký dịch vụ cũ nếu còn. Dòng phục hồi có nhãn và
thời gian riêng. Log đã mất khỏi bộ nhớ mà không từng được ghi ra đĩa/nhật ký
không thể khôi phục đầy đủ. Từ phiên bản này log không bị mất chỉ vì sang đợt
combo mới hoặc khởi động lại dịch vụ.

Sau cập nhật, đóng và mở lại tool; đóng/mở lại launcher web local rồi tải lại
trang. SSH tunnel vẫn dùng như trước; web tiếp tục chỉ chạy trên máy quản lý.
