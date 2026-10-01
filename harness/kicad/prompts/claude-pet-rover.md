# Claude Pet Rover — the prompt (v1, 2026-10-01)

The Pet that moves: a cylinder on two wheels, a face, a belly to poke, an ESP32-C3 SuperMini for a
brain so it can talk Wi-Fi later. Written for what the owner has on the desk (the "man096" 0.96"
I²C OLED, four "SG90 360°" continuous servos, one ESP32-C3 mini) and for a power bank, not a
battery on the board — the safety envelope has no sealed battery block yet, and a power bank is
5 V USB to the board. Reads the module cards this prompt was written beside
(`modules/esp32-c3-supermini`, `sg90-continuous-module`, `oled-0.96-i2c-module`, `ttp223-module`).

Settled with the owner 2026-10-01 (second pass): a buzzer ("bíp bíp chơi cho vui"), two servos
only, a SMARS-class body — a small printed box lying flat on four wheels, two of them driven, an
ultrasonic "eyes" module at the front — with lights, and the gestures living in the firmware so the
Mac only sends a state. The pet does not self-balance; the board's own USB-C is power only.

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
Một con pet để bàn kiểu xe SMARS: hộp in 3D nằm ngang, 4 bánh, 2 bánh sau có servo, mặt là màn
OLED 0.96" phía trước, "mắt" là module siêu âm HC-SR04 ở mũi, bụng là module cảm ứng để chọc, có
đèn và còi. Não là module ESP32-C3 SuperMini cắm socket; nói chuyện với Mac qua cổng USB của chính
module (sau này qua Wi-Fi). Board nằm trên nóc xe như hình mẫu, là toàn bộ điện tử trừ các module
mua sẵn, 2 servo và vỏ. KHÔNG tự cân bằng. KHÔNG pin trên board.

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
- Đèn: 2 LED RGB địa chỉ đúng $KICAD_HARNESS_BLOCKS/ws2812-chain/BLOCK.md, cấp từ V5, data 3.3→5 V
  qua dịch mức như block, đặt hai góc trước làm đèn pha; cộng một LED trạng thái
  ($KICAD_HARNESS_BLOCKS/status-led/BLOCK.md). Không nút (module có sẵn).
- Còi: một buzzer điều khiển từ GPIO (transistor nếu cần, ≤ 30 mA), để "bíp" khi cần duyệt.
- Mắt: một header 1×4 cho HC-SR04 (VCC 5V / TRIG / ECHO / GND, ECHO về 3.3 V qua chia áp) ở mũi.
- Mép bàn: 2 header 1×3 EDGE_L / EDGE_R (VCC/GND/OUT) cho cảm biến phản xạ nhìn xuống, chưa mua.
- Cơ khí: board CHỮ NHẬT nằm trên nóc hộp SMARS, nhỏ nhất đủ chỗ, mày đề xuất kích thước và ghi
  vào README; 4 lỗ M2; cổng USB nguồn và USB của module cùng hướng ra đuôi xe; header servo L/R
  hai bên; OLED, HC-SR04 và 2 LED ra mũi. Mỗi gợi ý vỏ đều là gợi ý, chủ dự án in vỏ theo board.

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
Mục tiêu ≤ 40 footprint, ≤ 8 USD/board giá LCSC lẻ CHƯA KỂ các module và 2 servo. Vượt ghi lý do
từng con. 2 lớp, JLCPCB, tier ráp Standard, linh kiện một mặt; socket/header hàn tay cũng được, ghi rõ.

# Firmware (sau khi prototype-ready, như AGENTS.md) — Arduino ESP32-C3, arduino-cli có sẵn
- USB CDC qua USB của module. Lúc boot in `HELLO pet v1`; `ping` → `PONG pet v1`.
- `M <trái> <phải>` tốc độ −100..100 mỗi bánh (1500 µs = 0, đảo chiều một bánh vì gắn đối xứng);
  `STOP`; `TRIM <L|R> <us>` chỉnh điểm dừng từng servo, lưu Preferences; `oled sh1106` /
  `oled ssd1306` chuyển driver, lưu Preferences; `BEEP`; `LED <r> <g> <b>`; `TOUCH` và `DIST <cm>`
  gửi lên.
