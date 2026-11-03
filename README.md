# hoshino-tool — Mochi Chat, Proxy Pool & Telegram

> **[CSV báo cáo theo người dùng](BAO_CAO_CSV_THEO_NGUOI_DUNG_2026-11-03.md):**
> chọn người trong bảng Telegram rồi bấm **Xuất báo cáo CSV** trên tool/web.
> Bot dùng `/baocao`: partner xuất của mình; admin dùng `/baocao CHAT_ID` để xuất
> lịch sử job và mua mail của partner hoặc admin khác.

> **Chờ mua lại mail:** sau khi shop từ chối, lần thử lại hoặc chuyển sản phẩm/shop
> vì hết hàng chờ tối thiểu **10 giây**. Nếu `Retry-After` dài hơn thì chờ theo shop.
> Mất phản hồi giao dịch vẫn dừng để đối soát, tránh mua trùng.

> **[Hướng dẫn sử dụng phần mềm và web](HUONG_DAN_SU_DUNG_PHAN_MEM_VA_WEB.md):**
> cách mở, kết nối VPS, tạo job/combo, proxy, Telegram, khách hàng, SePay và xử lý lỗi.

> **[Web local quản lý VPS](docs/WEB_LOCAL.md):** mở `Launch_Web_VPS_Manager.bat`
> để dùng giao diện giống phần mềm trên trình duyệt tại localhost. Dùng chung
> SSH tunnel và cấu hình kết nối; job vẫn chạy trên VPS khi đóng web.

> **[Admin quản lý partner trên bot](QUAN_LY_PARTNER_BOT_2026-11-03.md):** dùng
> `/partner_add CHAT_ID TÊN`, `/partner_remove CHAT_ID`, `/partners [TRANG]` và
> `/partner CHAT_ID` trong chat riêng. Xóa quyền vẫn giữ lịch sử job và tài chính.

> **[Nhập mã mời combo](COMBO_MA_MOI_2026-11-03.md):** nhập tổng bean, bật Combo
> hoặc nhắn `/combo MÃ_MỜI SỐ_BEAN`. Đạt giới hạn ngày thì lưu cùng job và tự chạy
> tiếp sau 24 giờ 1 phút đến đủ mục tiêu; giữ tiến độ/lịch khi VPS khởi động lại.

> **[Giới hạn mã mời 24 giờ](GIOI_HAN_MA_MOI_24H_2026-11-03.md):** cộng mọi lượt nhập
> thành công theo mã, kể cả trong job lỗi/hủy. Đủ10 lượt trong24h gần nhất trả
> giới hạn ngay, không mua mail thử. Còn ít lượt chỉ mua phần quota còn lại;
> giữ bộ đếm khi mở lại và nhập lịch sử thành công cũ một lần.

> **Chế độ VPS Ubuntu + quản lý Windows:** xem [hướng dẫn triển khai](docs/VPS_UBUNTU.md).
> Launcher mặc định mở trình quản lý VPS; tác vụ chạy trong dịch vụ `hoshino` trên Ubuntu.
> API đi qua SSH tunnel. Dùng `main_mochi_gui.py --local` để mở bản local cũ.
> Các mô tả “100% cục bộ” bên dưới thuộc kiến trúc trước khi thêm chế độ VPS.

> **03/11/2026 — giao diện quản lý VPS giống tool cũ:** khôi phục logo/thanh bên,
> Dashboard, biểu mẫu job hai cột, các tab Mochi, bảng proxy và ba tab Telegram.
> Kết nối VPS tại **Cài đặt chung → Hạ tầng chung**; xử lý vẫn chạy trên VPS.

> **Đồng bộ job VPS:** bảng Job đang chạy ưu tiên job đang xử lý và job mới;
> tạo từ máy tính tự mở/chọn job, job từ bot tự cập nhật. Có nút **Đồng bộ VPS**
> và thời điểm cập nhật; job kết thúc nhanh vẫn được hiển thị.

> Thêm [Mochi Chat → Lấy mã mời](LAY_MA_MOI_TAI_KHOAN_2026-11-02.md): nhập email/mật khẩu Google để thu thập mã mời, tên hiển thị và số dư beans của tài khoản, kèm ID và giờ cập nhật. Có lưu danh sách, sao chép mã và xuất CSV; không lưu mật khẩu/token. Đối chiếu API với `mochi-api-analysis` và XAPK 1.3.2/build38, dùng `remainingQuota` để đọc beans.

> Thêm [ô gửi thông báo cho admin/partner được chọn](THONG_BAO_TELEGRAM_2026-11-02.md): **Telegram → Người dùng và bot → Gửi thông báo đến admin / partner**. Chọn tất cả, chỉ admin, chỉ partner hoặc tích từng người; kiểm tra số người đã chọn, nhập nội dung và bấm Gửi thông báo. Có bộ đếm độ dài, tiến độ thành công/lỗi/bỏ qua và Dừng gửi; chỉ gửi tới các ID đang được cấp quyền.

> Thêm [hai API mua mail riêng cho từng admin/partner](API_RIENG_TELEGRAM_2026-11-02.md). Nhập tại **Telegram → Quản lý người dùng → chọn Chat ID → Lưu 2 API mua mail**, hoặc nhắn riêng `/mailapi KEY_GMAILVIP KEY_1TRIEUMAIL`. Cần đủ cả hai key trước khi tạo job. Nguồn shop, product ID và số luồng vẫn quản lý chung trên máy tính; job admin/partner không dùng API chung để thay thế key còn thiếu.

