# GitHub và Ubuntu VPS

Mô hình được yêu cầu: VPS chạy bot, hàng chờ, lịch sử và bộ thực thi; máy Windows chỉ mở giao diện quản lý từ xa. Tắt máy Windows không dừng dịch vụ VPS.

**Trạng thái bộ thực thi: chưa hoàn thành.** Bot, API quản lý, lưu dữ liệu và giao diện từ xa đã có mã nguồn chạy không cần desktop trên VPS. Bộ thực thi thật của bản Windows nằm trong EXE được cung cấp, không nằm trong Controller. `MiraimindLocal/local_api.py:start_job` chỉ ghi nhận yêu cầu, không thực thi tác vụ. `clone/core` là bản tái dựng, có nhánh sinh ID phiên/MMUID dự phòng và khóa MMUID v3 không hợp lệ. Không thể dùng việc chạy thành công test mã hóa để khẳng định job thật chạy được.

`vps/executor.py:readiness` hiện báo chưa sẵn sàng. Dịch vụ cho phép nhận/lưu job nhưng giữ hàng chờ dừng, từ chối tiếp tục chạy bằng bộ thực thi chưa hoàn chỉnh. Cần hoàn thiện/port bộ thực thi thật và kiểm thử một job hợp lệ trên Linux trước khi mở chạy sản xuất. Không có tiến độ, doanh thu hay kết quả job giả. Hai nguồn mail được nối thử trong adapter là Mail.tm và TempMail.lol; chưa port đầy đủ các provider từ ứng dụng Windows.

## Nội dung repository

- `control/`: giao diện Windows hiện tại, giao diện quản lý VPS và lớp dữ liệu dùng chung.
- `vps/`: dịch vụ Python không dùng Qt, UI Automation, Wine hay desktop.
- `clone/core/`: mã giao thức tái dựng có sẵn, giữ riêng để đối chiếu và hoàn thiện adapter.
- `deploy/`: cài môi trường và unit systemd.
- `.github/workflows/checks.yml`: kiểm tra backend trên Ubuntu/Python 3.10.

Thư mục gốc được cung cấp còn chứa EXE, DLL và thư viện Windows. Chúng được giữ tại máy và loại khỏi repository cùng database, token, cấu hình riêng, bản sao lưu, cookie và log. Bản desktop điều khiển local vẫn cần runtime Windows gốc bên ngoài repository. Clone từ GitHub để dùng giao diện VPS không cần runtime đó. Không tự chuyển bot, người dùng, job hoặc token đang vận hành từ máy Windows lên VPS.

## Cài VPS Ubuntu 22.04

```bash
sudo apt-get update
sudo apt-get install -y git python3-venv ca-certificates
git clone git@github.com:chinh7722/tool_mochi.git ~/hoshino-tool
cd ~/hoshino-tool
bash deploy/install.sh
sudo install -m 644 deploy/hoshino.service /etc/systemd/system/hoshino.service
sudo systemctl daemon-reload
sudo systemctl enable --now hoshino.service
systemctl status hoshino.service --no-pager
```

Unit mẫu dùng user `root1`, mã nguồn `/home/root1/hoshino-tool`, dữ liệu `/home/root1/.local/share/hoshino`, cấu hình `/home/root1/.config/hoshino/server.env`. Nếu dùng tài khoản/đường dẫn khác, sửa unit trước khi cài. Cấu hình được tạo một lần với khóa quản lý ngẫu nhiên, quyền file `600`; cài lại không ghi đè token hay database. Bot Token trên Linux được mã hóa Fernet bằng `token.key` trong thư mục dữ liệu; sao lưu cả DB lẫn key. Token DPAPI Windows không dùng trực tiếp trên Linux.

API chỉ lắng nghe `127.0.0.1:18900`, yêu cầu khóa Bearer và đi qua SSH tunnel. Không mở cổng 18900 ra Internet. Không chạy hai bot polling với cùng Bot Token.

## Mở giao diện quản lý trên máy tính

Cài Python và thư viện giao diện trong môi trường riêng (đứng tại thư mục `control` để tránh DLL Python 3.11 đi kèm bộ cũ):

```powershell
python -I -m pip install -r requirements-remote.txt
```

Mở tunnel bằng OpenSSH, thay HOST và PORT bằng máy chủ của bạn:

```powershell
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 127.0.0.1:18900:127.0.0.1:18900 -p PORT root1@HOST
```

Giữ tunnel mở, chạy `2_MO_QUAN_LY_VPS.bat`. URL là `http://127.0.0.1:18900`. Nhập khóa `HOSHINO_ADMIN_TOKEN` lấy riêng từ file cấu hình VPS. Khóa được lưu bằng DPAPI trong `control/data/remote.sqlite3`, không được gửi cho Telegram. Không đăng khóa vào GitHub/chat. Bot Token là trường riêng trong trang Telegram; lưu cấu hình rồi bật bot trên VPS. Tên admin/partner, quyền và đơn giá được quản lý trong trang này.

Giao diện giữ Dashboard, bốn mục Jobs, Telegram và Cài đặt; dùng konpeito, không thêm quản lý proxy/chi phí mail. Lịch sử/log được đọc từ VPS; CSV xuất trên máy quản lý là một bản sao. Cache giao diện là dữ liệu tạm, VPS là nguồn dữ liệu chính. Các thay đổi cấu hình chỉ được xác nhận khi VPS phản hồi; mất mạng sẽ thử lại cùng request ID để không thêm trùng job. Đóng giao diện không tắt bot hay hủy job.

## Vận hành và kiểm thử

```bash
sudo systemctl restart hoshino
journalctl -u hoshino -n 50 --no-pager
~/hoshino-tool/.venv/bin/python -I ~/hoshino-tool/vps/test_service.py
~/hoshino-tool/.venv/bin/python -I ~/hoshino-tool/clone/test_miraimind.py
```

Job đang xử lý khi VPS bị dừng/khởi động lại được đánh dấu gián đoạn, giữ tiến độ và không tự gửi lại tác vụ có thể đã có hiệu lực. Job còn chờ vẫn được lưu; lịch hẹn dùng UTC, hiển thị giờ Việt Nam. Thu hồi quyền hủy job chưa chạy. Xóa khỏi phiên không xóa lịch sử. Token bot không được trả về trong snapshot.

Kiểm thử backend dùng SQLite tạm và executor giả để xác minh điều phối; không chứng minh giao thức MiraiMind thật đã hoạt động trên Ubuntu. Test giao diện từ xa dùng HTTP thật trên loopback và backend giả, không gửi tin Telegram hay tạo tài khoản thật.

Tài liệu tham chiếu: [GitHub ignore rules](https://docs.github.com/en/get-started/git-basics/ignoring-files), [systemd service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html), [Telegram getUpdates](https://core.telegram.org/bots/api#getupdates).
