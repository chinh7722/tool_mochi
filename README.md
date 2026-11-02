# HOSHINO-TOOL — ĐIỀU KHIỂN MIRAIMIND LOCAL & TELEGRAM

**Ubuntu VPS và GitHub (02/11/2026):** đã bổ sung dịch vụ bot/API/hàng chờ không cần desktop tại `vps/` và giao diện quản lý từ xa tại `control/remote_gui.py`. Xem [GITHUB_UBUNTU.md](GITHUB_UBUNTU.md) để cài đặt và biết giới hạn. **Bộ thực thi job thật trên Linux chưa hoàn thành**: bản Windows thực thi trong EXE, còn `clone/core` chứa giao thức tái dựng chưa tương đương. Dịch vụ giữ hàng chờ dừng và từ chối chạy bằng adapter chưa sẵn sàng; không tạo kết quả giả. `2_MO_QUAN_LY_VPS.bat` mở giao diện quản lý VPS; launcher bản local vẫn dùng như trước.

**Jobs cập nhật 02/11/2026:** Trang **Job & hàng chờ** có bốn mục **Thiết lập job**, **Job đang chạy**, **Log job**, **Lịch sử job**, theo bố cục ảnh mẫu. Giữ nhập nhiều mã và đơn vị konpeito; không có cấu hình mail/proxy.

* **Thiết lập job:** tên khách/ghi chú, mã mời (mỗi dòng một mã), konpeito và tổng tiền cho từng mã. **Lưu nháp** giữ form qua lần mở lại. **Hẹn giờ chạy** dùng giờ Việt Nam, được lưu cùng hàng chờ. Job hẹn giờ chỉ nhận lượt khi đến hạn; các mã đã sẵn sàng vẫn chạy theo thứ tự. Tool cần mở và hàng chờ không tạm dừng để thực thi.
* **Job đang chạy:** danh sách phiên, người tạo, mã mời, tiến độ account, konpeito thực tế/mục tiêu, kết quả, thời gian và nút **Xóa / Log**. Xóa mã chưa chạy sẽ hủy mã đó; xóa job đã kết thúc chỉ ẩn khỏi phiên. **Tạm dừng hàng chờ** ngừng chạy mã kế tiếp, job đang xử lý vẫn tiếp tục.
* **Log job:** xem tất cả hoặc chọn một job; có log từ Controller và log backend của đúng ID job. Log lưu vào SQLite, có thể mở lại từ lịch sử. **Xóa màn hình** chỉ dọn khung hiển thị. Khung xem lấy tối đa 2.000 dòng gần nhất; dữ liệu gốc vẫn được lưu. Dòng log được ghi từ bản cập nhật này trở đi.
* **Lịch sử job:** tự ghi kết quả cuối, thời điểm kết thúc theo giờ Việt Nam, người tạo, Chat ID, khách hàng, mã mời, số account, konpeito thực tế, ID job/mã đơn. Có tìm kiếm không phân biệt hoa thường tiếng Việt, lọc kết quả và làm mới. Nhấp đúp một dòng hoặc bấm **Log** để xem nhật ký đúng job. Màn hình lấy tối đa 500 kết quả phù hợp; CSV chứa toàn bộ lịch sử.

Lịch sử giữ tại `control/data/telegram.sqlite3`; file `control/data/job_history.csv` tự cập nhật khi job kết thúc. **Mở CSV / Mở thư mục** dùng để xem và đối soát. Nếu CSV đang bị khóa bởi Excel, lịch sử trong tool vẫn được lưu; đóng CSV rồi bấm **Làm mới**. Dữ liệu cũ được nâng cấp thêm trường, giữ nguyên job, Token và giá đã chốt.

**Giao diện cập nhật 02/11/2026:** bố cục sidebar theo mẫu gồm **Dashboard**, **Job & hàng chờ**, **Telegram**, **Cài đặt chung**. Không có trang quản lý proxy hay chỉ số proxy. Đơn vị hiển thị và thông báo bot thống nhất dùng **konpeito**; giá bán vẫn theo VND / 100 konpeito, số liệu và cách tính được giữ nguyên.

