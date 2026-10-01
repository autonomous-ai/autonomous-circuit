# Claude Pet — the tune-up prompt (2026-10-01)

For an existing prototype-ready Pet workspace (harness-19, Astra, 2026-09-25) before the first order:
widen the VBUS entry run to the 0.6 mm floor, re-check the discontinued LED, write and build the
firmware that was never written, republish. Open the folder on purpose (⌘N → Project → Open Folder),
any KiCad tile, permission full. Fits the dialog.

```
Đọc hết rồi mới làm. Plan ≤ 15 dòng rồi làm luôn trong turn này. Không hỏi.

ĐÂY LÀ TINH CHỈNH, KHÔNG VẼ LẠI. Board Claude Pet trong folder này (harness-19) đã prototype-ready, chuẩn bị đặt mẫu. Giữ nguyên kích thước 43.7×32, bảng chân, linh kiện (trừ mục 2), vị trí linh kiện. Sửa qua transaction kicadpy.

LUẬT ĐỌC: chỉ folder này, $KICAD_HARNESS_ROOT, $KICAD_HARNESS_BLOCKS và web datasheet/LCSC/JLC. Không mở folder run khác.

1. ĐƯỜNG NGUỒN VÀO: hardware yêu cầu VBUS từ USB-C tới tụ C1/C2 và LDO U2 phải ≥ 0.6 mm (hiện 0.187 mm). Netclass riêng cho VBUS (0.6, via 0.8/0.4), đi lại CHỈ đoạn đó, không đụng fanout RP2040. kicadpy.verify power --rail VBUS=10 → entry VBUS hết cờ; DRC 0; parity 0.

2. LED: README ghi listing LED đã ngừng. Tra tồn kho live LCSC. Hết hàng thì đổi mã còn hàng, cùng footprint, datasheet VDD min ≤ điện áp cấp; cập nhật parts.json + README. Còn hàng thì ghi số tồn và ngày tra.

3. FIRMWARE (chưa có): viết vào firmware/ theo product.json (bảng chân, GPIO22 và GPIO2 giữ LOW tới khi cấu hình xong, core 40 MHz, QSPI ≤ 31.25 MHz). Thứ tự: LED trạng thái → khung đen ST7789 rồi mắt pet → TTP223 → 2 LED RGB. USB CDC: lúc boot in HELLO pet v1, ping → PONG pet v1, lệnh STATE <tên> đổi trạng thái mặt, TOUCH gửi lên khi chạm. Unit test host. BUILD RA UF2 bằng toolchain tile (PICO_SDK_PATH có sẵn), flash.json trỏ đúng file. Chưa có UF2 là chưa xong.

4. PUBLISH lại --manufacturing, gate 0, stale none, attestation mới, knowledge learn. README thêm mục "Đặt mẫu": JLCPCB Standard 2 lớp 1.6 mm, việc người đặt tự kiểm (preview chiều linh kiện phân cực, thứ tự chân màn và cảm ứng, tồn kho LED), và phép thử đầu tiên trên bàn bằng lời thường.

Báo cuối: 5 dòng, số trước chữ.
```