> Thêm [hẹn giờ job trên tool và Telegram](HEN_GIO_JOB_2026-11-02.md): tích **Hẹn giờ chạy** ở Thiết lập job hoặc nhắn `/hen MÃ_MỜI SỐ_BEAN DD/MM/YYYY HH:MM [TÊN_KHÁCH]`, theo giờ Việt Nam. `/lich` xem lịch, `/huy MÃ_JOB` hủy job chưa chạy của mình. Lịch lưu khi đóng/mở lại; đến giờ vào hàng đợi chung, chưa chạy thì chưa mua mail. Khách hủy lịch được hoàn tiền tạm giữ.

> [Chẩn đoán Proxy.vn bị reset khi kiểm tra IP](PROXYVN_RESET_2026-11-01.md): hiển thị nguyên nhân và mã curl từ các nguồn kiểm tra, thử lại một kết nối mới tới cùng proxy, không nhầm timeout với cooldown xoay. Ba endpoint trong ảnh hiện nhận curl 56/Windows 10054; cần nhà cung cấp xác nhận đường kết nối nếu kiểm tra lại vẫn lỗi.

> Thêm [giới hạn số job, hàng đợi và trạng thái Telegram](HANG_DOI_JOB_VA_TELEGRAM_2026-11-01.md): **Cài đặt chung → Mochi Chat → Số job chạy cùng lúc** áp dụng chung cho Local/admin/partner/khách hàng. Job chờ chưa mua mail, chạy lần lượt khi có chỗ; bot báo Đang chờ/Đang xử lý/Đã hoàn thành cho người tạo. Định dạng Proxy.vn là `ip:port|key_xoay`, ví dụ nhập đổi theo nhà cung cấp ở cả hai tab proxy.

> Thêm [mua 1 mail thử mã và Proxy mua mail riêng](THU_MA_VA_PROXY_MUA_MAIL_2026-11-01.md): mục tiêu 10 lượt mua 1 mail trước, nhập mã thành công mới mua 9. Thử lỗi/giới hạn 24 giờ thì dừng. Tab Proxy mua mail có danh sách/TXT riêng; mỗi POST mua xong xoay IP nền, dùng proxy sẵn sàng khác và chỉ chờ khi tất cả đều bận/xoay.

> Thêm [Lịch sử job và CSV tự động](LICH_SU_JOB_2026-11-01.md): job kết thúc được lưu vào `data/job_history.csv`; xem tại Mochi Chat → Nhiệm vụ → Lịch sử job. Có người tạo/Chat ID, mã job/mã đơn, mã mời, kết quả và bean thực tế, tìm/lọc và mở CSV. Giữ lịch sử khi mở lại tool hoặc xóa job khỏi danh sách phiên, chống ghi trùng và ghi tiếp khi CSV đang bị khóa.

> Telegram có [Quản lý khách hàng và thanh toán SePay](KHACH_HANG_SEPAY_2026-11-01.md): khách tự ghi nhận khi nhắn bot, ID được cấp quyền admin/partner luôn nằm ngoài danh sách khách. Có thêm/khôi phục, xóa và sửa số dư kèm lịch sử. Khách dùng ví để đặt đơn có mã riêng; giữ tiền khi tạo, thu theo bean thực tế thành công và hoàn phần còn lại. Nội dung nạp là Chat ID, giới hạn 10.000–100.000 VND mỗi giao dịch; cấu hình SePay tại tab thanh toán để đồng bộ và chống cộng trùng.

> Thêm [mua mail từ cả hai shop theo thứ tự ưu tiên](HAI_SHOP_MAIL_2026-11-01.md): Cài đặt chung → Mochi Chat → **Cả hai shop**, nhập hai API key riêng và danh sách như `gmailvip:3445, 1trieumail:3423`. Hết hàng thì thử mục tiếp theo; đã mua thành công thì lưu lô và dừng mua. Cấu hình dùng chung cho job Local và Telegram.

> Thêm người dùng hệ thống [Local](NGUOI_DUNG_LOCAL_2026-11-01.md) trong bảng Telegram: tự gom job nhập từ máy tính, lưu số job/bean và doanh thu/chi phí/lợi nhuận kể cả khi bot tắt. Chọn Local → Xem job để mở lịch sử; job từ bot vẫn thuộc đúng người gửi.

> Thêm [proxy xoay muaproxyxoay.net](PROXY_MUAPROXYXOAY_2026-11-01.md): chọn nhà cung cấp tại Proxy Pool rồi nhập `ip:port:user:pass|key_xoay`, mỗi dòng một proxy. Giữ user/password sau đổi IP, xử lý cooldown theo API và dùng chung cơ chế cấp proxy cho job. Có [file TXT mẫu](examples/proxy_muaproxyxoay_mau.txt).

> Telegram có [doanh thu, chi phí mail và lợi nhuận theo người dùng](THONG_KE_TAI_CHINH_2026-11-01.md). Cấu hình giá bán chung theo VND / 100 bean; giá được chốt cho từng job. Doanh thu cộng khi job hoàn thành, chi phí ghi theo số dư shop trước/sau mua cho toàn bộ mail đã mua. Có sổ giao dịch và đối soát khi thiếu số tiền; không tính mail dùng lại hai lần.

> Proxy Pool có **Nhập TXT / Lưu TXT / Xuất TXT**, mỗi proxy một dòng. Hỗ trợ proxy thường, thông tin đăng nhập, HTTP/SOCKS và proxy xoay `endpoint|key_xoay`. Xem [hướng dẫn file proxy](PROXY_TXT_2026-11-01.md) và [file mẫu](examples/proxy_mau.txt).

> Giao diện mới theo mẫu: thanh điều hướng trái, Dashboard, Mochi Chat với các tab thiết lập/job/log, Telegram thay vị trí License và Cài đặt chung. Không có mục MiraiMind. Trang Proxy giữ nguyên. Chạy bằng **`Launch_HoshinoTool.bat`**; launcher cũ vẫn sử dụng được.

