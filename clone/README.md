# MiraiMind Tool Decompiled & Reconstructed Core (Clone v2.6.3)

Mã nguồn sạch, đã được giải mã, phân tích và tái tạo hoàn chỉnh 100% từ binary native gốc (`MiraimindTool.exe` / `TTMTool.exe` v2.6.3 đóng gói bằng Nuitka C-Python 3.11).

Toàn bộ các thuật toán mã hóa độc quyền, cơ chế ký định danh thiết bị phần cứng Android, các endpoint API chính thức của MiraiMind/Momo, bộ giải CAPTCHA AI, tích hợp email tạm thời và hệ thống tự động hóa tác vụ đều được giải mã và viết lại nguyên bản bằng Python hiện đại, không còn bị làm rối hay phụ thuộc vào file EXE đóng gói.

---

## 1. Cấu trúc thư mục `clone`

```text
clone/
├── README.md                      # Tài liệu giải mã và hướng dẫn chi tiết
├── requirements.txt               # Thư viện phụ thuộc
├── main.py                        # Điểm khởi động chương trình (Giao diện Desktop & CLI)
├── test_miraimind.py              # Bộ kiểm thử tự động (Unit Test & Crypto Verification)
│
├── core/
│   ├── crypto/                    # Cụm thuật toán mật mã đã giải mã
│   │   ├── mdata_emulator.py      # P-384 ECDH, native_m, native_h, build_mdata, Java URL Encode
│   │   ├── mmuid_codec.py         # MMUID Command 101, 107, 102/103 (v3 supplement), mzip
│   │   ├── photon_crypto.py       # Giao thức Photon IM, mã hóa AES-128 packet, PBPacket
│   │   └── device_profile.py      # Giả lập phần cứng Android (IMEI, AndroidID, MAC, Fingerprint)
│   │
│   ├── api/                       # Kết nối API chính thức của MiraiMind
│   │   ├── endpoints.py           # Danh mục toàn bộ URL API MiraiMind / Momo / Immomo
│   │   └── fetcher.py             # MiraimindFetcher: loginGuest, confirmEmailCode, submitInvitation...
│   │
│   ├── captcha/                   # Bộ giải CAPTCHA Geetest AI
│   │   └── geetest_solver.py      # Google SigLIP ONNX Nine-Grid & Mask IoU Icon Matcher
│   │
│   ├── mail/                      # Dịch vụ hộp thư tạm thời & bắt mã OTP
│   │   └── mail_providers.py      # TempMail.lol (v2), Mail.tm, NiMail, OTP Regex Engine
│   │
│   ├── runners/                   # Bộ điều phối tác vụ nạp điểm tự động
│   │   └── invitation_runner.py   # Quy trình nạp khách / tài khoản điểm trọn vòng đời
│   │
│   └── android/                   # Hooking thiết bị & emulator
│       └── frida_manager.py       # Quản lý Frida-server & script hook SSL Unpinning / MNative
│
└── gui/                           # Giao diện người dùng Desktop
    └── main_window.py             # Bảng điều khiển tác vụ, nhật ký trực tiếp, xuất danh sách tài khoản
```

---

## 2. Chi tiết các thuật toán đã giải mã

### A. Mã hóa Native M & H (`core.crypto.mdata_emulator`)
- **Khóa & IV (`native_aes_parameters`)**:
  Được sinh động từ chuỗi `native_key` (là mã MD5 32 ký tự hex của Public Key P-384 trong bước đăng nhập, hoặc Base64 ECDH Shared Secret trong các yêu cầu có xác thực):
  $$\text{Key} = \text{SHA512}(native\_key)[:29] + \text{'mms'}$$
  $$\text{IV} = \text{SHA512}(native\_key)[32:46] + \text{'ce'}$$
- **Hàm mã hóa `native_m(plaintext, native_key)`**:
  - Dữ liệu plaintext (UTF-8) được đệm PKCS#7 theo khối 16 byte.
  - Mã hóa AES-256-CBC với Key và IV ở trên.
  - Tính mã băm SHA-1 (20 byte) của khối dữ liệu đã mã hóa.
  - Ghép: `SHA1 + Ciphertext` rồi chuyển sang chuỗi Base64.
- **Hàm giải mã `native_h(envelope_b64, native_key)`**:
  - Giải mã Base64, trích 20 byte đầu làm mã kiểm tra SHA-1.
  - So sánh mã SHA-1 với phần ciphertext còn lại để đảm bảo tính toàn vẹn.
  - Giải mã AES-256-CBC, gỡ đệm PKCS#7 và trả về chuỗi JSON ban đầu.

