# Claude Pet Rover — the prompt (v1, 2026-10-01)

The Pet that moves: a cylinder on two wheels, a face, a belly to poke, an ESP32-C3 SuperMini for a
brain so it can talk Wi-Fi later. Written for what the owner has on the desk (the "man096" 0.96"
I²C OLED, four "SG90 360°" continuous servos, one ESP32-C3 mini) and for a power bank, not a
battery on the board — the safety envelope has no sealed battery block yet, and a power bank is
5 V USB to the board. Reads the module cards this prompt was written beside
(`modules/esp32-c3-supermini`, `sg90-continuous-module`, `oled-0.96-i2c-module`, `ttp223-module`).

Decisions the owner did not take and the prompt takes for them (change the line if wrong): the
pet does not self-balance (SG90s are too coarse; an I²C header is left for an IMU), the body
diameter is proposed by the agent from the parts, and the board's own USB-C is power only.

The dialog takes ~2000 characters: paste the **short form** at the end; it points here.

```
CHẠY THỬ TILE KICAD — đọc hết tin này trước khi làm bất cứ gì.
Chủ dự án ĐÃ DUYỆT TRƯỚC plan bên dưới: trình bày plan tối đa 20 dòng rồi build luôn trong
cùng turn này. Không chờ "yes". Không hỏi gì cả.

# LUẬT ĐỌC — vi phạm là bài không tính
Chỉ đọc trong: workspace này, $KICAD_HARNESS_ROOT, $KICAD_HARNESS_BLOCKS và
$KICAD_HARNESS_BLOCKS/../modules, và web cho datasheet / LCSC / EasyEDA / JLC. KHÔNG ls ~/harnesses,
KHÔNG mở folder run khác, KHÔNG đọc ~/projects, KHÔNG copy từ board nào khác. Mọi số chân và bản
vẽ từ datasheet hoặc trang EasyEDA/LCSC của đúng mã, ghi nguồn vào engineering/sources.md.

# Claude Pet Rover — cái board
Một con pet để bàn thân hình trụ, hai bánh xe hai bên, mặt là màn OLED 0.96" ở phía trước, bụng
là một module cảm ứng để chọc. Não là module ESP32-C3 SuperMini cắm socket; nói chuyện với Mac
qua cổng USB của chính module (sau này qua Wi-Fi của module). Board là toàn bộ điện tử trừ 3 module
mua sẵn, 2 servo và vỏ in 3D. KHÔNG tự cân bằng (chừa header I2C cho IMU sau). KHÔNG pin trên board.

# Các quyết định ĐÃ CHỐT. Không mở lại, không "cải tiến":
- Não: ESP32-C3 SuperMini đúng theo $KICAD_HARNESS_BLOCKS/../modules/esp32-c3-supermini/BLOCK.md:
  hai socket 1×8 cách nhau 15.24 mm, antenna keep-out cả hai lớp, IO2/IO8/IO9 không dùng, IO21 không
  bị kéo xuống GND. Không chip trần, không mạch RF.
- Nguồn: board có CỔNG USB-C NGUỒN RIÊNG ($KICAD_HARNESS_BLOCKS/usb-c-power/BLOCK.md, Rd 5.1k mỗi
  chân CC) cắm pin sạc dự phòng ≥ 2 A. Từ đó ra rail V5 nuôi 2 servo và chân 5V của module qua một
  Schottky (để USB của module cắm Mac không bao giờ phải gánh servo, và V5 không chảy ngược vào Mac).
  Rail servo KHÔNG nối tới USB của module ở bất kỳ đâu. Silk cạnh cổng nguồn: "POWER IN: power
  bank / charger only". Cổng nguồn này không phải thiết bị USB của máy tính: giới hạn tụ 10 µF trên
  VBUS KHÔNG áp cho rail V5; ghi điều đó trong product.json (đặt tên rail V5, không phải VBUS).
- Ngoại vi 3.3 V lấy từ LDO của module: tổng ≤ 40 mA theo module card (OLED + touch + pull-up).
- Bánh: 2 servo quay liên tục đúng $KICAD_HARNESS_BLOCKS/../modules/sg90-continuous-module/BLOCK.md
  trên 2 block servo-header (V+ LUÔN chân giữa, cùng hướng, silk L/R + GND/V+/SIG). Dòng thiết kế
  2 × 0.8 A kẹt; tụ lớn trên V5 tính từ số đó (ghi phép tính vào engineering/power.md), đồng V5
  và GND servo tính cho dòng đó; tín hiệu 3.3 V từ GPIO, trở nối tiếp là quyết định của mày.
- Mặt: OLED 0.96" I2C đúng $KICAD_HARNESS_BLOCKS/../modules/oled-0.96-i2c-module/BLOCK.md trên
  header 1×4, pull-up DNP mặc định. Thêm MỘT header 1×4 I2C nữa cùng bus, để trống, cho IMU sau.
- Bụng: TTP223 đúng $KICAD_HARNESS_BLOCKS/../modules/ttp223-module/BLOCK.md trên header 1×3.
- Đèn: một LED trạng thái ($KICAD_HARNESS_BLOCKS/status-led/BLOCK.md). Không nút (module có sẵn).
- Cơ khí: board TRÒN, đường kính nhỏ nhất đủ chỗ, mày đề xuất và ghi vào README; 4 lỗ M2 cách đều;
  cổng USB nguồn và USB của module cùng hướng ra một mặt vỏ; 2 header servo ở hai bên đối xứng.
  Mỗi gợi ý vỏ đều là gợi ý, chủ dự án in vỏ theo board.

Bảng chân — TẠM THỜI, ghi vào product.json là provisional kèm câu "firmware must follow this
table or the board changes in a later revision": mày chọn GPIO từ module card, né strap pin và
UART0, ghi cả bảng vào product.json + silk cạnh từng header.

# "pass" cho 7 mục manufacturing.json
Mỗi mục fail PHẢI trích được một dòng trong tin này, AGENTS.md, BLOCK.md hoặc datasheet. "Chưa đo",
"chưa có firmware" KHÔNG phải fail — đó là hardwareTested=false + một dòng trong bringup.md.
- power: rail V5 chỉ từ cổng nguồn riêng; module 5V qua Schottky; ngoại vi 3V3 ≤ 40 mA; tụ V5 có
  phép tính. Hết. Không IC đọc CC, không supervisor, không load switch, không pin.
- protection: ESD theo block usb-c-power nếu block có; Schottky đúng chiều, ghi mã và dòng định mức.
- pinout: từng chân đối chiếu module card + datasheet; parity sạch; V+ ở chân giữa cả 2 cổng servo.
- thermal: Schottky và đồng V5 ở 1.6 A, ghi số.
- assembly, fabricator, bringup: như AGENTS.md.
Thêm bất kỳ IC nào ngoài danh sách chốt để qua check = sai.

# Linh kiện, giá
parts.json đúng hãng, MPN, package, mã LCSC từ listing sống; ưu tiên Basic; tồn kho + ngày tra.
Mục tiêu ≤ 30 footprint, ≤ 6 USD/board giá LCSC lẻ CHƯA KỂ 3 module và 2 servo. Vượt ghi lý do
từng con. 2 lớp, JLCPCB, tier ráp Standard, linh kiện một mặt; socket/header hàn tay cũng được, ghi rõ.

# Firmware (sau khi prototype-ready, như AGENTS.md) — Arduino ESP32-C3, arduino-cli có sẵn
- USB CDC qua USB của module. Lúc boot in `HELLO pet v1`; `ping` → `PONG pet v1`.
- `M <trái> <phải>` tốc độ −100..100 mỗi bánh (1500 µs = 0, đảo chiều một bánh vì gắn đối xứng);
  `STOP`; `TRIM <L|R> <us>` chỉnh điểm dừng từng servo, lưu Preferences; `FACE <state>` đổi mặt;
  `TOUCH` gửi lên khi chạm; `oled sh1106` / `oled ssd1306` chuyển driver, lưu Preferences.
- Boot, reset, flash trống: KHÔNG xung ra servo. Xung chỉ ra khi Mac ra lệnh; mất kết nối 2 s → STOP.
- Unit test host cho parser + giới hạn. BUILD ra `<build>/<sketch>.ino.bin` bằng arduino-cli,
  flash.json family esp32 trỏ đúng; chưa có .bin là chưa xong.

# Bàn giao
design/, publish --manufacturing, engineering/, manufacturing.json, README lời thường: (1) giả định,
(2) người đặt phải kiểm gì (so silk socket với module thật, thứ tự chân OLED, chiều Schottky,
tồn kho), (3) chưa có gì lên nguồn, (4) phép thử đầu tiên: cắm pin dự phòng vào cổng nguồn, cắm
Mac vào USB module, gửi `M 30 30`, hai bánh quay cùng chiều tiến, gửi `STOP` thì đứng.
```

