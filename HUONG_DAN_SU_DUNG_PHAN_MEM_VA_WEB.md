# Hướng dẫn sử dụng Hoshino Tool — Phần mềm và web local

> **[Quản lý kết nối SSH và ưu tiên khách](KET_NOI_SSH_VA_UU_TIEN_KHACH_2026-11-05.md):**
> file mới **Mo_Quan_Ly_Ket_Noi_VPS.bat** lưu hồ sơ/SSH key và mở tunnel bằng
> giao diện. Job tích bean kho tự nhường job khách và chạy tiếp khi hết job khách.
> API tạo acc được phép trùng API nhập mã mời.

> **05/11/2026:** [Tạo acc tích bean và quản lý kho](KHO_ACC_TICH_BEAN_2026-11-05.md).
> Kho hiện lưu acc Mochi đã tạo, thay cho danh sách lô mail. Có mục tiêu/vô hạn,
> dừng/tiếp tục, xóa có xác nhận và xuất với lịch sử riêng. Mở giao diện Windows
> bằng **Mo_Giao_Dien_Quan_Ly_VPS.bat**.
> API mua mail tạo acc nhập riêng trong biểu mẫu. Bot dùng `/acc_mailapi` cho
> bước này; `/mailapi` và API Cài đặt chung vẫn chỉ phục vụ mail nhập mã mời.

*Cập nhật: 05/11/2026.*

> **[Bảng giá, khách hàng theo mã mời và lịch sử tạm](BANG_GIA_LICH_SU_TAM_VA_LOG_2026-11-04.md):**
> cấu hình giá theo tỷ lệ mốc bean tại tab Bảng giá. Lịch sử tạm ở Mochi có
> Log/Tiếp tục/Xóa có xác nhận; lịch sử Thống kê không bị xóa. Log combo được
> lưu bền vững và ô Ngày xem trong thống kê có lựa chọn cả tháng.

> **Mới:** [Thống kê, tiếp tục job và mua mail bù](THONG_KE_VA_TIEP_TUC_JOB_2026-11-04.md).
> Lịch sử job chuyển sang **Thống kê**, có lọc người/tất cả và bấm biểu đồ để xem
> chi tiết. Job có nút **Tiếp tục**, xác nhận trước khi dừng; **Log** tự cập nhật.
> Lỗi tài khoản được mua mail bù, dừng ở 3 lỗi liên tiếp hoặc tổng 5 lỗi/job.
> Các mô tả cũ về không mua bù sau lỗi tài khoản được thay thế bởi quy tắc mới.

Tài liệu này hướng dẫn dùng hai giao diện để quản lý cùng một VPS:

| Giao diện | Cách mở | Nơi sử dụng |
| --- | --- | --- |
| Phần mềm Windows | `Launch_HoshinoTool.bat` hoặc `Launch_VPS_Manager.bat` | Cửa sổ ứng dụng trên máy tính |
| Web local | `Launch_Web_VPS_Manager.bat` | Trình duyệt, mặc định tại `http://127.0.0.1:8787/` |

`Launch_MochiTool.bat` cũng mở trình quản lý VPS như launcher phần mềm.

Job, bot Telegram, lịch hẹn, combo, dữ liệu và đồng bộ SePay chạy trên VPS.
Máy tính dùng để nhập lệnh và theo dõi. Hai giao diện dùng chung dữ liệu VPS;
có thể chuyển giữa chúng để tiếp tục quản lý.

Web chỉ mở trên máy tính của bạn, không được đăng lên Internet. Máy tính vẫn
cần kết nối mạng và SSH tunnel để quản lý VPS.

## Mục lục