### B. Trao đổi khóa P-384 ECDH & Đóng gói `mdata`
- Sử dụng đường cong elliptic `secp384r1` sinh cặp khóa phù du (ephemeral keypair).
- Điểm Public Key không nén (97 byte, bắt đầu bằng tiền tố `0x04`) được mã hóa Base64 thành chuỗi 132 ký tự.
- Gói `mdata` gửi lên máy chủ có định dạng:
  $$mdata = \text{JavaURLEncode}(\text{native\_m}(plaintext, \text{MD5}(pubkey)) + "\&" + pubkey)$$

### C. Định danh phần cứng MMUID v1 & v3 (`core.crypto.mmuid_codec`)
- **Command 101**:
  - Sử dụng khóa cố định: `KEY = b"CAg3ZgqjY20eHnn4"`, `IV = b"Nrq2CYQskOPt8ERM"`.
  - Mã hóa JSON định danh thiết bị (thay thế dấu `/` thành `\/`).
  - Ký dữ liệu bằng SHA-512, tạo chuỗi token `mzip` phục vụ đăng ký tại `/v1/log/upload_info/supplement`.
- **Command 107**:
  - Giải mã phản hồi từ máy chủ để trích xuất mã định danh phần cứng chuẩn gồm 40 ký tự hex (`mmuid`).
- **MMUID v3 supplement**:
  - Đăng ký phần cứng mở rộng qua `/v3/log/upload_info/supplement`.

---

## 3. Danh mục API chính thức của MiraiMind (`core.api.endpoints`)

| Tác vụ | Endpoint URL | Phương thức |
|---|---|---|
| **Đăng nhập khách** | `https://api.miraimind.ai/miraimind-server/api/app/user/loginGuest` | POST (`mdata`) |
| **Đăng nhập Email** | `https://api.miraimind.ai/miraimind-server/api/app/user/loginWeb` | POST (`mdata`, OTP) |
| **Gửi mã OTP** | `https://melon-gateway-os.immomo.com/miraimind-server/api/app/user/sendEmailVerifyCode` | POST (`email`) |
| **Nhập mã mời** | `https://api.miraimind.ai/miraimind-server/api/app/mall/invitation/submit` | POST (`invitationCode`) |
| **Tra cứu điểm & coin** | `https://api.miraimind.ai/miraimind-server/api/app/mall/getUserBalance` | GET |
| **Follow Creator** | `https://api.miraimind.ai/miraimind-server/api/app/follow/followOne` | POST (`creatorId`) |
| **Thả tym bài viết** | `https://api.miraimind.ai/miraimind-server/api/app/feed/like` | POST (`feedId`) |
| **Đăng ký MMUID v1** | `https://app-log-oversea.immomo.com/v1/log/upload_info/supplement` | POST (`mzip`) |
| **Đăng ký MMUID v3** | `https://app-log-oversea.immomo.com/v3/log/upload_info/supplement` | POST (`mzip`) |
| **Báo cáo HData** | `https://app-log-oversea.immomo.com/v1/log/upload_info` | POST (JSON) |

---

## 4. Hướng dẫn sử dụng

### Cài đặt môi trường
Đảm bảo đã cài đặt Python 3.10 trở lên và cài các gói cần thiết:
```powershell
pip install -r requirements.txt
```

### Chạy kiểm tra tính toàn vẹn (Self-Test)
Kiểm tra toàn bộ thuật toán mã hóa và định danh thiết bị:
```powershell
python main.py --self-test
# Hoặc chạy kiểm thử đơn vị:
python test_miraimind.py
```

### Khởi động Giao diện Desktop (GUI)
```powershell
python main.py
```
- Nhập **Mã mời** (mặc định: `npxMgy` hoặc mã tùy chọn).
- Chọn **Số lượng tài khoản** cần nạp.
- Chọn **Nhà cung cấp Mail** (`TempMail.lol` hoặc `Mail.tm`).
- Bấm **Bắt đầu chạy** để theo dõi trực tiếp quá trình đăng ký, nhận OTP và cộng điểm.
- Bấm **Xuất tài khoản** để lưu file CSV / JSON / TXT.

### Chạy qua dòng lệnh (CLI Headless Mode)
Dành cho máy chủ, VPS hoặc kịch bản tự động hóa:
```powershell
# Chạy nạp 5 tài khoản vào mã mời
python main.py --cli --code npxMgy --count 5 --provider tempmail_lol

# Chạy có cấu hình proxy
python main.py --cli --code npxMgy --count 10 --proxy "http://user:pass@1.2.3.4:8080"
```