Dashboard dùng dữ liệu thật được Controller ghi nhận trong SQLite: tổng job, đang chạy, hoàn thành, thất bại (không tính job đã hủy), konpeito thực tế và số mã chờ. Có bảng 10 job mới nhất, nút **Tạo job mới** và các nút mở nhanh Telegram/cài đặt. **Cài đặt chung** lưu konpeito/tổng tiền/tên mặc định cho các mã thêm sau; không sửa thông số job đã vào hàng chờ.

Sau khi mở `1_MO_GIAO_DIEN.bat`, chọn **Job & hàng chờ** để nhập mã, hoặc chọn **Telegram** để quản lý bot. Cấu hình, Token và lịch sử từ bản trước vẫn dùng chung tại `control/data/telegram.sqlite3`.

Hệ thống mã nguồn Python hoàn chỉnh dùng để **thao tác trực tiếp trên giao diện đồ họa (GUI)** của ứng dụng bản local (`Miraimind tool - by Hermit Coder` / `TTM-Tool`), tự động hóa quy trình nhập job, bơm mã mời, điều khiển nút bấm và giám sát tiến độ thực thi theo thời gian thực.

**Cập nhật 01/11/2026 — thao tác nền không chiếm chuột:** Controller điền form qua Accessibility, chọn sidebar/tab bằng thông điệp gửi riêng đến cửa sổ Qt và kích hoạt nút chạy bằng UI Automation. Không di chuyển con trỏ, không gửi phím thật. Trong lúc nhập job, cờ `WS_EX_NOACTIVATE` được bật tạm thời trên cửa sổ local để tránh giành quyền nhập, rồi khôi phục khi hoàn tất hoặc có lỗi. Giữ app local mở, không thu nhỏ; cửa sổ có thể nằm sau ứng dụng khác. Nếu không thao tác nền được, Controller dừng và báo lỗi trong nhật ký, không tự chuyển sang dùng chuột hoặc tự tạo lại job qua API.

**Cập nhật nhập nhiều mã:** Dán nhiều mã (mỗi dòng một mã, hoặc ngăn cách bằng khoảng trắng/dấu phẩy/chấm phẩy), rồi bấm **Thêm mã vào hàng chờ**. Controller chạy từng mã theo thứ tự thêm; các mã thêm trong lúc chạy nằm cuối hàng chờ. Điểm mục tiêu/tổng tiền áp dụng cho **từng mã**, lưu theo thiết lập lúc thêm. Mã trùng với mã đang chạy/đang chờ được bỏ qua. Có thể tạm dừng sau job hiện tại, tiếp tục và xóa mã đang chờ trong tab **Hàng chờ nhập mã**. Từ bản tích hợp Telegram, hàng chờ và lịch sử được lưu xuống đĩa. Khi mở lại, hàng chờ được khôi phục ở trạng thái tạm dừng; bấm **Tiếp tục** để chạy tiếp. Job đã biết ID chỉ được theo dõi lại, không gửi lại; job gián đoạn khi chưa xác định ID cần kiểm tra bản local trước khi tạo lại. Việc đóng Controller không hủy job đã gửi sang bản local.

**Tab Telegram:** Có cấu hình Bot Token, tự bật bot, đơn giá VND/100 konpeito, thống kê, quản lý người dùng và giao dịch/đối soát. Không có phần chi phí mail. Job Telegram chạy qua GUI local không chiếm chuột và vào chung hàng chờ với mã nhập trên máy.

### Kết nối và dùng Telegram