- CỬ CHỈ nằm trong firmware, Mac chỉ gửi `STATE <idle|thinking|needs_user|done|error>`:
  thinking = nhích tới lui vài mm liên tục, đèn thở; needs_user = tiến 3 cm về phía người rồi dừng,
  bíp 2 tiếng, lặp mỗi 30 s; done = lùi 3 cm, mặt cười; error = rung tại chỗ, đèn đỏ; idle = thỉnh
  thoảng lắc nhẹ. Mọi cử chỉ ngắn, tổng quãng đường mỗi phút ≈ 0, và dừng ngay khi HC-SR04 thấy
  vật < 8 cm hoặc EDGE báo mất mặt bàn.
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
Đọc hết rồi mới làm. Plan ≤ 20 dòng rồi build luôn, không hỏi.

ĐỀ ĐẦY ĐỦ, là hợp đồng: $KICAD_HARNESS_ROOT/harness/kicad/prompts/claude-pet-rover.md — đọc khối trong ``` và làm đúng từng dòng. Tóm tắt:

LUẬT ĐỌC: chỉ workspace này, $KICAD_HARNESS_ROOT, $KICAD_HARNESS_BLOCKS (+ ../modules) và web datasheet/LCSC/JLC. Không mở folder run khác.

BOARD: Claude Pet Rover kiểu xe SMARS — hộp nằm ngang 4 bánh, 2 bánh có servo, mặt OLED 0.96" I2C, mắt HC-SR04, bụng TTP223, 2 LED RGB, còi, não ESP32-C3 SuperMini cắm socket, nói với Mac qua USB module. Không pin trên board.

CHỐT: C3 theo modules/esp32-c3-supermini. Nguồn: cổng USB-C NGUỒN RIÊNG (block usb-c-power, Rd 5.1k) từ pin dự phòng ≥ 2 A → rail V5 nuôi servo, LED, HC-SR04 và chân 5V module qua Schottky; V5 KHÔNG BAO GIỜ về USB module. 2 servo theo modules/sg90-continuous-module trên block servo-header (V+ giữa), 2 × 0.8 A kẹt, tụ lớn tính từ đó, đồng 2 mm. OLED theo modules/oled-0.96-i2c-module + 1 header I2C dự phòng. Touch theo modules/ttp223-module. 2 WS2812 theo block ws2812-chain (dịch mức). 1 LED status, 1 buzzer GPIO, header HC-SR04 (ECHO chia áp), 2 header EDGE_L/R. Ngoại vi 3V3 ≤ 40 mA.

HÌNH: board chữ nhật trên nóc xe, nhỏ nhất đủ chỗ, mày đề xuất; 4 lỗ M2; USB ra đuôi, OLED/HC-SR04/LED ra mũi. 2 lớp, JLCPCB Standard, một mặt.

PASS: mỗi fail trích được dòng trong đề/AGENTS/BLOCK/datasheet. ≤ 40 footprint, ≤ $8 chưa kể module.

FIRMWARE (Arduino C3): boot không xung; HELLO/PONG pet v1; M, STOP, TRIM lưu flash, BEEP, LED, oled sh1106; STATE <idle|thinking|needs_user|done|error> → cử chỉ trong firmware (nhích ít rồi dừng; dừng khi vật < 8 cm hoặc mất mặt bàn); mất kết nối 2 s → STOP. Unit test host. Build ra .bin, flash.json esp32. Chưa có .bin là chưa xong.

BÀN GIAO: publish --manufacturing, gate 0, README lời thường + phép thử: cắm pin, gửi STATE needs_user, xe tiến 3 cm, bíp 2 tiếng, dừng.
```