## Short form (the ⌘N dialog takes ~2000 characters)

```
Đọc hết rồi mới làm. Plan ≤ 20 dòng rồi build luôn trong turn này, không hỏi.

ĐỀ ĐẦY ĐỦ, là hợp đồng: $KICAD_HARNESS_ROOT/harness/kicad/prompts/claude-pet-rover.md — đọc khối trong ``` và làm đúng từng dòng. Tóm tắt:

LUẬT ĐỌC: chỉ workspace này, $KICAD_HARNESS_ROOT, $KICAD_HARNESS_BLOCKS (+ ../modules) và web datasheet/LCSC/JLC. Không mở folder run khác.

BOARD: Claude Pet Rover — pet thân trụ, 2 bánh hai bên, mặt OLED 0.96" I2C, chạm TTP223, não ESP32-C3 SuperMini cắm socket, nói với Mac qua USB module. Không tự cân bằng, không pin trên board.

CHỐT: C3 theo modules/esp32-c3-supermini. Nguồn: cổng USB-C NGUỒN RIÊNG trên board (block usb-c-power, Rd 5.1k) từ pin sạc dự phòng ≥ 2 A → rail V5 nuôi 2 servo và chân 5V module qua Schottky; rail servo KHÔNG BAO GIỜ về USB module. 2 bánh = 2 servo SG90 quay liên tục theo modules/sg90-continuous-module trên block servo-header (V+ giữa), 2 × 0.8 A kẹt, tụ lớn tính từ đó, đồng 2 mm. Màn theo modules/oled-0.96-i2c-module + 1 header I2C dự phòng (IMU). Touch theo modules/ttp223-module. 1 LED status. Không nút (module có sẵn). Ngoại vi 3V3 ≤ 40 mA từ LDO module.

HÌNH: board tròn nhỏ nhất đủ linh kiện, mày đề xuất; 4 lỗ M2; hai cổng USB cùng hướng. 2 lớp, JLCPCB Standard, một mặt.

PASS: mỗi fail trích được dòng trong đề/AGENTS/BLOCK/datasheet. ≤ 30 footprint, ≤ $6 chưa kể module; vượt ghi lý do.

FIRMWARE (Arduino C3, arduino-cli có sẵn): boot không xung servo; HELLO/PONG pet v1; M <trái> <phải> −100..100; STOP; TRIM <n> <us> lưu flash; FACE <state>; TOUCH gửi lên; oled sh1106 chuyển driver. Unit test host. Build ra .bin, flash.json family esp32. Chưa có .bin là chưa xong.

BÀN GIAO: publish --manufacturing, gate 0, README lời thường + phép thử: cắm pin dự phòng, gửi M 30 30, hai bánh quay cùng chiều tiến.
```