1. Mở lại `1_MO_GIAO_DIEN.bat`, chọn tab **Telegram**.
2. Dán Bot Token lấy từ **@BotFather**, chọn tự bật bot nếu muốn, bấm **Lưu cấu hình** rồi **Bật bot**.
3. Nhắn riêng `/id` cho bot để lấy Chat ID. Nhập Chat ID, tên và vai trò trong tab Telegram rồi bấm **Thêm người dùng**. Vai trò admin/partner chỉ để phân loại; cả hai chỉ xem thống kê của chính mình qua bot.
4. Lệnh `/job npxMgy 2000 Khách A` thêm một job. Điểm phải là bội số dương của 200, tối đa 1.000.000.000. Có thể gửi nhiều dòng `/job` trong một tin nhắn. Mã thêm sau vào cuối hàng chờ. Bot phản hồi khi nhận, bắt đầu và kết thúc/lỗi.
5. `/stats` xem số job hoàn thành/thất bại, konpeito đã bán và doanh thu của người nhắn. `/start` hoặc `/help` xem hướng dẫn. Bot chỉ nhận job từ chat riêng đã được cấp quyền; `/id` dùng được trước khi cấp quyền.
6. **Thu hồi quyền** chặn lệnh mới và hủy mã chưa chạy của người đó. Job đã gửi sang bản local vẫn tiếp tục được theo dõi. **Khôi phục quyền** cấp lại quyền nhận lệnh mới.

Đơn giá được chốt khi nhận job Telegram; thay đơn giá không sửa job cũ. Doanh thu chỉ ghi nhận khi job hoàn thành, theo số konpeito thực giao và giá đã chốt. Job lỗi/hủy có thể có điểm đã chạy nhưng không được tính doanh thu. Job nhập trên máy nằm ở dòng **LOCAL**, dùng tổng tiền đã nhập trong form. Thống kê tính những job Controller ghi nhận từ khi có tính năng này; không tự nhập lịch sử cũ của backend.

