# Doanh thu, chi phí và lợi nhuận theo người dùng Telegram

Mở `Launch_HoshinoTool.bat`, vào **Telegram**. Điền **Giá bán (VND / 100 bean)** rồi bấm **Lưu đơn giá**. Ví dụ nhập `1000` nghĩa là mỗi 100 bean bán 1.000 VND. Dùng dấu chấm cho phần thập phân, ví dụ `1000.25`; không nhập dấu phân cách hàng nghìn.

## Cách tính

| Số liệu | Cách ghi nhận |
| --- | --- |
| Doanh thu | Chỉ khi job có trạng thái hoàn thành: số lượt nhập mã thành công × giá bán cho 100 bean đã chốt lúc tạo job. |
| Chi phí | Số dư shop trước mua trừ số dư sau mua, ghi cho Chat ID sở hữu job ngay trong giao dịch mua. Toàn bộ lô đã trả tiền được tính, kể cả mail chưa dùng, mail lỗi hoặc job thất bại/hủy. |
| Lợi nhuận | Doanh thu trừ chi phí đã ghi nhận. Có thể âm. Nếu thiếu đơn giá hoặc có giao dịch chưa biết chi phí, hiển thị **Chờ đối soát**. |

Ví dụ giá bán 1.000 VND / 100 bean, job hoàn thành 10 lượt, mua lô mail hết 530,27 VND: doanh thu 10.000 VND, chi phí 530,27 VND, lợi nhuận 9.469,73 VND. Nếu job thất bại sau 2 lượt, doanh thu đơn hàng là 0; toàn bộ 530,27 VND vẫn là chi phí. Bộ đếm bean thực tế vẫn giữ 200 bean đã chạy, như trước.

Các cột mới ở cuối bảng người dùng là Doanh thu, Chi phí và Lợi nhuận. Ba thẻ phía trên tổng hợp các người dùng. `/stats` qua bot có số liệu tài chính của chính người gửi. Admin/partner vẫn chỉ là nhãn phân loại.

Chủ sở hữu job từ bot xác định bằng **Chat ID của người tạo job qua Telegram**, không suy đoán theo tên khách hàng. Đổi tên hoặc thu hồi quyền không xóa số liệu. Job tạo trực tiếp trong tool từ bản cập nhật Local được gắn vào người dùng hệ thống **Local**, với cùng cách tính doanh thu/chi phí/lợi nhuận. Xem [người dùng Local](NGUOI_DUNG_LOCAL_2026-11-01.md).

## Chốt giá và chống cộng trùng

- Mỗi job giữ đơn giá lúc được tạo, trước khi worker khởi chạy. Đổi giá chung chỉ áp dụng cho job mới.
- Kết quả job gửi lại không làm cộng doanh thu hai lần. Biên nhận mua mail có định danh giao dịch; các cập nhật đang chờ/đã chốt và phản hồi lặp không nhân đôi chi phí.
- Dùng lại lô mail đã mua không cộng lại chi phí. Chi phí thuộc người sở hữu lần mua ban đầu; không tự chuyển chi phí sang người dùng lô sau đó.
- Các đơn cũ trước bản cập nhật không có giá bán/chi phí gốc nên không tự suy đoán hoặc áp giá mới cho lịch sử. Thống kê tiền bắt đầu từ giao dịch được ghi nhận bởi bản cập nhật này; các bộ đếm job/bean cũ giữ nguyên.

## Đọc số dư thực tế

Theo JSON profile đã cung cấp, `data.money` là số dư. Với job được nối vào sổ tài chính, tool đọc `/api/profile.php` trước và sau yêu cầu mua. Giữ khóa dùng chung theo shop/API key cho cả ba bước: đọc trước → mua → đọc sau và lưu biên nhận. Các job trong tool không xen giao dịch vào khoảng này. Dữ liệu tiền lưu bằng số nguyên tương ứng 1/100 VND, không cộng bằng số thực nhị phân; giữ chính xác các số như `5607.73` hoặc `0.65`.

Khóa chỉ áp dụng cho các job trong cùng tiến trình tool. Mua/nạp/rút từ website hoặc ứng dụng khác dùng cùng tài khoản trong khoảng đọc số dư có thể làm chênh lệch không còn là chi phí của riêng giao dịch. Khi đó cần đối chiếu lịch sử shop và đối soát; không dùng thống kê chênh lệch như một hóa đơn độc lập với các giao dịch bên ngoài.

- Không đọc được số dư trước mua: dừng trước khi gửi yêu cầu mua.
- Đã mua nhưng không đọc được số dư sau: giữ biên nhận thiếu chi phí, không coi là 0 VND. Mail trả về vẫn được xử lý/lưu theo luồng hiện có.
- Số dư tăng bất thường hoặc không xác định được trạng thái request: giữ giao dịch cần đối soát, không tự gửi lại một yêu cầu mua chưa rõ kết quả.
- Shop từ chối vì giới hạn tốc độ: ghi nhận lượt từ chối không bị trừ tiền, giữ cơ chế chờ/xoay/thử lại hiện có. Mỗi lần thử có định danh riêng.

## Đối soát

Bấm **Giao dịch / đối soát**:

1. Tab **Mua mail** có tên người dùng, job, shop, số mail yêu cầu, chi phí, số dư trước/sau và mã giao dịch. Chọn giao dịch thiếu chi phí, đối chiếu shop rồi nhập số tiền thực tế. Không sửa giao dịch đã chốt chi phí; không đối soát khi job đang chạy.
2. Tab **Job chưa có giá** cho phép bổ sung đơn giá đã thỏa thuận cho một job được tạo khi chưa đặt giá chung. Không thay đổi các job đã chốt giá.

Số tiền có dấu `*` trong bảng/thẻ chỉ là phần đã xác định. Lợi nhuận chưa được kết luận nếu vẫn thiếu số tiền hoặc đơn giá.

## Dữ liệu và kiểm chứng

- Sổ job và mua mail: `data/telegram_users.sqlite3`.
- Biên nhận phục hồi: `data/mail_purchase_receipts/telegram_users/*.json`. Lưu trước khi cập nhật SQLite, phục hồi khi mở tool nếu lần ghi cơ sở dữ liệu trước bị lỗi. Không lưu API key, email hoặc mật khẩu trong biên nhận; chỉ lưu định danh băm của tài khoản shop và thông tin giao dịch.
- Khi dùng INI/đường dẫn SQLite tùy chỉnh, biên nhận nằm cạnh file SQLite trong thư mục `mail_purchase_receipts/<tên file dữ liệu>/`.
- Nguồn gốc và `src/` đồng bộ. Chưa đóng gói lại EXE.

189 kiểm thử liên quan đã qua, gồm 31 kiểm thử tài chính mới (24 sổ giao dịch/luồng mua và 7 GUI). Kiểm tra gồm: số tiền lẻ, tính chi phí khi mail chưa dùng, phản hồi/giao dịch lặp, hai job dùng chung API key, giao dịch mất phản hồi, hủy, phục hồi biên nhận, mail mua nhưng dữ liệu lỗi, dùng lại lô cho người khác, chốt giá theo job và sửa tên/thu hồi quyền. Tất cả dùng file/SQLite/HTTP giả lập; không gửi mua mail hoặc đăng nhập bot/shop thật.

```powershell
py -3.14 -I tests/test_finance.py
py -3.14 -I tests/test_finance_ui.py
```
