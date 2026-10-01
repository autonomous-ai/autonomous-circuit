# Claude Pet — the tune-up prompt (2026-10-01)

For an existing prototype-ready Pet workspace (harness-19, Astra, 2026-09-25) before the first order:
widen the VBUS entry run to the 0.6 mm floor, re-check the discontinued LED, write and build the
firmware that was never written, republish. Open the folder on purpose (⌘N → Project → Open Folder),
any KiCad tile, permission full. Fits the dialog.

```
Đọc hết rồi mới làm. Plan ≤ 15 dòng rồi làm luôn trong turn này. Không hỏi.

ĐÂY LÀ TINH CHỈNH, KHÔNG VẼ LẠI. Board Claude Pet trong folder này (harness-19) đã prototype-ready, chuẩn bị đặt mẫu. Giữ nguyên kích thước 43.7×32, bảng chân, linh kiện (trừ mục 2), vị trí linh kiện. Mọi sửa đổi phải qua transaction của kicadpy (snapshot → apply → check → commit).

LUẬT ĐỌC: chỉ folder này, $KICAD_HARNESS_ROOT, $KICAD_HARNESS_BLOCKS và web datasheet/LCSC/JLC. Không mở folder run khác.

1. ĐƯỜNG NGUỒN VÀO: hardware yêu cầu VBUS từ USB-C tới tụ C1/C2 và LDO U2 phải ≥ 0.6 mm (hiện 0.187 mm). Tạo netclass riêng cho VBUS (track 0.6, via 0.8/0.4) rồi đi lại CHỈ đoạn đó; không đụng fanout RP2040. Kiểm bằng "$KICAD_HARNESS_PYTHON" -m kicadpy.verify power design/main.kicad_pro --rail VBUS=10 → entry VBUS không còn cờ. DRC 0, netlist parity 0 khác biệt.

2. LED: README ghi listing LED đã ngừng sản xuất. Tra tồn kho live trên LCSC đúng mã. Hết hàng thì đổi sang mã còn hàng có datasheet ghi VDD min ≤ điện áp đang cấp, cùng footprint; cập nhật parts.json, ghi lý do vào README. Còn hàng thì ghi số tồn + ngày tra.

3. FIRMWARE (chưa có): viết vào firmware/ theo product.json (bảng chân, GPIO22 và GPIO2 giữ LOW tới khi cấu hình xong, core 40 MHz, QSPI ≤ 31.25 MHz). Thứ tự: LED trạng thái → khung đen ST7789 rồi vẽ mắt pet → đọc TTP223 → 2 LED RGB. USB CDC: lúc boot in HELLO pet v1, ping → PONG pet v1, lệnh STATE <tên> đổi trạng thái mặt, TOUCH gửi lên khi chạm. Unit test host. BUILD RA UF2 bằng toolchain tile (PICO_SDK_PATH có sẵn), flash.json family rp2040 trỏ đúng file. Chưa có UF2 là chưa xong.

4. PUBLISH lại --manufacturing, gate 0, stale none, attestation mới, knowledge learn. README thêm mục "Đặt mẫu": JLCPCB Standard 2 lớp 1.6 mm, các việc người đặt phải tự kiểm (preview chiều linh kiện phân cực, thứ tự chân màn và module cảm ứng, tồn kho LED), và một phép thử đầu tiên trên bàn bằng lời thường.

Báo cuối: 5 dòng, số trước chữ.
```