> Thêm logo, icon và ảnh nền tại **[`gui/assets/branding/`](gui/assets/branding/README.md)**, hoặc bấm **Mở thư mục hình ảnh** ở cuối thanh bên. Không cần sửa code hay chép ảnh sang `src/`. Xem [hướng dẫn giao diện và tài nguyên](GIAO_DIEN_HOSHINO_2026-11-01.md).

> Telegram có [quản lý tên, vai trò admin/partner và thống kê theo Chat ID](QUAN_LY_TELEGRAM_2026-11-01.md): tổng job hoàn thành, job thất bại và bean thực tế đã chạy. Dữ liệu lưu SQLite, không cộng trùng kết quả; thu hồi quyền vẫn giữ lịch sử. Mọi Chat ID được cấp quyền đều tạo job và xem số liệu của mình; admin có thêm quyền quản lý partner qua bot. Quản lý tài khoản admin thực hiện trong ứng dụng quản lý.

> Có [tab Telegram để thêm job theo Chat ID](TELEGRAM_2026-11-01.md): nhập Bot Token, thêm ID được phép và bật bot. Người dùng nhắn riêng `/id` để lấy ID; sau khi được cấp quyền có thể gửi `/job MÃ_MỜI SỐ_KẸO`, ví dụ `/job VBRN5H 1000`. Job dùng cấu hình hiện tại trong tool và bot báo kết quả về đúng người gửi.

> Cập nhật 01/11/2026: [số luồng tự tính theo proxy](DONG_THOI_PROXY_2026-11-01.md). Ví dụ 5 proxy × 1 luồng/proxy chạy tối đa 5 tài khoản đồng thời; 5 × 2 chạy 10. Ô tổng luồng tự hiển thị và không bị giới hạn bởi giá trị nhập tay cũ. Tài khoản chạy Direct vẫn dùng mức nhập tay.

> Cập nhật 31/10/2026: xem [báo cáo tối ưu và rà soát](RA_SOAT_2026-10-31.md) về mã nguồn hiện tại, các lỗi đã sửa và phạm vi kiểm chứng. Một số mô tả kiến trúc bên dưới là tài liệu lịch sử.

> Kết quả mới và sửa ngân sách sau job `mochi_a46f0cbb`: xem [kiểm tra proxy và thay đổi phục hồi](KIEM_TRA_PROXY_2026-10-31.md). Cùng endpoint/IP có lần HTTPS Mochi thành công và có lần timeout. Đăng nhập qua proxy có tối đa 4 POST để vẫn có lượt sau cấp lại proxy khi lỗi chuyển TLS → HTTP/2 → timeout; giữ cùng token. Sau đổi IP dùng profile HTTP/TLS gốc, bỏ HTTP/1.1 và DNS cũ. Kết quả đo chỉ phản ánh thời điểm thử.

> Với lỗi curl35 ở job `mochi_5fe09cc6`, xem [chẩn đoán HTTPS và cách chạy lại mail đã mua](CHAN_DOAN_HTTPS_2026-10-31.md). Giao diện có ô **Dùng lô mail đã mua** để chọn file JSON cũ và chạy lại mà không mua thêm mail. Proxy trả IP/CONNECT200 vẫn có thể không hoàn tất TLS; sửa nguồn không thay thế yêu cầu có đường HTTPS hoạt động.

> Luồng mua mail hiện tại: mua 1 mail thử mã, thành công mới mua phần còn thiếu (10 lượt → 1 + 9; đã có 3/10 → 1 + 6). Không tự mua bù sau lỗi tài khoản hoặc shop trả thiếu. Tất cả mail đã nhận được lưu tại `data/mail_batches/` kèm trạng thái. Proxy mua mail có tab/danh sách riêng, mỗi POST xong xoay IP nền và ưu tiên proxy sẵn sàng. Google, đăng nhập Mochi và nhập mã dùng các lựa chọn Proxy Pool riêng. Shop giới hạn tốc độ vẫn tuân thủ thời gian chờ và thử lại cùng sản phẩm/số lượng tối đa 3 lần; mất phản hồi giao dịch thì dừng đối soát.

Hệ thống tự động hóa đăng ký, đăng nhập và kích hoạt mã mời (Referral Code) cho ứng dụng **Mochi Chat**, tích hợp hệ thống quản lý **Proxy Xoay (Proxy.vn)** và xác thực **Google OAuth 2.0 Local**. Toàn bộ tác vụ chạy 100% cục bộ trên máy trạm người dùng, không phụ thuộc máy chủ trung gian.

---

