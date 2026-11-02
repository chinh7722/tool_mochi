# Hình ảnh của hoshino-tool

Chỉ cần chép ảnh vào **thư mục này**, đúng tên bên dưới, rồi đóng và mở lại tool.
Không cần chép sang `src/gui/assets`. Không có ảnh thì tool vẫn chạy bằng giao diện mặc định.

| Tên file | Vị trí hiển thị | Kích thước gợi ý |
| --- | --- | --- |
| `logo.png` hoặc `logo.svg` | Logo phía trên thanh bên trái | PNG nền trong suốt, từ 128 × 128 px |
| `app.ico` | Biểu tượng trên thanh tiêu đề Windows | ICO chứa 32, 48 và 256 px |
| `sidebar-background.png` hoặc `.jpg` | Nền thanh điều hướng bên trái | 440 × 1800 px, ảnh tối để dễ đọc chữ |
| `dashboard-banner.png` hoặc `.jpg` | Banner trên Dashboard | 1600 × 320 px |
| `nav-dashboard.svg` hoặc `.png` | Biểu tượng Dashboard | 24 × 24 px |
| `nav-mochi.svg` hoặc `.png` | Biểu tượng Mochi Chat | 24 × 24 px |
| `nav-proxy.svg` hoặc `.png` | Biểu tượng Proxy Pool trên thanh bên | 24 × 24 px |
| `nav-telegram.svg` hoặc `.png` | Biểu tượng Telegram | 24 × 24 px |
| `nav-settings.svg` hoặc `.png` | Biểu tượng Cài đặt chung | 24 × 24 px |

PNG được ưu tiên cho logo; SVG được ưu tiên cho các biểu tượng điều hướng. Nếu không có
`app.ico`, tool dùng logo làm icon. Các biểu tượng và tài nguyên **bên trong trang Proxy**
vẫn dùng thư mục `gui/assets/` cũ.

Nút **Mở thư mục hình ảnh** ở cuối thanh bên mở đúng thư mục này.
