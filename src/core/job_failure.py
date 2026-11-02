"""User-facing failure reasons; raw exception text never leaves the tool."""
import unicodedata


def _plain(value):
    text=str(value or '').casefold()
    return ''.join(c for c in unicodedata.normalize('NFD',text)
        if not unicodedata.combining(c)).replace('đ','d')


def _reason(message='',status=''):
    text=_plain(message)
    direct={
        'invalid_code':'Mã mời không hợp lệ hoặc đã hết hiệu lực.',
        'invite_limit_reached':'Mã mời đã đạt giới hạn nhận thưởng trong 24 giờ.',
        'already_redeemed':'Tài khoản đã nhập mã mời trước đó, không được cộng thêm lượt.',
        'tls_error':'Không xác thực được chứng chỉ HTTPS của dịch vụ.'}
    if status in direct:
        return direct[status]
    def has(*terms):
        return any(term in text for term in terms)
    if has('invalid_code','ma moi khong hop le','ma moi khong ton tai','invalid invite','invalid referral'):
        return direct['invalid_code']
    if has('invite_limit_reached','rolling 24-hour','gioi han 24','24-hour limit'):
        return direct['invite_limit_reached']
    if has('already_redeemed','da tung nhap ma','da nhap ma moi','already redeemed'):
        return direct['already_redeemed']
    if has('certificate_verify_failed','certificate error','chung chi','tls_certificate_error'):
        return direct['tls_error']
    if has('het hang','khong du so luong','out of stock','sold out','san pham khong ton tai'):
        return 'Shop mail hết hàng hoặc không đủ số lượng ở các sản phẩm đã thử.'
    if has('khong du tien','so du khong du','insufficient balance','not enough money'):
        return 'Số dư tại shop mail không đủ để mua lô tài khoản.'
    if has('chua cau hinh don gia','don gia ban chua','chua co don gia'):
        return 'Chưa cấu hình đơn giá bán hợp lệ trong tool.'
    if has('api key','api_key') and has('khong hop le','invalid','unauthorized','khong chinh xac','het han'):
        return 'API key của nhà cung cấp không hợp lệ, hết hạn hoặc chưa có quyền sử dụng.'
    if has('vui long nhap api key','chua nhap api key','chua cau hinh mail','nhap api key nha cung cap'):
        return 'Chưa cấu hình đầy đủ API key mua mail trong tool.'
    if has('khong xac dinh duoc trang thai giao dich','kiem tra lich su giao dich',
            'khong phai json','dung de doi soat','so du truoc giao dich','so du khong doi'):
        return 'Chưa xác minh được giao dịch hoặc số tiền mua mail; job dừng để đối soát, tránh mua trùng.'
    if has('thao tac qua nhanh','too many requests','rate limit','ratelimit','http 429','status=429'):
        return 'Dịch vụ giới hạn tốc độ yêu cầu; chưa hoàn tất được sau các lượt thử lại.'
    if has('rotation_not_ready','proxy dang cho','chua the doi proxy','moi co the doi proxy'):
        return 'Proxy đang chờ đủ thời gian xoay IP; chưa cấp được kết nối cho job.'
    if has('proxy mua mail chua co','tab proxy mua mail chua co'):
        return 'Chưa cấu hình proxy xoay khả dụng tại tab Proxy mua mail.'
    if has('tat ca proxy mua mail') and has('tu choi'):
        return 'Nhà cung cấp từ chối toàn bộ proxy mua mail; kiểm tra key xoay hoặc hạn gói proxy.'
    if has('het thoi gian cho proxy mua mail xoay'):
        return 'Hết thời gian chờ các proxy mua mail xoay IP.'
    if has('can nhap du 2 api key rieng','can nhap du api key rieng'):
        return 'Chưa nhập đủ API mua mail riêng của GmailVip.vn và 1TrieuMail.com. Dùng /mailapi để cập nhật cả hai key.'
    if has('captcha','xac minh danh tinh','xac minh tai khoan','verify your identity','2-step verification'):
        return 'Google yêu cầu xác minh tài khoản/CAPTCHA nên chưa đăng nhập được.'
    if has('account disabled','account suspended','tai khoan bi khoa','tai khoan bi vo hieu hoa'):
        return 'Tài khoản Google bị khóa hoặc vô hiệu hóa.'
    if has('wrong password','incorrect password','sai mat khau','invalid credentials'):
        return 'Google không chấp nhận thông tin đăng nhập của tài khoản.'
    if has('curl: (28)','curl (28)','timed out','timeout','het thoi gian cho'):
        if has('google','oauth','consent'):
            return 'Đăng nhập Google hết thời gian chờ trước khi hoàn tất.'
        return ('Kết nối qua proxy tới dịch vụ hết thời gian chờ.' if has('proxy')
            else 'Kết nối tới dịch vụ hết thời gian chờ.')
    if has('curl: (35)','curl (35)','curl 35','ssl_connect','ssl_error','tls handshake','bat tay tls','boringssl','https/tls'):
        return ('Proxy không hoàn tất được kết nối HTTPS/TLS tới dịch vụ.' if has('proxy')
            else 'Không hoàn tất được kết nối HTTPS/TLS tới dịch vụ.')
    if has('http/2','curl: (16)','curl: (92)'):
        return 'Kết nối HTTP tới dịch vụ bị lỗi giao thức, chưa hoàn tất yêu cầu.'
    if has('khong cap duoc proxy','khong khoi phuc duoc proxy','khong co proxy','khong lay duoc proxy'):
        return 'Không cấp hoặc khôi phục được proxy để chạy job.'
    if has('het tai khoan dau vao','da het tai khoan','lo chi con','khong du mail'):
        return 'Lô mail không còn đủ tài khoản để đạt mục tiêu; tool không tự mua bù.'
    if has('10 lan xu ly khong thanh cong','10 lan that bai'):
        return 'Job dừng sau 10 lần xử lý không thành công liên tiếp.'
    if has('database is locked','database disk image','sqlite','khong luu duoc chu so huu','khong doc duoc lich su'):
        return 'Không đọc/ghi được dữ liệu job trong tool; cần kiểm tra file dữ liệu.'
    if has('no space left','disk full','het dung luong'):
        return 'Ổ đĩa không còn đủ dung lượng để lưu dữ liệu.'
    if has('permission denied','access is denied'):
        return 'Tool không có quyền truy cập file hoặc tài nguyên cần thiết.'
    if has('cannot find chrome','browser executable','khong tim thay chrome','browser has been closed','target closed'):
        return 'Trình duyệt Google không khởi chạy được hoặc đã bị đóng.'
    if has("can't start new thread",'could not start thread'):
        return 'Không khởi chạy được luồng xử lý job; cần kiểm tra tài nguyên của máy.'
    if has('http 401','http 403','status=401','status=403','forbidden','cloudflare'):
        return 'Dịch vụ từ chối quyền truy cập của yêu cầu đăng nhập/kích hoạt.'
    if has('http 500','http 502','http 503','http 504','status=500','status=502','status=503','status=504'):
        return 'Máy chủ dịch vụ đang lỗi hoặc tạm thời không sẵn sàng.'
    if status=='auth_failed':
        return 'Đăng nhập Google/Mochi không thành công; tài khoản chưa được kích hoạt.'
    if status=='network_error' or has('connection refused','connection reset','failed to connect','network_error',
            'ngat/reset','khong ket noi duoc proxy','curl 52','curl 55','curl 56'):
        return 'Không kết nối được dịch vụ hoặc kết nối bị ngắt giữa chừng.'
    return None


def job_failure_reason(summary=None,*,message='',status='failed'):
    """Summarize causes from trusted categories, never echo secrets/URLs/accounts."""
    reasons=[]
    if summary is not None:
        status=getattr(summary,'status',status)
        message=getattr(summary,'error_message','') or ''
    first=_reason(message,status)
    if first:
        reasons.append(first)
    for result in (getattr(summary,'results',()) or ()):
        if getattr(result,'success',False):
            continue
        reason=_reason(getattr(result,'message',''),getattr(result,'status',''))
        if reason and reason not in reasons:
            reasons.append(reason)
        if len(reasons)>=3:
            break
    if not reasons:
        reasons.append('Tool gặp lỗi chưa phân loại; xem log job trong tool để kiểm tra chi tiết.' if message
            else 'Job kết thúc trước khi đạt mục tiêu; dịch vụ chưa trả chi tiết nguyên nhân.')
    return ' '.join(reasons)
