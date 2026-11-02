# Số luồng theo dung lượng proxy — 01/11/2026

Khi các tài khoản dùng proxy để đăng nhập Google, đăng nhập Mochi hoặc nhập mã, số luồng tối đa tự tính bằng số proxy duy nhất × luồng/proxy. Ô số luồng nhập tay trước đây không còn chặn tổng này.

| Cấu hình | Số tài khoản xử lý đồng thời tối đa |
|---|---:|
| 5 proxy × 1 luồng/proxy | 5 |
| 5 proxy × 2 luồng/proxy | 10 |
| 6 proxy × 10 luồng/proxy | 60 |
| Một key xoay × 4 luồng/proxy | 4 |
| Dung lượng 5 luồng, chỉ còn 3 lượt cần làm | 3 |

Giao diện hiển thị phép tính và tổng luồng, đồng thời khóa ô nhập tổng khi chạy theo proxy. Đổi số proxy, nguồn proxy hoặc luồng/proxy sẽ cập nhật tổng. Khi tắt proxy cho các bước tài khoản, ô nhập tay được mở lại và giữ giá trị Direct trước đó. Chỉ mua mail qua proxy không giới hạn số luồng của các tài khoản đi Direct.

`ProxyBroker.account_capacity` cộng slot theo danh tính gateway/key duy nhất. Một proxy được nhập bằng nhiều dạng hoặc một key xoay xuất hiện nhiều lần không làm tăng dung lượng. Các job vẫn dùng chung giới hạn slot của broker; slot tạm bận/chờ xoay không bị coi là mất cấu hình proxy.

Runner lấy tổng slot từ broker, giới hạn thêm theo số lượt và số mail còn lại. Khi một tài khoản kết thúc, slot rảnh được phân cho tài khoản tiếp theo. Vẫn chỉ mua một lô mail bằng số lượt cần; sau khi lưu lô, các worker được chạy đầy dung lượng mà không chờ tài khoản đầu hoàn tất.

## Kiểm chứng

- 10 kiểm thử mới dùng hàng rào bất đồng bộ để đo số tài khoản thực sự đang xử lý, kiểm tra riêng giới hạn từng proxy, lấp slot rảnh, giới hạn theo mục tiêu, proxy trùng và luồng sau mua lô.
- 5 kiểm thử GUI mới kiểm tra 5 proxy dù giá trị nhập tay cũ là 1, tăng tổng lên 60, cập nhật khi danh sách đổi, chế độ Direct/chỉ mua mail qua proxy, proxy trùng và một key xoay.
- Các kiểm tra dùng tài khoản, proxy và shop giả lập; không mua mail, không đăng nhập hoặc nhập mã thật.
- Nguồn đồng bộ ở gốc và `src/`. Mở lại bằng `Launch_MochiTool.bat` để dùng mã mới; chưa đóng gói lại EXE.

Lệnh cho phần mới:

```powershell
py -3.14 -I tests/test_proxy_concurrency.py
py -3.14 -I tests/test_browser_gui.py
```
