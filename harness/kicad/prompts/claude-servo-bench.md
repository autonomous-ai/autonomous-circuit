# Claude Servo Bench — the prompt (v1, 2026-09-30)

A board no engine has built before, chosen so a run cannot lean on an earlier workspace: the
blocks it needs (`servo-header`, a separate servo rail, a screw terminal) appear in no board under
`~/harnesses`. Written after the first Opus 5.5 run (2026-09-29) listed `~/harnesses`, found Astra's
finished Claude Deck and read its pinout, parts and engineering before drawing its own. The prompt
therefore also carries a hard rule on where the agent may read, and the transcript is checked
against it afterwards (every tool call with a path outside the three allowed roots counts).

Paste it whole into a **new folder** (⌘N → Project → New Folder) on any KiCad tile, permission mode
**full**. Two lines are meant to be edited before pasting: the servo count (4) and the footprint / cost
bar.

```
CHẠY THỬ TILE KICAD — đọc hết tin này trước khi làm bất cứ gì.
Board thử nghiệm build qua tile KiCad (pipeline KiCad-native của Circuit trong Harness).
Chủ dự án ĐÃ DUYỆT TRƯỚC plan bên dưới: trình bày plan tối đa 20 dòng rồi build luôn trong
cùng turn này. Không chờ "yes". Không hỏi gì cả — mọi thứ user có thể trả lời đã chốt ở đây;
phần còn lại là quyết định của mày, trong đúng khung bên dưới.

# LUẬT ĐỌC — vi phạm là bài không tính
Mày chỉ được đọc trong đúng ba chỗ: workspace này, $KICAD_HARNESS_ROOT (tài liệu kicadpy,
packages) và $KICAD_HARNESS_BLOCKS. KHÔNG ls ~/harnesses, KHÔNG mở folder của run khác,
KHÔNG đọc ~/projects, KHÔNG copy script/footprint/engineering từ board nào khác. Bản vẽ và
số chân lấy từ datasheet hoặc trang EasyEDA/LCSC của đúng mã linh kiện, ghi nguồn vào
engineering/sources.md. Tao đọc transcript và đếm từng lệnh đi ra ngoài ba chỗ đó.

# Claude Servo Bench — cái board
Một board để bàn cắm USB vào Mac, cho coding agent trên máy điều khiển 4 con servo hobby
(loại 3 dây, kiểu SG90 / MG90S) qua USB serial. Board là toàn bộ thiết bị: não, cổng servo,
nguồn cho servo. Không màn, không radio, không pin.

# Chuẩn tham chiếu: Raspberry Pi Pico
Cái gì Pico được làm thì board này được làm. Bất kỳ check nào mày tự viết mà Pico rớt thì
check đó SAI, không phải board sai.

# Các quyết định ĐÃ CHỐT. Không mở lại, không đề xuất thay thế, không "cải tiến":
- Não: RP2040, hệ tối thiểu đúng như $KICAD_HARNESS_BLOCKS/rp2040-core/BLOCK.md (thạch anh,
  flash, tụ decoupling, RESET, BOOTSEL, SWD). DVDD lấy từ VREG_VOUT như block ghi.
- Nguồn logic và data: USB-C ($KICAD_HARNESS_BLOCKS/usb-c-data/BLOCK.md, Rd 5.1k mỗi chân CC)
  cấp cho LDO 3.3 V ($KICAD_HARNESS_BLOCKS/ldo-3v3/BLOCK.md). USB chỉ nuôi logic.
  Ngân sách USB 2.0 thiết bị thường: 100 mA trước cấu hình, 500 mA sau, tụ trực tiếp trên
  VBUS ≤ 10 µF. KHÔNG IC đọc CC, KHÔNG supervisor, KHÔNG load switch.
- Nguồn servo: rail V_SERVO RIÊNG, vào từ cọc vít 2 chân bước 5.08 mm (mã mua được, dòng
  định mức ≥ 5 A), điện áp 5–6 V DC từ nguồn bàn hoặc BEC. V_SERVO KHÔNG nối vào VBUS ở bất
  kỳ chỗ nào — không jumper, không diode, không "tiện thì nối". GND chung. Lý do: servo kẹt
  kéo hơn 500 mA, cổng USB của Mac không được thấy dòng đó.
- Bảo vệ đầu vào servo: mày chọn MỘT trong hai và ghi rõ trong README: (a) không bảo vệ,
  in dấu +/− to trên silk, hoặc (b) P-MOSFET chống ngược cực. Không diode nối tiếp (rớt áp).
- Tụ lớn trên V_SERVO: bắt buộc có, mày tính giá trị từ số dòng đỉnh của servo trong
  datasheet mã mày chọn làm tham chiếu, ghi phép tính vào engineering/power.md. Tụ này
  nằm trên V_SERVO, không dính gì tới giới hạn 10 µF của VBUS.
- Cổng servo: 4 header 3 chân đúng $KICAD_HARNESS_BLOCKS/servo-header/BLOCK.md — V+ LUÔN ở
  chân giữa, tất cả cùng hướng, in số kênh và thứ tự GND/V+/SIG lên silk. Tín hiệu 3.3 V từ
  GPIO đi thẳng, có hoặc không có trở nối tiếp là quyết định của mày, ghi lý do.
- Đường đồng V_SERVO và GND servo: tính bề rộng cho dòng tổng 4 servo kẹt cùng lúc theo số
  datasheet, ghi phép tính. GND servo về cọc vít không đi chung đường hẹp với GND logic.
- Đèn: một LED báo trạng thái ($KICAD_HARNESS_BLOCKS/status-led/BLOCK.md) trên một GPIO.
- Nút: chỉ RESET và BOOTSEL. Không thêm.
- Cơ khí: 4 lỗ M3 ở bốn góc.

Bảng chân — TẠM THỜI, ghi vào product.json là provisional kèm câu "firmware must follow this
table or the board changes in a later revision":
    SERVO1=GPIO2, SERVO2=GPIO3, SERVO3=GPIO4, SERVO4=GPIO5, STATUS_LED=GPIO16.
    SWCLK/SWDIO/GND trên header debug 3 chân. Mọi pad header in số GPIO / số kênh lên silk.

# Cái gì được tính là "pass" cho 7 mục trong manufacturing.json
Mỗi mục fail PHẢI trích được một dòng trong tin này, trong AGENTS.md, trong BLOCK.md, hoặc
một con số trong datasheet. "Chưa chứng nhận", "chưa đo", "chưa có firmware" KHÔNG phải fail —
đó là hardwareTested=false và một dòng trong bringup.md. Cụ thể:
- power: hai ngân sách tách riêng — rail logic trong 500 mA USB, rail servo theo cọc vít và
  số datasheet; tụ VBUS ≤ 10 µF; V_SERVO không chạm VBUS. Hết.
- protection: ESD trên D+/D− đúng như block usb-c-data; quyết định (a)/(b) cho đầu vào servo
  được ghi rõ.
- pinout: từng chân đối chiếu datasheet; pad parity schematic–PCB sạch; V+ ở chân giữa cả 4 cổng.
- thermal: mọi thứ mang dòng servo (cọc vít, đường đồng, MOSFET nếu có) tính ở dòng kẹt
  tổng, ghi số.
- assembly, fabricator, bringup: như AGENTS.md.
Cách đóng một finding "chưa chứng minh được" là viết phép thử vào bringup.md, KHÔNG PHẢI
thêm linh kiện. Thêm bất kỳ IC nào ngoài danh sách chốt để qua check = sai.

# Linh kiện, giá, hình dáng
- parts.json ghi đúng hãng, MPN, package, mã LCSC cho từng part xưởng ráp, lấy từ listing
  sống — không bịa. Ưu tiên JLCPCB Basic; tra và ghi Basic/Extended + tồn kho tại thời điểm tra;
  cái nào không tra được ghi "chưa tra". Không ghi tồn kho mày chưa tra.
- Mục tiêu: ≤ 30 footprint, linh kiện ≤ 6 USD/board giá LCSC lẻ. Vượt là phải ghi lý do
  trong README, từng con một.
- Hình dáng: nhỏ nhất mà 4 header servo cùng hướng + cọc vít cho phép; USB một cạnh, cọc vít
  cạnh đối diện, servo một hàng. 2 lớp, JLCPCB, tier ráp Standard, linh kiện một mặt.

# Firmware (viết sau khi board prototype-ready, như AGENTS.md)
- USB Serial/JTAG, dòng lệnh text, mỗi lệnh một dòng:
  `ping` → `PONG servo v1`; lúc boot in `HELLO servo v1`.
  `S<n> <us>` đặt xung kênh n (1–4) theo micro giây, chỉ nhận 500–2500, ngoài khoảng trả `ERR`.
  `OFF <n>` ngắt xung kênh n. `ALL <us>` cho cả 4. `STATUS` in 4 kênh.
- Lúc boot, sau reset và khi flash trống: KHÔNG có xung nào ra 4 kênh. Servo chỉ nhận xung khi
  Mac ra lệnh. Đây là yêu cầu phần cứng lẫn firmware: GPIO servo mặc định input/pull-down.
- Unit test trên host cho parser + giới hạn xung. Compile bằng đúng lệnh ghi trong README.

# Cách làm việc
- Các block là KIẾN THỨC kỹ thuật, không phải sheet. Đọc BLOCK.md lấy bảng chân, giá trị, ghi
  chú layout, rồi TỰ VẼ schematic và PCB KiCad; symbol/footprint copy vào design/ có ghi nguồn.
- Publish tới khi verdict prototype-ready. Nếu sau 3 vòng review vẫn đỏ: DỪNG, publish với
  fab.ready=false, và báo đúng một câu: blocker là gì, trích rule nào, cần tao quyết cái gì.
- Thật thà: block rp2040-core mới compile-verified, CHƯA có board nào lên nguồn. Ghi rõ.

# Bàn giao
Project KiCad trong design/; publish kèm packet nguyên mẫu
("$KICAD_HARNESS_PYTHON" -m kicadpy.publish --manufacturing design/main.kicad_pro); bằng
chứng trong engineering/ và manufacturing.json; README.md bằng lời thường nói (1) mày đã giả
định gì, (2) người phải kiểm tra gì trước khi đặt, (3) não chưa từng bay, (4) một phép thử
đầu tiên trên bàn: cắm nguồn 5 V vào cọc vít, cắm một servo vào kênh 1, gửi `S1 1500`, servo
về giữa, và điện áp VBUS trên cổng USB không đổi. Người đọc không đọc schematic.
```
