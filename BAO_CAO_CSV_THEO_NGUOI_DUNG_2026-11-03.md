# Báo cáo CSV theo người dùng

Mỗi báo cáo gồm hai file độc lập:

| File | Nội dung |
| --- | --- |
| `hoshino_CHATID_jobs_THOIGIAN.csv` | Lịch sử job của người đó |
| `hoshino_CHATID_purchases_THOIGIAN.csv` | Lịch sử giao dịch mua mail gắn với các job của người đó |

Tên file của tài khoản Local dùng `local` thay cho Chat ID. Thời gian trong tên
file và các cột thời gian dùng giờ Việt Nam (UTC+7).

## Xuất trên tool quản lý VPS

1. Vào **Telegram → Người dùng và bot → Quản lý người dùng**.
2. Chọn đúng dòng người cần xuất trong bảng.
3. Bấm **Xuất báo cáo CSV**.
4. Chọn thư mục lưu một lần; tool ghi cả hai CSV vào thư mục đó.

Nút xuất lấy người đang được chọn trong bảng. Việc sửa Chat ID trong ô nhập
không đổi đối tượng của báo cáo. Có thể xuất người đã thu hồi quyền và Local.
Tên file đã tồn tại được thêm hậu tố để giữ file cũ. Hủy hộp chọn thư mục sẽ
không ghi file; nếu lưu chưa đủ, trạng thái cho biết những file đã được ghi.

## Xuất trên web local

1. Mở web local bằng `Launch_Web_VPS_Manager.bat` sau khi mở SSH tunnel.
2. Vào **Telegram → Người dùng và bot → Quản lý người dùng**.
3. Chọn một dòng rồi bấm **Xuất báo cáo CSV**.
4. Kiểm tra tên/Chat ID trong cửa sổ báo cáo.
5. Bấm **Tải CSV** ở từng mục để tải lịch sử job và lịch sử mua mail.

Tệp được lưu theo thiết lập tải xuống của trình duyệt. Mỗi file có số dòng
riêng. Hai nút tải dùng cùng một bản chụp dữ liệu khi báo cáo được tạo.

## Xuất trên bot Telegram

Nhắn riêng cho bot:

```text
/baocao
```

Bot gửi hai file CSV của chính người gửi. Có thể dùng `/report` thay `/baocao`.

Admin muốn xuất cho một người khác dùng:

```text
/baocao 123456789
```

Thay `123456789` bằng Chat ID cần xuất.

| Người yêu cầu trên bot | Quyền xuất |
| --- | --- |
| Admin đang được cấp quyền | Của mình, partner và admin khác; có thể lấy lịch sử của người đã thu hồi quyền |
| Partner đang được cấp quyền | Chỉ của chính mình |
| Người chưa được cấp hoặc đã bị thu hồi quyền | Không có quyền xuất báo cáo người dùng qua bot |

Báo cáo Local xuất từ tool hoặc web quản lý. Các bản ghi tạm phục vụ đơn khách
hàng không được coi là partner để xuất qua lệnh quản lý trên bot.

Quyền được kiểm tra từ SQLite lúc tạo báo cáo và trước từng file được gửi,
kể cả khi thử lại sau giới hạn tốc độ Telegram. Nếu admin bị hạ quyền hoặc
thu hồi quyền trong lúc chờ, việc gửi báo cáo không còn được phép sẽ dừng.

Khi lỗi mạng, có thể đã nhận được một file. Kiểm tra những file đã nhận trước
khi yêu cầu lại; bot không tự gửi lại yêu cầu chưa xác nhận kết quả.

## Các cột chính

### Lịch sử job

- Chat ID, tên, vai trò và quyền hiện tại của người dùng.
- Mã job, mã đơn khách, mã mời, ghi chú khách nếu đã được lưu và cờ combo.
- Bean yêu cầu, mục tiêu, lượt đã thử/thành công/lỗi và bean thực tế.
- Trạng thái job, trạng thái hàng đợi và lịch chạy tiếp.
- Đơn giá, doanh thu đã ghi nhận, chi phí mail đã biết, tổng chi phí khi đủ dữ
  liệu, số giao dịch chờ đối soát và lợi nhuận đã xác định.
- Với đơn khách: tiền tạm giữ, đã thu, đã hoàn và thời điểm chốt.
- Các mốc tạo, cập nhật, kết thúc và hẹn giờ.

### Lịch sử mua mail

- Người sở hữu, mã giao dịch mua mail, mã job và mã mời liên quan.
- Nhà cung cấp, Product ID, số mail và mã đơn shop.
- Chi phí, số dư shop trước/sau mua nếu có dữ liệu.
- Nguồn xác định chi phí, trạng thái giao dịch và tình trạng đối soát.
- Thời điểm tạo và cập nhật.

## Cách đọc số liệu

- Xuất toàn bộ lịch sử phù hợp trong dữ liệu đã lưu, không chỉ 500 dòng đang
  hiển thị trên giao diện. Có cả job hoàn thành, lỗi, hủy và chưa kết thúc.
- Mỗi báo cáo đọc từ một bản chụp nhất quán của sổ dữ liệu. Job đang chạy có
  thể tiếp tục tăng tiến độ sau thời điểm xuất.
- Bean thực tế tính mọi lượt thành công, kể cả trong job thất bại hoặc hủy.
- Doanh thu và thu tiền theo quy tắc sổ hiện có: job thông thường ghi doanh
  thu khi hoàn thành; đơn khách dùng số tiền thực tế đã chốt.
- Chi phí chưa biết để trống và ghi **Chờ đối soát**. Cột chi phí đã xác định
  là phần đã có dữ liệu; tổng chi phí và lợi nhuận chưa đủ dữ liệu được để trống.
- Báo cáo không chứa API key, Bot Token, mật khẩu hoặc tài khoản mail đã mua.
- File dùng UTF-8 có BOM, dấu phẩy phân cách, tiền VND viết với dấu chấm thập
  phân. Nếu Excel không tự tách cột, dùng **Data → From Text/CSV**, chọn UTF-8
  và dấu phẩy. ID dài được bảo vệ để hạn chế mất chữ số khi mở bằng bảng tính.

Một lượt xuất có giới hạn 6 MiB theo dữ liệu báo cáo đã mã hóa cho API. Nếu vượt
giới hạn, hệ thống báo lỗi rõ ràng và không trả báo cáo bị cắt ngắn. Người dùng
chưa có lịch sử vẫn nhận các file có dòng tiêu đề, số dòng dữ liệu bằng 0.