## Mục Lục
1. [Tổng Quan Kiến Trúc](#1-tổng-quan-kiến-trúc)
2. [Cấu Trúc Thư Mục](#2-cấu-trúc-thư-mục)
3. [Chi Tiết Nội Dung & Chức Năng Từng File & Hàm](#3-chi-tiết-nội-dung--chức-năng-từng-file--hàm)
   - [3.1. File Khởi Chạy (Entrypoints)](#31-file-khởi-chạy-entrypoints)
   - [3.2. Lớp Giao Diện (GUI - PySide6)](#32-lớp-giao-diện-gui---pyside6)
   - [3.3. Lớp Xác Thực Google (Core Auth)](#33-lớp-xác-thực-google-core-auth)
   - [3.4. Lớp Quản Lý Browser (Core Browser)](#34-lớp-quản-lý-browser-core-browser)
   - [3.5. Lớp Quản Lý Proxy (Core Proxy)](#35-lớp-quản-lý-proxy-core-proxy)
   - [3.6. Lớp Nền Tảng Mochi Chat (Core Platforms Mochichat)](#36-lớp-nền-tảng-mochi-chat-core-platforms-mochichat)
   - [3.7. Lớp Mua Mail Tự Động (Core Mail Providers)](#37-lớp-mua-mail-tự-động-core-mail-providers)
   - [3.8. Cấu Hình & Sự Kiện (Core Config & Events)](#38-cấu-hình--sự-kiện-core-config--events)
4. [Các Cơ Chế Cốt Lõi Đặc Biệt](#4-các-cơ-chế-cốt-lõi-đặc-biệt)
   - [4.1. Cơ Chế Xác Thực Google Hai Tầng (Two-Tier Auth)](#41-cơ-chế-xác-thực-google-hai-tầng-two-tier-auth)
   - [4.2. Tự Động Hóa Vượt Màn Hình Choose An Account](#42-tự-động-hóa-vượt-màn-hình-choose-an-account)
   - [4.3. Quản Lý Vòng Đời Cooldown Proxy.vn](#43-quản-lý-vòng-đời-cooldown-proxyvn)
   - [4.4. Tối Ưu Băng Thông Proxy Khi Tải Google](#44-tối-ưu-băng-thông-proxy-khi-tải-google)
5. [Hướng Dẫn Cài Đặt & Sử Dụng](#5-hướng-dẫn-cài-đặt--sử-dụng)

---

## 1. Tổng Quan Kiến Trúc

```
+-----------------------------------------------------------------------------------+
|                              PySide6 GUI Interface                                |
|  [ MochiChatPage (Tabs, Stats, Log) ]   <--->   [ ProxyPage (Quản lý Proxy.vn) ]  |
+-----------------------------------------------------------------------------------+
                                        |
                                        v
+-----------------------------------------------------------------------------------+
|                        MochiJobLifecycleManager & Runners                         |
|   - Điều phối đa luồng (Worker Threads) độc lập                                  |
|   - Quản lý hủy phiên (Cancellation Events), cô lập Job ID                        |
+-----------------------------------------------------------------------------------+
       |                                |                                   |
       v                                v                                   v
+------------------+     +-------------------------------+     +--------------------+
| Mail Providers   |     |      Google Auth Engine       |     |   Proxy Broker     |
| - 1TrieuMail.com |     | Strategy 1: Browser OAuth 2.0 |     | - Proxy.vn API     |
| - GmailVip.vn    |     | Strategy 2: GLS Play Services |     | - Slot Concurrency |
+------------------+     +-------------------------------+     | - Cooldown Guard   |
                                        |                      +--------------------+
                                        v
                         +-------------------------------+
                         |     CentralBrowserManager     |
                         | - Chrome System Headless      |
                         | - Route Abort (Images, Fonts) |
                         +-------------------------------+
```

---

## 2. Cấu Trúc Thư Mục

Dự án duy trì hai cây thư mục đồng bộ: thư mục gốc và thư mục `src/` để đảm bảo tương thích tuyệt đối khi đóng gói hoặc chạy trực tiếp bằng Python:

```
TTMTool (1) - Copy/
|-- main_mochi_gui.py                 # File khởi chạy giao diện chính (PySide6)
|-- cli_mochi.py                      # File khởi chạy dòng lệnh (CLI Headless)
|-- gui/                              # Thư mục giao diện
|   |-- assets/                       # Vector SVGs, Logos, Icons chuẩn của TTM Tool
|   |-- pages/
|       |-- mochi_chat.py             # Tab Mochi Chat (nhập mã, cấu hình luồng, bảng tiến trình, log console)
|       |-- proxy.py                  # Tab Quản lý Proxy độc quyền (4 Stat Cards, bảng Hoạt động & Bảng Lỗi)
|-- core/ (và src/core/)              # Lớp lõi xử lý nghiệp vụ
|   |-- auth/
|   |   |-- google.py                 # Engine xác thực Google Account (OAuth 2.0 Web & GLS Master Token)
|   |-- browser/
|   |   |-- manager.py                # CentralBrowserManager quản lý Chromium Context & tối ưu mạng
|   |-- config/
|   |   |-- constants.py              # Hằng số toàn cục
|   |   |-- runtime_config.py         # Quản lý cấu hình QSettings
|   |-- job_events.py                 # Hệ thống bus phát sự kiện JobEventEmitter & Log Level
|   |-- mail/providers/
|   |   |-- one_trieu_mail.py         # Client API tích hợp shop 1TrieuMail.com
|   |   |-- gmailvip_vn.py            # Client API tích hợp shop GmailVip.vn
|   |-- platforms/mochichat/
|   |   |-- constants.py              # App Client ID, Package Name, SHA-1, API Base
|   |   |-- device.py                 # Sinh vân tay thiết bị Android ngẫu nhiên (DeviceInfo)
|   |   |-- fetcher.py                # MochiChatFetcher: Đăng nhập Mochi backend & Nhập mã mời
|   |   |-- job_lifecycle.py          # Quản lý vòng đời độc lập của các phiên Job
|   |   |-- runners/
|   |       |-- invite_referral.py    # Pipeline chạy tự động 1 tài khoản từ A - Z
|   |-- proxy/
|       |-- broker.py                 # ProxyBroker: Điều phối, cấp phát slot & xoay proxy
|       |-- rotators.py               # Provider chuyên biệt cho API xoay Proxy.vn
|       |-- models.py                 # Mô hình dữ liệu ProxyModel, ProxyStatus
|       |-- protocol.py               # Chuẩn giao thức kết nối proxy
|       |-- client.py                 # Client tương tác nội bộ với Broker
```

---

## 3. Chi Tiết Nội Dung & Chức Năng Từng File & Hàm

### 3.1. File Khởi Chạy (Entrypoints)

#### `main_mochi_gui.py`
Điểm khởi đầu chính của ứng dụng giao diện đồ họa.
- **`MochiMainWindow(QMainWindow)`**: Cửa sổ chính của tool.
  - `__init__()`: Thiết lập tiêu đề cửa sổ, kích thước tối thiểu (1020x720), nạp stylesheet tối chuẩn (`#080c14`), icon `.ico`, tạo thanh điều hướng header bar và `QStackedWidget` quản lý các trang.
  - `_create_header_bar() -> QWidget`: Dựng thanh topbar chứa logo, tên ứng dụng `TTM TOOL - LOCAL`, và hai nút chuyển tab chính (`Mochi Chat`, `Quản lý Proxy`).
  - `switch_to_mochi_tab()`: Chuyển sang màn hình cấu hình và chạy job Mochi Chat (Index 0).
  - `switch_to_proxy_tab()`: Chuyển sang màn hình quản lý danh sách Proxy (Index 1).
  - `_on_proxies_updated()`: Bắt tín hiệu khi danh sách proxy thay đổi từ `ProxyPage` để cập nhật số lượng proxy sẵn sàng trên tab Mochi Chat.

#### `cli_mochi.py`
Khởi chạy dòng lệnh không qua giao diện, phục vụ chạy ngầm trên VPS hoặc debug trực tiếp.
- `main()`: Parse tham số dòng lệnh (`--invite-code`, `--target`, `--concurrency`, `--key-proxy`), khởi tạo runner và lắng nghe log console.

---

### 3.2. Lớp Giao Diện (GUI - PySide6)

#### `src/gui/pages/proxy.py` (và `gui/pages/proxy.py`)
Trang quản lý danh sách Proxy.vn chuyên dụng định dạng `ip:port|key xoay`.
- **`_ProxyRotateThread(QThread)`**: Luồng chạy nền thực hiện xoay proxy bằng `ProxyVnProvider.rotate_and_wait()`, phát tín hiệu `progress_signal` đếm ngược thời gian chờ và `finished_signal` khi xoay xong để không làm đơ giao diện.
- **`_get_asset_icon(name: str, size: int) -> QIcon`**: Hàm tiện ích nạp các vector `.svg` từ thư mục `assets/` thông qua `QSvgRenderer`, trả về `QIcon` chuẩn sắc nét không vỡ hạt.
- **`_SparklineWaveWidget(QWidget)`**: Widget vẽ biểu đồ đường cong lượn sóng dạ quang ở góc các thẻ thống kê.
- **`_StatCard(QFrame)`**: Thẻ thống kê số lượng (Hoạt động, Đang dùng, Đang nghỉ, Đã lỗi) với tiêu đề, số lớn và đường cong màu.
- **`_ProxyImportDialog(QDialog)`**: Hộp thoại mở lên khi bấm "Import", cho phép dán danh sách hàng loạt `ip:port|key xoay` kèm tùy chọn tự động check sau khi thêm.
- **`ProxyPage(QWidget)`**: Widget trang quản lý proxy.
  - `_init_ui()`: Dựng 4 thẻ thống kê, thanh tìm kiếm (có icon kính lúp), Section 1 (Bảng trạng thái proxy), Section 2 (Bảng proxy lỗi), thanh phân trang.
  - `_parse_proxy_line(line: str) -> dict`: Tách chuỗi `ip:port|key xoay` thành cấu trúc dữ liệu proxy chuẩn.
  - `_save_to_settings()`: Lưu trữ danh sách proxy vào `QSettings` dưới dạng chuỗi JSON.
  - `_load_from_settings()`: Nạp proxy từ `QSettings`, đồng thời tự động khôi phục các proxy đang chờ xoay IP không bị xếp nhầm vào bảng lỗi.
  - `_update_metrics_and_render()`: Đếm số lượng theo 4 trạng thái và cập nhật lại cả hai bảng.
  - `_render_active_table(items)`: Vẽ bảng proxy đang hoạt động (cột Proxy, Badge trạng thái `SUCCESS`/`COOLING`/`RUNNING`, Loại provider `Xoay - proxy.vn`, Tốc độ/thời gian chờ, 3 nút thao tác xoay, ping, xóa).
  - `_render_failed_table(items)`: Vẽ bảng proxy lỗi (cột Proxy, Lý do lỗi màu đỏ, Thời gian lỗi, 3 nút thao tác thử lại, check, xóa).
  - `_rotate_single_proxy(pid)`: Bắt đầu xoay IP cho 1 dòng proxy cụ thể.
  - `_check_single_proxy(pid)`: Kiểm tra tốc độ mạng và ping của 1 proxy.
  - `_delete_single_proxy(pid)`: Xóa 1 proxy khỏi danh sách.
  - `_check_all_active_proxies()` / `_check_all_failed_proxies()`: Kiểm tra hàng loạt proxy.
  - `get_active_proxies() -> List[Tuple[str, str]]`: Trả về danh sách proxy hoạt động dạng `[(endpoint, key_xoay), ...]` cho các job sử dụng.

#### `src/gui/pages/mochi_chat.py` (và `gui/pages/mochi_chat.py`)
Trang điều khiển chính của Mochi Chat.
- **`MochiWorkerThread(QThread)`**: Luồng thực thi ngầm của 1 Job Mochi Chat:
  - `run()`: Khởi tạo `MochiInviteRunner`, chạy hàm bất đồng bộ `runner.run()`, bắt tín hiệu log và kết quả trả về giao diện.
  - `stop()`: Phát cờ hủy phiên độc lập cho Job.
- **`MochiChatPage(QWidget)`**: Giao diện chính điều khiển Mochi Chat:
  - `_init_ui()`: Thiết lập layout 2 cột: Cột trái (Cấu hình mã mời, số lượng mục tiêu, shop mail, số luồng, cài đặt proxy xoay); Cột phải (Thống kê tiến độ, nút Bắt đầu / Tạm dừng, bảng danh sách tài khoản, khung log trực tiếp).
  - `_start_job()`: Thu thập dữ liệu cấu hình, kiểm tra tính hợp lệ, cấp phát `job_id` UUID độc lập và kích hoạt `MochiWorkerThread`.
  - `_stop_job()`: Dừng an toàn job đang chạy mà không làm ảnh hưởng đến việc tạo job mới sau này.
  - `_on_log_message()`: Append dòng log định dạng thời gian thực `[HH:MM:SS] Nội dung` kèm màu sắc tương ứng với log level.

---

### 3.3. Lớp Xác Thực Google (Core Auth)

#### `src/core/auth/google.py` (và `core/auth/google.py`)
Engine xác thực tài khoản Google hoàn toàn ở máy cục bộ.
- **`resolve_google_login_via_proxy(settings) -> bool`**: Kiểm tra cấu hình xem có cho phép mở browser đăng nhập Google qua proxy hay không.
- **`_parse_google_kv_response(text: str) -> dict`**: Parse văn bản phản hồi định dạng Key=Value của máy chủ Google Android Clients.
- **`_post_google_auth(headers, payload, timeout, proxy, session)`**: Gửi request POST tới `https://android.clients.google.com/auth`.
- **`_extract_id_token_via_browser(...) -> Optional[str]` (Chiến lược 1)**:
  - Mở Chromium qua `CentralBrowserManager`.
  - Điều hướng tới endpoint OAuth 2.0 Web: `https://accounts.google.com/o/oauth2/v2/auth?client_id=...&response_type=id_token&scope=openid%20profile%20email&redirect_uri=https://mochi.lightchaser.xyz&nonce=...`.
  - Nhập email vào `input[name="identifier"]`.
  - Nhập mật khẩu vào `input[name="Passwd"]`.
  - **`_LOGIN_STATE_SCRIPT`**: Vòng lặp đơn kiểm tra trạng thái DOM thông minh theo chu kỳ:
    - Nhập email vào `input[name="identifier"]`.
    - Nhập mật khẩu vào `input[name="Passwd"]`.
    - Tự động nhận diện và vượt màn hình *"Choose an account to continue to lightchaser.xyz"* (click vào dòng tài khoản tương ứng).
    - Tự động nhận diện và bấm nút ủy quyền *"Continue"* / *"Tiếp tục"* trên màn hình *"Sign in to lightchaser.xyz"* (loại trừ nút Cancel, xử lý `aria-label` chứa tên ứng dụng).
    - Cơ chế click kép: Playwright click + JS direct event dispatch fallback đảm bảo kích hoạt thành công ngay lập tức.
  - Lắng nghe sự kiện điều hướng `framenavigated` và trích xuất `id_token` dạng JWT (`eyJ...`) từ hash URL `#id_token=...`.
- **`_extract_oauth_token(...) -> Optional[str]` (Chiến lược 2 - Fallback)**:
  - Mở trang Google Embedded Setup dành cho Android (`https://accounts.google.com/embedded/setup/android`).
  - Điền email, password và trích xuất cookie `oauth_token`.
- **`_exchange_oauth_to_master_token(...) -> str`**: Đổi `oauth_token` lấy Master Token Android (`aas_et/...`).
- **`_exchange_master_to_id_token(...) -> str`**: Sử dụng Master Token gọi Play Services Auth API qua 5 cấu hình audience khác nhau để xin cấp ID Token cho Mochi Chat.
- **`get_google_id_token_for_app(...) -> Optional[str]`**: Hàm API công khai duy nhất, tự động ưu tiên chạy Chiến lược 1 (Browser OAuth Web trực tiếp không bị chặn SHA-1 Android Console), nếu thất bại tự động chuyển sang Chiến lược 2.

---

### 3.4. Lớp Quản Lý Browser (Core Browser)

#### `src/core/browser/manager.py` (và `core/browser/manager.py`)
- **`_find_system_chrome() -> Optional[str]`**: Quét các đường dẫn cài đặt chuẩn của Google Chrome trên Windows (`C:\Program Files\Google\Chrome\Application\chrome.exe`).
- **`CentralBrowserManager`**: Quản lý tập trung vòng đời của Chromium:
  - `_init_browser()`: Khởi chạy trình duyệt với các cờ tối ưu tốc độ cực đại:
    - `--disable-gpu`, `--disable-extensions`, `--disable-default-apps`.
    - `--disable-background-networking`, `--disable-sync`.
    - `--blink-settings=imagesEnabled=false` (tắt render ảnh).
  - `acquire_context(owner_id, proxy, headless) -> BrowserContext`:
    - Tạo một Browser Context hoàn toàn cô lập theo từng phiên job.
    - Cài đặt proxy (nếu có cấu hình).
    - **Route Interception tối ưu**: Đăng ký `ctx.route("**/*", ...)` ngắt ngay lập tức mọi request tải hình ảnh (`.png`, `.jpg`, `.webp`), fonts (`.woff2`, `.ttf`), media và telemetry Google (`play.google.com/log`, `gen_204`, `google-analytics.com`), giảm tải lưu lượng qua proxy từ 4.5MB xuống 300KB.
  - `release_context(context)`: Đóng context và giải phóng bộ nhớ RAM.
  - `close_all()`: Dừng hoàn toàn tiến trình trình duyệt khi tắt tool.

---

### 3.5. Lớp Quản Lý Proxy (Core Proxy)

#### `src/core/proxy/rotators.py` (và `core/proxy/rotators.py`)
Engine tương tác với hệ thống API của **Proxy.vn**.
- **`RotationResult`**: Dataclass chứa thông tin: `success: bool`, `endpoint: str`, `wait_seconds: float`, `error: str`, `is_cooling: bool`.
- **`is_cooldown_or_wait_message(msg: str) -> bool`**: Kiểm tra xem thông báo trả về từ API có phải là trạng thái chờ xoay hay không (chứa `cho`, `die sau`, `waiting`).
- **`is_real_proxy_error(err: str) -> bool`**: Phân biệt lỗi chết thật (như `key khong ton tai`, sai cú pháp) với trạng thái cooldown bình thường.
- **`ProxyVnProvider`**:
  - `get_current_ip() -> Tuple[bool, str]`: Lấy endpoint IP:Port hiện tại đang mở của key.
  - `rotate_ip() -> RotationResult`: Gửi yêu cầu đổi IP mới qua endpoint `https://proxy.vn/api/change-ip`.
  - `get_current_or_rotate(...) -> RotationResult`: Lấy IP sẵn sàng; nếu đang cooldown thì trả về `is_cooling=True` kèm số giây chờ, không đánh dấu là lỗi.
  - `rotate_and_wait(timeout, on_progress) -> RotationResult`: Vòng lặp đếm ngược và tự động lấy IP ngay khi hết thời gian cooldown.

#### `src/core/proxy/broker.py` (và `core/proxy/broker.py`)
Bộ điều phối phân bổ proxy cho các luồng job.
- **`ProxyBroker`**:
  - `set_proxy_list(proxies)`: Nạp danh sách proxy hoạt động.
  - `lease(job_id, timeout) -> Optional[ProxyModel]`: Cấp phát một proxy sẵn sàng cho luồng công việc. Nếu proxy đang trong thời gian chờ xoay, hàm sẽ tự động đợi cho đến khi xoay thành công trước khi bàn giao cho luồng.
  - `release(proxy, mark_cooling)`: Thu hồi proxy sau khi luồng hoàn thành, đánh dấu cooldown nếu cần.

#### `src/core/proxy/models.py`
Mô hình đối tượng proxy chuẩn trong hệ thống (`ProxyModel`, `ProxyStatus`).

---

### 3.6. Lớp Nền Tảng Mochi Chat (Core Platforms Mochichat)

#### `src/core/platforms/mochichat/fetcher.py` (và `core/platforms/mochichat/fetcher.py`)
Tương tác trực tiếp với API backend của Mochi Chat Android.
- **`MochiChatFetcher`**:
  - `_get_mochi_headers(access_token) -> dict`: Sinh bộ Headers chuẩn của app Mochi Chat Android (React Native/OkHttp/4.9.2), bao gồm `X-Device-Id`, `X-App-Version`, `X-Platform: android`.
  - `login(owner_id, email, password, proxy, on_step, headless) -> dict`:
    - Bước 1: Gọi `get_google_id_token_for_app` để lấy Google ID Token.
    - Bước 2: Gửi ID Token lên endpoint `https://api.mochichat.com/auth/google` để nhận JWT Bearer Token của Mochi Chat.
  - `redeem_invite_code(invite_code: str) -> dict`: Gửi mã mời lên backend Mochi Chat để nhận phần thưởng giới thiệu.
  - `close()`: Đóng phiên HTTP `AsyncSession`.

#### `src/core/platforms/mochichat/device.py`
- **`DeviceInfo`**: Tự động sinh ngẫu nhiên định danh thiết bị Android giả lập thực tế: `android_id` (16 ký tự hex), `device_id` (UUIDv4), model máy (`Pixel 7`, `Galaxy S23`, `Xiaomi 13`), phiên bản OS (Android 11 - 14).

#### `src/core/platforms/mochichat/job_lifecycle.py`
- **`MochiJobLifecycleManager`**: Quản lý trạng thái cô lập giữa các phiên job:
  - `create_job() -> str`: Sinh mã `job_id` UUID riêng biệt cho phiên mới.
  - `register_worker(job_id, worker)`: Gán thread worker vào danh bạ phiên.
  - `cancel_job(job_id)`: Bật cờ `asyncio.Event` để dừng nhẹ nhàng toàn bộ các luồng con thuộc job_id này mà không làm ảnh hưởng đến các job khác.
  - `cleanup_job(job_id)`: Dọn dẹp tài nguyên và giải phóng browser context.

#### `src/core/platforms/mochichat/runners/invite_referral.py`
- **`MochiInviteRunner`**: Trình điều phối chạy referral trọn gói:
  - `run() -> MochiJobResult`: Vòng lặp chính xử lý số lượng mục tiêu:
    1. Kiểm tra cờ dừng job (`cancel_event`).
    2. Gọi Mail Provider mua tài khoản Gmail mới.
    3. Cấp phát Proxy qua `ProxyBroker`.
    4. Khởi tạo `MochiChatFetcher`.
    5. Đăng nhập Google, xác nhận điều khoản, chọn tài khoản để lấy ID Token.
    6. Đăng nhập Mochi Chat backend và đổi mã mời `redeem_invite_code`.
    7. Cập nhật tiến độ thành công/thất bại lên UI và ghi nhận vào bảng kết quả.

---

### 3.7. Lớp Mua Mail Tự Động (Core Mail Providers)

#### `src/core/mail/providers/one_trieu_mail.py`
- **`OneTrieuMailProvider`**:
  - `get_balance() -> float`: Kiểm tra số dư tài khoản tại `1TrieuMail.com`.
  - `buy_account(product_id) -> Tuple[bool, str, str, str]`: Gọi API mua 1 tài khoản Gmail mới, parse trả về `(success, email, password, error_message)`.

#### `src/core/mail/providers/gmailvip_vn.py`
- **`GmailVipProvider`**: Tương tự cho shop `GmailVip.vn`.

---

### 3.8. Cấu Hình & Sự Kiện (Core Config & Events)

#### `src/core/job_events.py`
- **`JobLogLevel`**: Enum định nghĩa cấp độ log: `INFO`, `SUCCESS`, `WARNING`, `ERROR`, `DEBUG`.
- **`JobEventEmitter`**: Đối tượng Singleton phát các sự kiện log và tiến độ cho giao diện lắng nghe theo mô hình Observer.

#### `src/core/config/runtime_config.py`
- **`RuntimeConfig`**: Quản lý nạp/lưu các thiết lập của ứng dụng vào Windows Registry qua `QSettings` (`Hermit Coder / TTM Tool`).

---

## 4. Các Cơ Chế Cốt Lõi Đặc Biệt

### 4.1. Cơ Chế Xác Thực Google Hai Tầng (Two-Tier Auth)
Khi đăng nhập tài khoản Google từ máy tính cá nhân để lấy token cho ứng dụng Android, Google thường trả về lỗi `UNREGISTERED_ON_API_CONSOLE` nếu gọi endpoint Google Play Services giả lập do chưa khớp SHA-1 Certificate Fingerprint.
- **Giải pháp**: TTM Tool ưu tiên **Chiến lược 1 (Browser OAuth 2.0 Web)** mở trực tiếp endpoint cấp token của Google với Web Client ID của Mochi Chat (`redirect_uri="https://mochi.lightchaser.xyz"`). Luồng này được Google đối xử như một lượt đăng nhập Web hợp lệ, bỏ qua hoàn toàn cơ chế kiểm tra chữ ký ứng dụng Android native.
- **Fallback**: Nếu có lỗi phát sinh ở trình duyệt, tool mới tự động chuyển sang Chiến lược 2 (GLS Android Master Token exchange).

### 4.2. Tự Động Hóa Vượt Màn Hình Choose An Account
Khi điều hướng OAuth 2.0 sau bước submit mật khẩu, Google thường hiển thị màn hình trung gian: *"Choose an account to continue to lightchaser.xyz"* liệt kê tài khoản vừa đăng nhập kèm nút *"Use another account"*.
- Hàm `try_click_account_chooser()` trong `google.py` sử dụng thuật toán nhận diện 3 tầng:
  1. Quét theo attribute `[data-identifier="{email}"]` và `[data-email="{email}"]`.
  2. Quét sâu các thẻ text chứa chính xác chuỗi email đăng nhập và tìm phần tử có thể click gần nhất (`role="link"`, `role="button"`, `li`, `[tabindex="0"]`).
  3. Bắn đồng thời chuỗi sự kiện chuột thực `mousedown` -> `mouseup` -> `click` để kích hoạt điều hướng cấp mã `id_token` ngay lập tức.

### 4.3. Quản Lý Vòng Đời Cooldown Proxy.vn
Proxy xoay tại Việt Nam luôn có khoảng thời gian chết giữa các lần đổi IP (từ 15s đến 60s).
- Khi nhận mã phản hồi 102 hoặc thông báo `cho ...s` / `die sau ...s`, hệ thống **không bao giờ** xếp proxy vào danh sách lỗi.
- Trạng thái được chuyển thành `COOLING`. Luồng `_ProxyRotateThread` chạy nền đếm ngược thời gian và tự động xoay lại mà không phong tỏa giao diện người dùng.
- Chỉ các lỗi xác thực chết thật sự như `key khong ton tai` mới được chuyển sang bảng **Proxy lỗi**.

### 4.4. Tối Ưu Băng Thông Proxy Khi Tải Google
Trang đăng nhập Google thông thường tải về 4.5MB hình ảnh và script phân tích. Khi đi qua proxy xoay di động (4G/Residential), độ trễ có thể lên tới 15 - 20 giây một lượt.
- `CentralBrowserManager` kích hoạt `ctx.route("**/*")` chặn toàn bộ hình ảnh, web fonts, video và các script Google Analytics/telemetry.
- Thay đổi chế độ điều hướng thành `wait_until="commit"`, cho phép tương tác điền thông tin ngay khi khung HTML được truyền về máy, rút ngắn thời gian xử lý xuống còn **dưới 1 giây**.

---

## 5. Hướng Dẫn Cài Đặt & Sử Dụng

### Yêu Cầu Môi Trường
- Hệ điều hành: Windows 10/11 64-bit.
- Python: Phiên bản 3.11 đến 3.14 (Khuyến nghị Python 3.14).
- Google Chrome: Bản cập nhật mới nhất đã cài đặt tại máy (`C:\Program Files\Google\Chrome\Application\chrome.exe`).

### Khởi Chạy Ứng Dụng
Mở PowerShell tại thư mục dự án và chạy:
```powershell
py -3.14 -I main_mochi_gui.py
```

### Các Bước Vận Hành
1. **Thiết lập Proxy**: Chuyển sang tab **Quản lý Proxy** -> Bấm **Import** -> Dán danh sách theo định dạng `ip:port|key xoay` (mỗi dòng một proxy) -> Bấm **Thêm vào danh sách**.
2. **Cấu hình Mochi Chat**:
   - Nhập **Mã giới thiệu (Invite Code)** cần chạy.
   - Nhập số lượng tài khoản mục tiêu.
   - Chọn shop mua Gmail (`1TrieuMail.com` hoặc `GmailVip.vn`) và dán API Key của shop.
   - Tích chọn **Sử dụng Proxy Xoay (Proxy.vn)** (chọn chế độ lấy từ Tab Quản lý Proxy).
   - Tùy chọn: Tích chọn **Chạy ẩn (Headless)** để ẩn cửa sổ Chrome khi tự động hóa.
3. **Bắt đầu**: Bấm nút **Bắt đầu nhập mã**. Theo dõi tiến độ thời gian thực trên bảng danh sách tài khoản và màn hình console nhật ký.