1. [Mở phần mềm hoặc web](#1-mở-phần-mềm-hoặc-web)
2. [Kết nối VPS](#2-kết-nối-vps)
3. [Cấu hình trước khi chạy job](#3-cấu-hình-trước-khi-chạy-job)
4. [Tạo job nhập mã mời](#4-tạo-job-nhập-mã-mời)
5. [Hẹn giờ và nhập mã mời combo](#5-hẹn-giờ-và-nhập-mã-mời-combo)
6. [Theo dõi, dừng job và xem lịch sử](#6-theo-dõi-dừng-job-và-xem-lịch-sử)
7. [Kho mail và lấy mã mời tài khoản](#7-kho-mail-và-lấy-mã-mời-tài-khoản)
8. [Quản lý proxy](#8-quản-lý-proxy)
9. [Telegram, admin và partner](#9-telegram-admin-và-partner)
10. [Gửi thông báo cho người được chọn](#10-gửi-thông-báo-cho-người-được-chọn)
11. [Khách hàng, số dư và SePay](#11-khách-hàng-số-dư-và-sepay)
12. [Doanh thu, chi phí và đối soát](#12-doanh-thu-chi-phí-và-đối-soát)
13. [Đóng giao diện và mở lại](#13-đóng-giao-diện-và-mở-lại)
14. [Lỗi thường gặp](#14-lỗi-thường-gặp)

## 1. Mở phần mềm hoặc web

### 1.1. Mở SSH tunnel trước

1. Mở thư mục dự án trên máy tính.
2. Chạy `Open_VPS_Tunnel.bat`.
3. Đăng nhập SSH khi được yêu cầu.
4. Giữ cửa sổ tunnel mở trong lúc quản lý VPS.

Khi nhập mật khẩu SSH, ký tự thường không hiện trên màn hình. Nhập xong rồi
bấm **Enter**. Sau khi kết nối, cửa sổ có thể đứng yên và không in thêm nội
dung; bạn tiếp tục mở giao diện quản lý.

### 1.2. Dùng phần mềm Windows

1. Chạy `Launch_HoshinoTool.bat` hoặc `Launch_VPS_Manager.bat`.
2. Chờ giao diện xuất hiện.
3. Kiểm tra nhãn kết nối VPS ở cuối thanh bên.

### 1.3. Dùng web local

1. Chạy `Launch_Web_VPS_Manager.bat`.
2. Trình duyệt mặc định tự mở giao diện web.
3. Giữ cả cửa sổ tunnel và cửa sổ launcher web mở trong lúc sử dụng.

Địa chỉ web mặc định là `http://127.0.0.1:8787/`. Lần mở đầu tiên nên dùng
launcher để tạo phiên local. Sau đó có thể tải lại trang trong cùng phiên.

Không mở trực tiếp file `web/index.html` bằng cách nhấp đúp.

### 1.4. Khi dùng trên máy tính mới

Nếu thư mục dự án đã có môi trường `.venv` hoạt động, dùng các launcher trực tiếp.
Nếu cần cài môi trường cho phần mềm Windows, mở PowerShell tại thư mục dự án:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Web local sử dụng Python và thư viện chuẩn. Có thể mở trực tiếp bằng:

```powershell
python -I main_mochi_web.py
```

## 2. Kết nối VPS

Vào **Cài đặt chung → Hạ tầng chung → Kết nối VPS**.

| Thông tin | Giá trị / cách nhập |
| --- | --- |
| Địa chỉ API VPS | Mặc định `http://127.0.0.1:8765` qua SSH tunnel |
| Token quản lý VPS | Dùng token của dịch vụ VPS đã được cấu hình |
| Địa chỉ giao diện web | Mặc định `http://127.0.0.1:8787/` |

API ở cổng **8765** và giao diện web ở cổng **8787** có hai mục đích riêng như
bảng trên. Trong ô kết nối VPS, nhập địa chỉ API.

Nếu có `data/remote_connection.json`, các giao diện tự đọc kết nối đã lưu.
Trên web, để trống token khi kết nối sẽ dùng lại token đang lưu trên máy.
Bấm **Kết nối VPS** trên phần mềm hoặc **Kết nối & lưu trên máy** trên web.

Kết nối thành công sẽ hiện **Đã kết nối VPS** và nạp dữ liệu từ VPS. Nút
**Đồng bộ VPS** dùng để lấy trạng thái mới ngay khi cần.

## 3. Cấu hình trước khi chạy job

### Nguồn mail và đơn giá

Vào **Cài đặt chung → Mochi Chat**:

1. Chọn **GmailVip.vn**, **1TrieuMail.com** hoặc **Cả hai shop**.
2. Nhập API key cho nguồn mail sử dụng.
3. Nhập Product ID hoặc thứ tự ưu tiên sản phẩm.
4. Nhập giá bán theo **VND / 100 bean**.
5. Chọn số luồng Direct và số job được chạy cùng lúc.
6. Bấm **Lưu thay đổi**.

Ví dụ thứ tự ưu tiên khi dùng cả hai shop:

```text
gmailvip:3445, 1trieumail:3423
```

Khi sửa cấu hình, các ô API chung để trống sẽ giữ key đã lưu trên VPS.
Đơn giá được chốt cho từng job khi tạo; thay đổi đơn giá áp dụng cho job mới.

Khi mua mail bị giới hạn tốc độ, hệ thống chờ ít nhất **10 giây** rồi thử lại
cùng sản phẩm/lô, tối đa ba lần. Chuyển sản phẩm hoặc shop vì hết hàng cũng
chờ 10 giây trước khi mua tiếp. Nếu shop yêu cầu chờ lâu hơn thì theo shop.
Trường hợp mất phản hồi, chưa biết giao dịch có trừ tiền hay không, job dừng
để đối soát.

### Đường mạng và số luồng

Tại **Hạ tầng chung**, chọn các bước dùng proxy: đăng nhập Google, đăng nhập
Mochi, nhập mã mời và mua mail. **Proxy Pool** và **Proxy mua mail** có danh sách riêng.

Luồng qua proxy được tính theo số proxy và số luồng trên mỗi proxy. Ví dụ
5 proxy × 1 luồng/proxy tương ứng tối đa 5 tài khoản đồng thời qua pool đó.

Giới hạn số job chạy cùng lúc dùng chung cho job từ máy tính và Telegram.
Job chưa được cấp chạy sẽ nằm trong hàng đợi.

## 4. Tạo job nhập mã mời

Vào **Mochi Chat → Nhiệm vụ → Thiết lập job**:

1. Nhập tên khách hoặc ghi chú nếu cần.
2. Nhập mã mời.
3. Nhập tổng số bean muốn đạt.
4. Kiểm tra số tài khoản và tổng tiền dự kiến.
5. Bấm **Bắt đầu nhập mã**.

Ví dụ: mã `VBRN5H`, mục tiêu **1.000 bean** tương ứng **10 lượt thành công**.
Mỗi lượt thành công được tính 100 bean. Mục tiêu được quy đổi theo số lượt
cần thiết; nhập bội số của 100 để khớp số bean mong muốn.

**Tổng tiền dự kiến** tự tính theo đơn giá VPS. **Lưu nháp** chỉ lưu thông tin
biểu mẫu trên máy tính hoặc trình duyệt đang dùng; chưa tạo job.

### Giới hạn mã mời trong 24 giờ

Hệ thống đếm tối đa **10 lượt nhập thành công / mã / 24 giờ gần nhất**.

- Lượt thành công được giữ trong bộ đếm kể cả job cuối cùng thất bại hoặc bị hủy.
- Lượt nhập lỗi không được cộng thành lượt thành công.
- Bộ đếm dùng chung cho cùng mã, dù job tạo từ phần mềm, web hay bot.
- Nếu đã đủ 10 lượt, job thông thường trả trạng thái giới hạn và không mua mail để thử.

Khoảng 24 giờ tính theo thời điểm các lượt thành công; không đặt lại bộ đếm
chỉ vì sang ngày mới.

## 5. Hẹn giờ và nhập mã mời combo

### Hẹn giờ bắt đầu

1. Điền biểu mẫu job.
2. Tích **Hẹn giờ chạy**.
3. Chọn ngày, giờ theo **giờ Việt Nam**.
4. Bấm nút tạo lịch.

Lịch được lưu trên VPS. Đến giờ, job vào hàng đợi chung để chạy. Trong lúc chờ
lịch chưa mua mail. Có thể tắt giao diện sau khi lịch đã được VPS ghi nhận.

### Combo qua nhiều đợt

1. Nhập mã mời và **tổng bean của toàn bộ combo**.
2. Tích **Nhập mã mời combo**.
3. Bấm **Bắt đầu combo**. Có thể kết hợp hẹn giờ cho đợt đầu.

Ví dụ **5.500 bean** cần 55 lượt thành công. Khi mã chưa có lượt gần đây,
các đợt thường là:

```text
1.000 + 1.000 + 1.000 + 1.000 + 1.000 + 500 = 5.500 bean
```

Khi chạm giới hạn, job chuyển sang **Chờ lượt combo**, giữ cùng mã job và
tổng bean đã đạt. VPS hẹn tiếp sau **24 giờ 1 phút** tính từ lúc đợt đó báo
giới hạn. Nếu bộ đếm yêu cầu chờ lâu hơn, lịch được đặt sau mốc khả dụng một phút.

Xem giờ chạy tiếp trong **Job đang chạy**. Đợt cuối chỉ chạy phần còn thiếu.
Lịch và tiến độ được khôi phục khi dịch vụ VPS khởi động lại.

Combo tự hẹn tiếp khi đạt giới hạn 24 giờ hoặc khi đợt chạy bị ngắt do dịch vụ
đang đóng. Các lỗi như mã sai, shop mail, API, proxy hoặc đăng nhập vẫn cần
kiểm tra log và xử lý nguyên nhân.

## 6. Theo dõi, dừng job và xem lịch sử

| Tab / nút | Cách sử dụng |
| --- | --- |
| **Dashboard** | Xem tổng quan và job gần đây |
| **Job đang chạy** | Xem tiến độ, trạng thái, bean và lịch tiếp tục |
| **Log** tại từng job | Mở nhật ký đúng mã job đó; dòng mới tự nối khoảng mỗi giây |
| **Đồng bộ VPS** | Lấy danh sách và trạng thái mới |
| **Dừng** tại từng job | Xác nhận rồi dừng job hoặc hủy lịch chờ của job đó |
| **Tiếp tục** tại từng job | Tạo job mới cho phần bean còn thiếu, giữ người sở hữu/đơn giá |
| **Mochi Chat → Lịch sử tạm** | Xem log, tiếp tục job thất bại hoặc xóa danh sách tạm có xác nhận; không xóa Thống kê |
| **Thống kê → Lịch sử job / Chi tiết** | Chọn tháng, từng người/tất cả, phân trang và xuất CSV lịch sử |

Job đang xử lý/chờ được ưu tiên, các job mới nằm trước. Sau khi tạo, giao diện
tự mở tab và chọn job mới. Danh sách tự cập nhật khoảng 2,5 giây/lần.

Khi mất kết nối, bảng có thể đang hiển thị dữ liệu lần đồng bộ trước. Nối lại
và xem trạng thái trước khi gửi thêm lệnh tạo job.

Với combo, số bean trong bảng là tổng đã đạt qua các đợt. Khi hủy đơn khách,
hệ thống tính theo bean đã thành công và hoàn phần tiền chưa dùng.

Tab **Bảng giá** cho phép khai báo, ví dụ 2.000 bean = 15.000 VND. Khi lưu,
job mới tính theo tỷ lệ mốc (1.000 bean = 7.500 VND); job đã tạo giữ giá cũ.
Nếu bảng rỗng, dùng lại đơn giá VND / 100 bean ở Cài đặt chung.

Tab **Thống kê** có biểu đồ lợi nhuận, số job và lượt khách theo ngày của tháng,
cùng biểu đồ tròn tỷ lệ lý do lỗi. Bấm cột để xem danh sách job theo người,
các dòng tổng tài chính hoặc mã mời trong ngày. Có tổng lượt khách hôm nay,
tuần này và tháng đang chọn. Một đợt combo kết thúc tính một lượt khách;
doanh thu/chi phí cả combo chỉ ghi vào ngày job kết thúc cuối cùng.

## 7. Kho mail và lấy mã mời tài khoản

### Dùng lại lô mail đã mua

1. Vào **Mochi Chat → Kho tài khoản**.
2. Làm mới danh sách lô, hoặc chọn file JSON để đưa lên VPS.
3. Chọn lô muốn dùng cho job tiếp theo.
4. Trở lại **Thiết lập job**, kiểm tra dòng nguồn mail rồi tạo job.

Chế độ dùng lô chỉ xử lý mail chưa hoàn tất trong lô và không tự mua thêm.
Muốn mua mail theo cấu hình chung, chọn lại **Mua mail theo cấu hình VPS**.

### Lấy mã mời của tài khoản

1. Vào **Mochi Chat → Lấy mã mời**.
2. Nhập email và mật khẩu Google cần tra cứu.
3. Bấm **Đăng nhập & lấy mã** và chờ VPS xử lý.
4. Xem email, ID, tên hiển thị, mã mời và bean; sao chép mã hoặc xuất CSV.

Nếu đăng nhập yêu cầu xác minh hoặc có lỗi đường mạng, xem trạng thái và log
để xử lý. Có thể bấm **Hủy** lần tra cứu đang chạy.

## 8. Quản lý proxy

Chọn **Proxy Pool** để quản lý proxy của tài khoản, hoặc **Proxy mua mail**
để quản lý đường mua mail riêng.

1. Chọn đúng nhà cung cấp.
2. Dùng **Nhập TXT** hoặc **Dán proxy**, mỗi dòng một proxy.
3. Bấm **Check all** hoặc **Check** tại một dòng để kiểm tra.
4. Dùng **Xoay**, **Xóa**, tìm kiếm và **Lưu / Xuất TXT** khi cần.

Ví dụ định dạng proxy xoay, với các giá trị minh họa:

```text
Proxy.vn:          IP:PORT|KEY_XOAY
muaproxyxoay.net:  IP:PORT:USER:PASSWORD|KEY_XOAY
```

Bảng chia proxy hoạt động và proxy lỗi. Đọc cột chi tiết khi kết nối không thành
công. Proxy đang được sử dụng có thể chưa cho phép xóa ngay.

Trên web, tệp TXT/CSV được tải xuống theo cài đặt của trình duyệt.

## 9. Telegram, admin và partner

### Bật bot và cấp quyền

1. Vào **Telegram → Người dùng và bot**.
2. Nhập Bot Token nếu chưa cấu hình, bấm **Lưu cấu hình** rồi **Bật bot**.
3. Người dùng nhắn riêng `/id` cho bot để lấy Chat ID.
4. Trong **Quản lý người dùng**, nhập Chat ID, tên và vai trò `admin` hoặc `partner`.
5. Bấm **Thêm / Lưu người dùng** và kiểm tra trạng thái được cấp quyền.

Có thể chọn **Tự bật bot khi VPS khởi động**. Khi đổi Bot Token, tắt bot trước.

### Hai API mua mail riêng

Admin và partner cần nhập đủ hai API trước khi tạo job. Có hai cách:

- Trên giao diện: chọn đúng dòng người dùng, nhập hai API rồi bấm **Lưu 2 API mua mail**.
- Trong chat riêng với bot: gửi `/mailapi KEY_GMAILVIP KEY_1TRIEUMAIL`.

Các tên `KEY_GMAILVIP` và `KEY_1TRIEUMAIL` là chỗ để thay bằng key của người dùng.
Nguồn shop, sản phẩm và số luồng vẫn lấy từ cấu hình chung trên VPS.

### Lệnh dùng cho admin và partner

| Lệnh | Chức năng |
| --- | --- |
| `/help` | Xem hướng dẫn |
| `/id` | Xem Chat ID |
| `/stats` | Xem thống kê của mình |
| `/baocao` | Nhận hai file CSV lịch sử job và mua mail của mình |
| `/mailapi` | Xem trạng thái hai API đã nhập |
| `/mailapi KEY_GMAILVIP KEY_1TRIEUMAIL` | Lưu hai API riêng |
| `/job MÃ_MỜI SỐ_BEAN [TÊN_KHÁCH]` | Tạo job thông thường |
| `/combo MÃ_MỜI SỐ_BEAN [TÊN_KHÁCH]` | Tạo combo |
| `/hen MÃ_MỜI SỐ_BEAN DD/MM/YYYY HH:MM [TÊN_KHÁCH]` | Hẹn giờ Việt Nam |
| `/lich` | Xem lịch job của mình |
| `/huy MÃ_JOB` | Hủy job hẹn giờ/chờ của mình |

Ví dụ:

```text
/job VBRN5H 1000 Khách A
/combo VBRN5H 5500 Khách A
/hen VBRN5H 1000 04/11/2026 09:30 Khách A
```

Khi dùng ví dụ hẹn giờ, thay ngày giờ bằng một thời điểm tương lai.

### Lệnh quản lý partner dành cho admin

| Lệnh | Chức năng |
| --- | --- |
| `/partner_add CHAT_ID TÊN` | Thêm mới, khôi phục quyền hoặc cập nhật tên partner |
| `/partner_remove CHAT_ID` | Xóa quyền partner, giữ lịch sử |
| `/partners [TRANG]` | Danh sách partner, 10 người mỗi trang |
| `/partner CHAT_ID` | Xem thông tin, bean, job và tài chính của partner |

Tài khoản gửi lệnh phải có vai trò **admin** và đang được cấp quyền. Danh sách
partner có cả người đã bị thu hồi quyền, kèm trạng thái. Thêm lại cùng Chat ID
giữ dữ liệu cũ. Việc cấp hoặc đổi vai trò admin thực hiện trên giao diện quản lý.

## 10. Gửi thông báo cho người được chọn

Vào **Telegram → Người dùng và bot → Gửi thông báo đến admin / partner**:

1. Bật bot và chờ kết nối sẵn sàng.
2. Tại **Gửi đến**, chọn một chế độ:
   - **Tất cả admin / partner**.
   - **Chỉ admin**.
   - **Chỉ partner**.
   - **Chọn từng người**, rồi tích tên/Chat ID muốn nhận.
3. Kiểm tra số người đã chọn.
4. Nhập nội dung và bấm **Gửi thông báo**.

Giới hạn mỗi lượt: **2000 người nhận**, nội dung tối đa **4000 đơn vị theo bộ
đếm hiển thị**; một số emoji chiếm nhiều hơn một đơn vị.

Lựa chọn người nhận độc lập với dòng đang chọn hoặc ô tìm kiếm ở bảng người
dùng. Ở chế độ chọn từng người, người mới xuất hiện không tự được tích.
Danh sách rỗng không gửi; người bị thu hồi quyền được loại khỏi lựa chọn.

Tiến độ hiển thị thành công, lỗi/chưa xác nhận, bỏ qua và chưa hoàn tất.
**Thành công** nghĩa là Telegram đã xác nhận gửi, chưa xác nhận người nhận đã đọc.
**Dừng gửi** chỉ dừng lượt thông báo hiện tại.

## 11. Khách hàng, số dư và SePay

### Quản lý khách hàng trên giao diện

Vào **Telegram → Quản lý khách hàng**:

- Thêm hoặc khôi phục khách bằng Chat ID.
- Chọn khách để xem giao dịch và đơn hàng.
- **Sửa số dư:** nhập số dư khả dụng cuối cùng muốn đặt và lý do điều chỉnh.
- **Xóa khách hàng:** dùng khi khách không còn đơn chưa chốt theo điều kiện hệ thống.
- **Chốt đơn gián đoạn:** kiểm tra log và tiến độ trước; job còn worker trên VPS
  chưa được chốt bằng thao tác này.

Khách đặt đơn bằng ví: hệ thống tạm giữ tiền khi tạo, thu theo bean thực tế đã
thành công và hoàn phần còn lại khi chốt. Combo giữ cùng đơn qua các đợt.

### Cấu hình SePay

Vào **Telegram → Thanh toán SePay**:

1. Nhập API Token, Bank Account ID và thông tin tài khoản nhận tiền.
2. Bấm **Lưu cấu hình**.
3. Bấm **Bật đồng bộ**.
4. Theo dõi bảng giao dịch và xử lý các dòng cần đối soát.

Tắt đồng bộ trước khi sửa cấu hình SePay. Có thể bật tùy chọn tự đồng bộ khi
VPS khởi động. Nội dung nạp tiền là **Chat ID khách hàng**; giới hạn mỗi giao
dịch từ **10.000 đến 100.000 VND**.

### Lệnh bot dành cho khách hàng

| Lệnh | Chức năng |
| --- | --- |
| `/id` | Xem Chat ID |
| `/sodu` | Xem số dư và thống kê |
| `/nap` | Xem hướng dẫn nạp tiền |
| `/job MÃ_MỜI SỐ_BEAN` | Đặt đơn từ ví |
| `/combo MÃ_MỜI SỐ_BEAN` | Đặt combo từ ví |
| `/donhang` | Xem đơn hàng của mình |
| `/giaodich` | Xem lịch sử giao dịch |
| `/hen ...`, `/lich`, `/huy MÃ_JOB` | Hẹn giờ, xem lịch và hủy job chưa chạy của mình |

## 12. Doanh thu, chi phí và đối soát

Theo dõi ở **Telegram → Người dùng và bot**, bảng người dùng và mục
**Giao dịch / Đối soát**.

| Số liệu / thao tác | Ý nghĩa |
| --- | --- |
| Job hoàn thành | Số job có kết quả hoàn thành |
| Job thất bại | Số job kết thúc lỗi |
| Bean đã bán / đã chạy | Bean thực tế từ các lượt thành công, có cả phần thành công trong job lỗi/hủy |
| Chi phí mail | Chi phí ghi nhận từ các lần mua mail |
| Chốt chi phí giao dịch | Bổ sung số tiền cho giao dịch mail cần đối soát |
| Bổ sung đơn giá job | Chốt đơn giá cho job còn thiếu giá |

Kiểm tra các khoản còn chờ đối soát trước khi dùng số liệu lợi nhuận để tổng
kết. Thao tác thu hồi quyền người dùng giữ lịch sử và thống kê đã có.

### Xuất CSV chi tiết theo từng người

Tại **Telegram → Người dùng và bot → Quản lý người dùng**, chọn một dòng và
bấm **Xuất báo cáo CSV**. Tool cho chọn thư mục để lưu hai file; web mở cửa sổ
với nút tải riêng cho lịch sử job và lịch sử giao dịch mua mail.

Trên bot, `/baocao` xuất báo cáo của mình. Admin dùng `/baocao CHAT_ID` để xuất
cho partner hoặc admin khác. Partner chỉ xuất được của chính mình.

Xem [hướng dẫn và các cột báo cáo CSV](BAO_CAO_CSV_THEO_NGUOI_DUNG_2026-11-03.md).

## 13. Đóng giao diện và mở lại

| Thao tác | Kết quả |
| --- | --- |
| Đóng phần mềm Windows | Job và dịch vụ trên VPS tiếp tục chạy |
| Đóng tab trình duyệt | VPS tiếp tục chạy job, lịch, bot và SePay |
| Đóng launcher web hoặc bấm `Ctrl+C` | Dừng web trên máy tính; VPS vẫn chạy |
| Đóng SSH tunnel hoặc mất mạng máy tính | Tạm mất khả năng quản lý; VPS vẫn chạy |
| Bấm Dừng job / Tắt bot / Dừng gửi / Tắt đồng bộ | Gửi đúng lệnh dừng tương ứng đến VPS |

Để dùng tiếp, mở lại tunnel và giao diện muốn dùng, rồi **Đồng bộ VPS**.
VPS và dịch vụ phải đang hoạt động để job thực sự được xử lý.

## 14. Lỗi thường gặp

| Hiện tượng | Cách xử lý |
| --- | --- |
| Không kết nối được VPS | Kiểm tra mạng, cửa sổ SSH tunnel và địa chỉ API `http://127.0.0.1:8765`; kết nối lại rồi đồng bộ |
| Token quản lý không đúng | Kiểm tra token của dịch vụ VPS; nhập lại tại Cài đặt chung |
| Web báo chưa có phiên local | Mở bằng `Launch_Web_VPS_Manager.bat` để tạo phiên, rồi dùng tab được mở tự động |
| Web không tải sau khi đóng cửa sổ launcher | Mở lại launcher web; giữ cửa sổ đó mở khi sử dụng |
| Port 8787 đang dùng | Đóng cửa sổ web local cũ hoặc đổi port bằng lệnh bên dưới |
| Tạo job nhưng chưa thấy trong bảng | Mở Job đang chạy, bấm Đồng bộ VPS và tìm theo mã job/mã mời |
| Job nằm trong hàng đợi | Xem giới hạn số job cùng lúc và các job đang xử lý |
| Mã đã đủ 10 lượt | Xem trạng thái giới hạn; dùng combo để tự lưu lịch chạy tiếp đến đủ mục tiêu |
| Job lỗi mail/API/proxy/đăng nhập | Mở Log đúng job, xử lý nguyên nhân và kiểm tra mail đã mua trong Kho tài khoản |
| Partner không tạo được job | Kiểm tra quyền truy cập và trạng thái đã nhập đủ hai API riêng |
| Admin không dùng được lệnh quản lý partner | Kiểm tra vai trò admin và quyền đang active của đúng Chat ID |
| Nút Gửi thông báo bị khóa | Kiểm tra kết nối, bot sẵn sàng, người nhận, độ dài nội dung và lượt gửi đang chạy |
| VPS báo danh sách người nhận đã thay đổi | Đồng bộ lại và chọn lại người đủ quyền |
| Mất kết nối sau khi bấm tạo job/gửi lệnh | Nối lại và kiểm tra kết quả trước khi thao tác lần nữa |

Đổi cổng web bằng PowerShell tại thư mục dự án:

```powershell
.\Launch_Web_VPS_Manager.bat --port 8788
```

## Tài liệu chi tiết

- [Web local](docs/WEB_LOCAL.md)
- [Triển khai và kết nối VPS Ubuntu](docs/VPS_UBUNTU.md)
- [Nhập mã mời combo](COMBO_MA_MOI_2026-11-03.md)
- [Bộ đếm mã mời 24 giờ](GIOI_HAN_MA_MOI_24H_2026-11-03.md)
- [Admin quản lý partner qua bot](QUAN_LY_PARTNER_BOT_2026-11-03.md)
- [Chọn người nhận thông báo](THONG_BAO_TELEGRAM_2026-11-02.md)