Bot sử dụng long polling theo [Telegram Bot API](https://core.telegram.org/bots/api#getupdates). Lệnh gửi lúc bot tắt được bỏ qua khi bật lại. Không bật cùng bot trong hai ứng dụng cùng lúc. Nếu bot đang dùng webhook ở nơi khác, Controller báo trạng thái để bạn tắt kết nối đó trước, không tự xóa webhook. Tắt bot chỉ ngừng nhận lệnh; job đã nhận vẫn ở hàng chờ. Khi tắt bot, thông báo chưa gửi được lưu lại để gửi khi bật đúng bot trở lại.

Cấu hình, người dùng, hàng chờ, lịch sử và thông báo nằm trong `control/data/telegram.sqlite3`. Token được lưu bằng Windows DPAPI theo tài khoản Windows hiện tại, không in vào nhật ký. Khi chuyển sang máy/tài khoản Windows khác, nhập lại token trong tab Telegram.

---

## 📁 1. CẤU TRÚC THƯ MỤC VÀ TỆP TIN

```text
MiraimindTool (16) - Copy/
├── 1_MO_GIAO_DIEN.bat              # Launcher khởi chạy bảng điều khiển GUI chính (Khuyên dùng)
├── DIEU_KHIEN_BAN_LOCAL.cmd         # Launcher phụ điều khiển bản local
├── CHAY_GIAO_DIEN.cmd               # Launcher dự phòng cho chế độ đồ họa
├── README.md                        # Tài liệu đặc tả kiến trúc & toàn bộ hàm trong hệ thống
├── GIAO_CA.md                       # Biên bản giao ca & toàn bộ tiến trình phát triển
│
├── control/                         # [MODULE CHÍNH] Full Source điều khiển & tự động hóa
│   ├── __init__.py                  # Định danh package control
│   ├── qt.conf                      # Cấu hình DPI awareness cố định cho Qt6
│   ├── main.py                      # Điểm vào thực thi đa năng (Entrypoint GUI & CLI)
│   ├── gui.py                       # Giao diện bàn điều khiển Desktop (PyQt6 Đa luồng)
│   ├── desktop_pages.py             # Sidebar, Dashboard và Cài đặt chung
│   ├── jobs_page.py                 # Bốn mục Jobs: form, danh sách phiên, log và lịch sử
│   ├── job_history.py               # Nhãn kết quả, giờ Việt Nam và xuất CSV
│   ├── assets/                      # Logo và biểu tượng điều hướng cục bộ
│   ├── job_queue.py                 # Danh sách mã FIFO và thiết lập riêng của từng mục
│   ├── job_worker.py                # Gửi từng job và theo dõi chính xác theo backend ID
│   ├── telegram_page.py             # Tab Telegram, quản lý quyền, thống kê và đối soát
│   ├── telegram_bot.py              # Bot API, long polling và gửi thông báo nền
│   ├── telegram_store.py            # SQLite lưu cấu hình, người dùng, job và thông báo
│   ├── data/telegram.sqlite3        # Dữ liệu Controller (tự tạo khi mở giao diện)
│   ├── ui_automator.py              # Engine tự động hóa UI qua Win32 API & Accessibility
│   ├── api_bridge.py                # Cầu nối HTTP REST API với backend local (Port 18888)
│   ├── tracker.py                   # Bộ giám sát tiến độ thời gian thực & Event Stream
│   └── run.cmd                      # Launcher nội bộ trong thư mục control
│
├── clone/                           # Bản mã nguồn giải mã tái cấu trúc từ bản chính
└── MiraimindLocal/                  # Thư mục dữ liệu & database SQLite local
```

---

## ⚙️ 2. ĐẶC TẢ CHI TIẾT TẤT CẢ CÁC HÀM VÀ MODULE

### 2.1. Module `control/ui_automator.py` (Engine tự động hóa GUI Native)

Chịu trách nhiệm trực tiếp tương tác với cửa sổ ứng dụng Qt6 của Hermit Coder qua hệ thống Accessibility và Win32 User32.

#### Hàm cấp Module:
* **`_run_on_default_desktop(func, *args, **kwargs) -> Any`**:
  * **Chức năng**: Khởi tạo một worker thread độc lập, đính kèm luồng vào Interactive Desktop người dùng (`WinSta0\default`), khởi tạo bộ nạp COM (`CoInitialize`) và `UIAutomationInitializerInThread`. Sau đó thực thi hàm `func` và trả về kết quả an toàn.
  * **Cơ chế chờ**: Các thao tác đọc có timeout 5 giây và báo lỗi nếu quá hạn. Luồng nhập/gửi job chờ kết quả thật trên worker nền, không trả thành công khi thao tác vẫn còn chạy. Handle desktop dùng kiểu con trỏ 64-bit và được đóng sau khi dùng.

#### Lớp `LocalAppAutomator`:
* **`__init__(log_callback=None)`**:
  * Khởi tạo đối tượng tự động hóa, lưu callback ghi nhật ký, khởi tạo handle cửa sổ (`hwnd`) và tiêu đề cửa sổ (`window_title`).
* **`_log(msg: str) -> None`**:
  * Chuyển tiếp thông điệp log về callback đã đăng ký hoặc logger chuẩn.
* **`find_local_window() -> Optional[int]`**:
  * **Chức năng**: Quét danh sách các cửa sổ cấp cao nhất trên desktop để tìm cửa sổ tool Hermit Coder theo danh sách ưu tiên (`Miraimind tool - by Hermit Coder` ưu tiên số 1, tiếp đến `TTM-Tool - by Hermit Coder`).
  * **Khử Deadlock Win32**: Nhận diện `my_pid = os.getpid()` và bỏ qua toàn bộ cửa sổ thuộc về chính tiến trình điều khiển, ngăn ngừa việc gửi thông điệp `WM_GETTEXT` gây treo GUI.
  * **Trả về**: Handle cửa sổ (`HWND`) hoặc `None`.
* **`_run_without_activation(action)`**:
  * Bật tạm `WS_EX_NOACTIVATE` trong lúc thao tác để tránh cửa sổ local giành quyền nhập. Khôi phục cờ trong `finally`, kể cả khi có lỗi.
* **`_background_action(ctrl, label)` / `_qt_background_press(ctrl, label)`**:
  * Kích hoạt nút bằng UIA; với sidebar checkable và tab Qt, gửi `WM_LBUTTONDOWN/UP` đến cửa sổ chứa control theo vị trí đọc từ UIA. Đây là thông điệp riêng cho cửa sổ, không phải sự kiện chuột toàn hệ thống. Chuyển đổi tọa độ có xử lý DPI.
* **`_background_value_pattern(ctrl, label)`**:
  * Lấy Legacy Accessibility hoặc ValuePattern để nhập và đọc lại dữ liệu, không dùng click hoặc SendKeys làm dự phòng.
* **`_find_by_class(ctrl, cls_name: str) -> Optional[Any]`**:
  * Đệ quy duyệt cây phần tử con UI để tìm phần tử đầu tiên khớp chính xác tên lớp Win32/Qt (ví dụ: `InviteCodeLineEdit`).
* **`_find_all_by_class(ctrl, cls_name: str, out=None) -> List[Any]`**:
  * Đệ quy thu thập toàn bộ các phần tử UI con khớp lớp chỉ định (ví dụ: tất cả `QLabel`, tất cả `QLineEdit`).
* **`_find_by_name(ctrl, text: str, ctrl_type=None) -> Optional[Any]`**:
  * Đệ quy tìm kiếm phần tử theo chuỗi tên hiển thị (so sánh không phân biệt hoa thường) và loại ControlType (ButtonControl, CheckBoxControl,...).
* **`enter_job_and_run(invitation_code: str, target_points: int = 2000, job_name=None, unit_price: int = 0) -> Dict[str, Any]`**:
  * **Quy trình thao tác thực tế**:
    1. Giữ cửa sổ tool local ở nền, bật tạm cơ chế chống kích hoạt cửa sổ.
    2. Chuyển mục sidebar (nếu ở TTMTool thì click `MiraiMind`, nếu ở bản standalone thì click `Jobs`).
    3. Định vị các ô nhập liệu theo ngữ cảnh động: Duyệt tìm `QLabel` nhãn (`Mã mời (*)`, `Tên job/khách hàng`, `Điểm mục tiêu (*)`, `Tổng tiền (VND)`), từ đó truy cập `parent QWidget` và lấy đúng ô `QLineEdit` tương ứng (tránh hoàn toàn sai lệch tọa độ).
    4. Kiểm tra đầy đủ các ô và nút gửi trước khi nhập; bơm giá trị qua Accessibility `SetValue()` rồi đọc lại để xác nhận. Thiếu ô hoặc dữ liệu không khớp thì dừng, chưa gửi job.
    5. Định vị nút `LoadingButton` có tên `Tạo && chạy job` và gọi lệnh UIA kích hoạt nền, không click bằng chuột thật.
  * **Trả về**: Dictionary trạng thái chi tiết của job vừa kích hoạt.
* **`read_active_jobs_from_ui() -> List[Dict[str, str]]`**:
  * **Chức năng**: Đọc trực tiếp nội dung các ô trong bảng "Job đang chạy" trên giao diện local.
  * **Cơ chế bóc tách**: Lấy danh sách `DataItemControl`, phân tích bước nhảy 9 cột (`Thời gian`, `Tên job`, `Loại`, `Đối tượng/Mã`, `Trạng thái`, `Tiến độ`, `Điểm`, `Tổng tiền`, `Thao tác`) và trích xuất thành danh sách Dictionary chuẩn.

---

### 2.2. Module `control/gui.py` (Bàn Điều Khiển Đồ Họa Desktop)

Được xây dựng trên nền tảng **PyQt6**, áp dụng kiến trúc đa luồng phân tách độc lập giữa Luồng hiển thị (Main GUI Thread) và Luồng xử lý nền (Worker Threads).

#### Lớp `StatusPollingThread(QThread)`:
* **Tín hiệu**: `status_updated = pyqtSignal(bool, str, int, bool, list)` (trả về: trạng thái cửa sổ local, tiêu đề cửa sổ, HWND, trạng thái backend, danh sách 12 job gần nhất).
* **`run()`**: Vòng lặp ngầm 2.5 giây/lần quét cửa sổ và truy vấn backend 18888, sau đó phát tín hiệu cập nhật lên GUI. Giúp luồng giao diện chính mượt mà 60 FPS, không bao giờ bị "Not Responding".
* **`stop()`**: Dừng vòng lặp ngầm khi người dùng đóng giao diện.

#### Lớp `AutomationWorkerThread(QThread)` (`control/job_worker.py`, được import vào `gui.py`):
* **Tín hiệu**: `log_signal`, `progress_signal`, `identified_signal`, `finished_signal`, `error_signal`.
* **`__init__(invitation_code, target_points, job_name, unit_price, use_ui_automation)`**: Khởi tạo tiến trình chạy nạp job.
* **`run()`**:
  * Nếu `use_ui_automation=True`: Gọi `LocalAppAutomator.enter_job_and_run` để tự động điều khiển phím/chuột trên app local.
  * Nếu thao tác GUI thất bại: Báo lỗi và dừng, không tự gửi lại qua API để tránh trùng job. Chế độ API chỉ chạy khi người dùng chọn nút Backend.
  * Với chế độ GUI: chụp danh sách ID trước khi gửi, sau đó nhận diện job mới theo mã/tên/mục tiêu. Chế độ API dùng ID trả về trực tiếp. Giám sát theo ID này đến khi job kết thúc, không lấy job gần nhất thay thế. Mất kết nối tạm thời thì giữ hàng chờ và thử đọc lại đúng ID; không gửi lại job.
  * Không xác định được job vừa gửi hoặc gửi lỗi: tạm dừng hàng chờ để người dùng kiểm tra bản local trước khi tiếp tục. Job đã kết thúc với trạng thái lỗi/mã không hợp lệ vẫn cho phép chuyển sang mã kế tiếp.

#### Lớp `ControlMainWindow(QMainWindow)`:
* **`__init__()`**: Thiết lập tiêu đề, kích thước cửa sổ (`1280x930`), nạp theme tối, dựng sidebar với Dashboard, Job & hàng chờ, Telegram, Cài đặt chung, khôi phục hàng chờ và khởi động `StatusPollingThread`.
* **`_apply_theme()`**: Nạp stylesheet QSS hiện đại cho các card frame, line edit, progress bar, nút bấm xanh lá/xanh dương và log box monospace.
* **`_init_ui()`**: Dựng toàn bộ cây widget giao diện:
  * *Top Status Banner*: Đèn báo cửa sổ App Local và Backend 18888.
  * *Splitter Trái*: Form nhập Mã mời, Điểm mục tiêu, Tên job, Tổng tiền và 2 nút bấm thao tác lớn.
  * *Splitter Phải*: Thống kê trạng thái, bộ đếm account/điểm, thanh tiến độ `QProgressBar`, khung nhật ký log trực tiếp.
  * *Bottom Section*: Bảng `QTableWidget` hiển thị các job gần nhất trên hệ thống local.
* **`closeEvent(event)`**: Dừng nhận mã, dừng polling/theo dõi, chờ các QThread kết thúc mà không khóa luồng giao diện; không tự chạy mã kế tiếp khi đang đóng. Không hủy job đã gửi sang bản local.
* **`_check_connections()`**: Cập nhật trạng thái chờ hiển thị trên banner.
* **`_on_status_updated(app_found, title, hwnd, backend_alive, jobs)`**: Slot tiếp nhận dữ liệu từ luồng ngầm để cập nhật màu sắc đèn báo, HWND và nạp dữ liệu bảng.
* **`JobsPage.render_queue()` / `update_progress()`**: Hiển thị danh sách phiên và cập nhật đúng dòng job đang chạy.
* **`JobHistoryPage.refresh()` / `reload()`**: Tải lịch sử từ SQLite theo từ khóa/kết quả; làm mới CSV khi cần.
* **`JobLogPage.select_job()` / `load()` / `append()`**: Chọn nhật ký, nạp dữ liệu đã lưu và hiển thị dòng mới theo đúng ID.
* **`_remove_job(number)`**: Hủy mục chưa chạy hoặc ẩn job đã kết thúc khỏi phiên, giữ bản ghi lịch sử và log.
* **`_start_job(use_ui: bool)`**: Tách danh sách mã, kiểm tra thông số, thêm vào cuối hàng chờ và giữ các nút thêm hoạt động để nhận mã mới trong lúc chạy.
* **`_pump_queue()` / `_on_worker_stopped()`**: Chạy đúng một mục FIFO; chỉ lấy mục tiếp theo sau khi job hiện tại kết thúc và QThread cũ thực sự thoát.
* **`_render_queue()` / `_toggle_queue_pause()` / `_remove_queued_rows()`**: Hiển thị thứ tự/trạng thái, tạm dừng/tiếp tục, xóa mục chưa chạy mà không ảnh hưởng job đang chạy.
* **`_enqueue_telegram_job()` / `_revoke_queued_jobs()`**: Nhận job được cấp quyền từ bot, chống trùng update/mã đang chờ, lưu giá/nguồn Chat ID và hủy mã chưa chạy khi thu hồi quyền.
* **`_restore_queue()`**: Nạp lại mục chờ và mục cần theo dõi ID cũ từ SQLite; khôi phục ở trạng thái tạm dừng, không tự gửi lại job đã có ID.
* **`_on_progress_update(cur, total, points, status)`**: Cập nhật giá trị phần trăm thanh `QProgressBar`, cập nhật text số account và số điểm nạp được theo thời gian thực.
* **`_on_job_finished(job_data)`**: Mở khóa lại nút bấm, ghi kết quả vào nhật ký, không bật hộp thoại làm gián đoạn công việc.
* **`_append_log(text)`**: Thêm dòng log vào console box và tự động cuộn xuống cuối trang.
* **`_on_error(err)`**: Mở khóa lại nút bấm, cập nhật trạng thái lỗi và ghi chi tiết vào nhật ký.
* **`launch_gui()`**: Khởi tạo instance `QApplication`, hiển thị cửa sổ chính và bắt đầu vòng lặp sự kiện `app.exec()`.

---

### 2.3. Module `control/api_bridge.py` (Cầu nối Backend HTTP REST)

Giao tiếp với dịch vụ nền local chạy trên cổng `http://127.0.0.1:18888`.

* **`LocalBackendBridge.__init__(host, port, api_key, timeout=4)`**: Khởi tạo thông tin host, port, Bearer API Key và timeout kết nối.
* **`_headers() -> Dict[str, str]`**: Sinh header chuẩn gồm `Authorization: Bearer <API_KEY>` và `Content-Type: application/json`.
* **`_request(method, path, body=None, params=None) -> Dict[str, Any]`**: Wrapper thực thi HTTP request bằng thư viện chuẩn `urllib.request`, tự động serialize JSON và bắt lỗi `HTTPError`.
* **`is_port_listening() -> bool`**: Kiểm tra nhanh socket TCP cổng 18888 (timeout 0.5s).
* **`is_alive() -> bool`**: Kiểm tra endpoint `GET /health` hoặc `GET /api/jobs`. Trả về `True` nếu backend sẵn sàng phục vụ.
* **`get_health() -> Dict[str, Any]`**: Truy vấn thông tin sức khỏe hệ thống local backend.
* **`create_job(invitation_code, target_count=10, job_name=None, unit_price=0, job_type="customer_invite") -> Dict[str, Any]`**: Gửi request `POST /api/jobs` để tạo job mới trực tiếp vào hàng đợi xử lý.
* **`get_jobs(page=1, limit=10, status=None) -> Dict[str, Any]`**: Lấy danh sách job có phân trang từ `GET /api/jobs`.
* **`get_job(job_id) -> Dict[str, Any]`**: Lấy thông tin chi tiết của một job theo ID.
* **`cancel_job(job_id) -> Dict[str, Any]`**: Hủy job đang chạy qua `POST /api/jobs/{job_id}/cancel`.
* **`stream_logs(callback)`**: Mở luồng Server-Sent Events (SSE) từ `GET /api/logs/stream` để nhận log sự kiện thời gian thực từ engine cày điểm.

---

### 2.4. Module `control/tracker.py` (Bộ Giám Sát Tiến Độ)

* **`JobProgressTracker.__init__(base_url, api_key, automator, log_callback)`**: Khởi tạo bộ giám sát tiến độ.
* **`_render_cli_bar(current, total, length=28) -> str`**: Tạo thanh tiến độ trực quan dạng ký tự `[████████░░░░] 60%` cho màn hình dòng lệnh.
* **`get_active_job_progress() -> Optional[Dict[str, Any]]`**: Lấy thông tin tiến độ của job đang chạy tích cực nhất (ưu tiên truy vấn qua API backend, nếu backend bận sẽ tự động đọc từ bảng UI của tool local).
* **`stream_live_events(duration_seconds=30)`**: Lắng nghe luồng sự kiện SSE và in ra màn hình.
* **`track_job(poll_interval=1.0, max_wait_seconds=900) -> Optional[Dict[str, Any]]`**: Vòng lặp giám sát liên tục: cập nhật tiến độ mỗi giây, tự động phát hiện khi job hoàn thành (`completed`, `failed`, `cancelled`), tính toán tốc độ nạp và điểm số.

---

### 2.5. Module `control/main.py` (Entrypoint Đa Chế Độ)

* **`print_status(automator, bridge) -> None`**: Kiểm tra toàn diện kết nối (Cửa sổ GUI bản local, Socket Backend, Database SQLite, Tổng số job, và nội dung bảng đang hiển thị trên app) và in kết quả ra terminal.
* **`main() -> None`**:
  * Phân tích tham số dòng lệnh (`argparse`):
    * `--status`: Kiểm tra chẩn đoán kết nối.
    * `--code <code> --points <pts>`: Tự động điều khiển GUI app local để nhập job và theo dõi.
    * `--code <code> --api-only`: Bơm job qua API backend và theo dõi.
    * `--track`: Theo dõi tiến độ job đang chạy.
    * Mặc định (không tham số): Khởi chạy bàn điều khiển giao diện Desktop (`launch_gui()`).

---

## 🚀 3. HƯỚNG DẪN VẬN HÀNH

### Cách 1: Sử dụng Giao Diện Đồ Họa (Khuyên dùng)
1. Double click vào file: **`1_MO_GIAO_DIEN.bat`** (ngay đầu thư mục).
2. Giao diện **MiraiMind Local Controller** sẽ bung lên màn hình.
3. Dán một hoặc nhiều mã vào ô **Danh sách mã mời** (mỗi dòng một mã), nhập điểm/tổng tiền cho từng mã -> Bấm **`+ Thêm mã vào hàng chờ`**.
4. Controller điền form và kích hoạt nút chạy trên app local ở nền, không chiếm chuột/bàn phím. Tiến độ hiển thị trên thanh Progress Bar và bảng danh sách. Giữ app local mở (có thể bị cửa sổ khác che), không thu nhỏ.
5. Trong lúc chạy, tiếp tục dán mã và bấm thêm: mã mới nằm cuối hàng chờ. Job kết thúc thì Controller tự chạy mã kế tiếp. Tab **Hàng chờ nhập mã** có nút tạm dừng/tiếp tục và xóa mã chờ đã chọn.

Sau khi cập nhật mã nguồn, đóng/mở lại **Controller** bằng `1_MO_GIAO_DIEN.bat` để nạp bản mới.

### Cách 2: Vận Hành Qua Dòng Lệnh (CLI)
Mở PowerShell hoặc CMD tại thư mục gốc:
```powershell
# Kiểm tra trạng thái hệ thống và app local:
python -I control/main.py --status

# Tự động thao tác nhập mã npxMgy và chạy 2000 điểm trên app local:
python -I control/main.py --code npxMgy --points 2000

# Chỉ theo dõi tiến độ realtime của job đang chạy:
python -I control/main.py --track
```
