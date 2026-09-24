# Claude Pet — the prompt (v2, 2026-09-23)

The board every engine gets measured on: the same words to Claude (harness-5, -6, -14), Astra
(harness-9, -11) and Grok 4.7 (harness-12). Paste it whole into a new harness on any KiCad tile
(`autonomous/kicad`, `-grok`, `-claude`); on the grok tile the daemon refuses a first prompt in the
dialog, so paste it into the pane.

Rewritten after harness-11 (Astra, 8 h 45, 79 footprints) and harness-12 (Grok, $90, never
ready) from four scars: a locked part at a voltage no datasheet backs (WS2812 at 3.3 V), a USB
bar the agent set itself (suspend ≤ 2.5 mA) and then bought hardware to pass, a BOM that tripled,
and a deviation from the locked block (external 1.1 V buck). Three lines are meant to be edited
before pasting: the M2 holes (drop if the case snaps), the ≤ 30 footprint / ≤ $5 bar (from
harness-6/9's 23 parts), and the LED routes (a)/(b).

Numbers the runs produced are in `.claude/BOARD.md` (local) and the company report doc.

```
CHẠY THỬ TILE KICAD — đọc hết tin này trước khi làm bất cứ gì.

Board thử nghiệm build qua tile KiCad (pipeline KiCad-native của Circuit trong Harness).
Chủ dự án ĐÃ DUYỆT TRƯỚC plan bên dưới: trình bày plan tối đa 20 dòng rồi build luôn trong
cùng turn này. Không chờ "yes". Không hỏi gì cả — mọi thứ user có thể trả lời đã chốt ở đây;
phần còn lại là quyết định của mày, trong đúng khung bên dưới.

# Claude Pet — cái board

Một con pet để bàn cỡ lòng bàn tay. Mặt nó là màn hình màu 1.54"; nó nằm cạnh bàn phím, cắm
USB vào máy tính vĩnh viễn, và hiện coding agent trên máy đang làm gì. Board là toàn bộ thiết
bị trừ màn hình và vỏ in 3D.

# Chuẩn tham chiếu: Raspberry Pi Pico

Cái gì Pico được làm thì board này được làm. Pico bật từ MỌI cổng USB (USB-A, hub, cáp A–C),
Pico không chứng minh dòng suspend 2.5 mA, Pico dùng regulator nội của RP2040. Board này
cũng vậy. Bất kỳ check nào mày tự viết mà Pico rớt thì check đó SAI, không phải board sai.

# Các quyết định ĐÃ CHỐT. Không mở lại, không đề xuất thay thế, không "cải tiến":

- Não: RP2040, hệ tối thiểu đúng như $KICAD_HARNESS_BLOCKS/rp2040-core/BLOCK.md (thạch anh,
  flash, tụ decoupling, RESET, BOOTSEL, SWD). DVDD lấy từ VREG_VOUT như block ghi — KHÔNG
  buck ngoài, KHÔNG regulator ngoài cho lõi. Không Wi-Fi, không BLE, không radio.
- Nguồn và data: USB-C ($KICAD_HARNESS_BLOCKS/usb-c-data/BLOCK.md, Rd 5.1k mỗi chân CC như
  block) cấp cho LDO 3.3 V ($KICAD_HARNESS_BLOCKS/ldo-3v3/BLOCK.md). Nguồn duy nhất. Không pin,
  không sạc, không điện lưới, không bao giờ.
- Ngân sách nguồn tính theo USB 2.0 thiết bị thường: 100 mA trước cấu hình, 500 mA sau cấu
  hình, tụ trực tiếp trên VBUS ≤ 10 µF. KHÔNG thêm IC đọc CC, KHÔNG supervisor, KHÔNG load
  switch, KHÔNG bắt máy quảng cáo 1.5 A. Board phải bật từ cổng USB-A qua cáp A–C.
- Tải lớn (đèn nền màn, LED) đi qua GPIO có pull-down để MẶC ĐỊNH TẮT khi reset, khi flash
  trống và khi chạy BOOTSEL — đó là toàn bộ phần cứng cho tiết kiệm điện. Phần còn lại là
  firmware: ghi yêu cầu vào product.json, KHÔNG chặn packet vì firmware chưa có.
- Mặt: màn 1.54" 240x240 IPS ST7789, SPI, module 8 chân phổ biến (GND VCC SCL SDA RES DC CS
  BLK), cắm header. Màn đứng cách board trong vỏ, không dán phẳng. Đèn nền qua transistor để
  BLK là GPIO, không phải tải 100 mA treo lên chân.
- Đèn: hai LED RGB điều khiển riêng từng con. Mày chọn linh kiện, với đúng MỘT điều kiện:
  datasheet của đúng mã mua được phải bảo đảm chạy ở điện áp mày cấp cho nó. Hai đường được
  phép: (a) LED địa chỉ kiểu WS2812 cấp 5 V + một con dịch mức 3.3→5 V trên đường data, hoặc
  (b) LED địa chỉ có datasheet ghi rõ VDD min ≤ 3.3 V, cấp từ V3_3, điện trở nối tiếp data.
  Không LED rời + driver + 6 điện trở. Không tự lock cái gì datasheet không nói.
- Cảm ứng: một module TTP223 trên header 3 chân (VCC GND OUT). Output số nối thẳng GPIO.
- Nút: chỉ RESET và BOOTSEL. Không thêm.
- Cơ khí: 2 lỗ M2 ở hai góc xa USB. (Bỏ dòng này nếu vỏ dùng ngàm.)

Bảng chân — TẠM THỜI, ghi vào product.json là provisional kèm câu "firmware must follow this
table or the board changes in a later revision":

    RP2040 hardware SPI0: LCD_SCK=GPIO18, LCD_MOSI=GPIO19, LCD_CS=GPIO17,
    LCD_DC=GPIO20, LCD_RST=GPIO21, LCD_BLK=GPIO22, LED_DATA=GPIO15, TOUCH=GPIO14.
    SWCLK/SWDIO/GND trên header debug 3 chân. Mọi pad header in số GPIO lên silk.

# Cái gì được tính là "pass" cho 7 mục trong manufacturing.json

Mỗi mục fail PHẢI trích được một dòng trong tin này, trong AGENTS.md, trong BLOCK.md, hoặc
một con số trong datasheet. "Chưa chứng nhận", "chưa đo", "chưa có firmware" KHÔNG phải fail —
đó là hardwareTested=false và một dòng trong bringup.md. Cụ thể:
- power: tổng dòng từng rail có đủ headroom trong 500 mA; tụ VBUS ≤ 10 µF; tải lớn mặc định
  tắt. Hết. Không suspend, không USB-IF, không Type-C current.
- protection: ESD trên D+/D−, đúng như block usb-c-data.
- pinout: từng chân đối chiếu datasheet; pad parity schematic–PCB sạch.
- thermal: LDO tính ở 500 mA đầu vào tệ nhất, ghi số.
- assembly, fabricator, bringup: như AGENTS.md.
Cách đóng một finding "chưa chứng minh được" là viết phép thử vào bringup.md, KHÔNG PHẢI
thêm linh kiện. Thêm bất kỳ IC nào ngoài danh sách chốt để qua check = sai.

# Linh kiện, giá, hình dáng

- parts.json ghi đúng hãng, MPN, package, mã LCSC cho từng part xưởng ráp, lấy từ listing
  sống — không bịa. Ưu tiên JLCPCB Basic; tra và ghi Basic/Extended + tồn kho tại thời điểm tra;
  cái nào không tra được ghi "chưa tra". Không ghi tồn kho mày chưa tra.
- Mục tiêu: ≤ 30 footprint, linh kiện ≤ 5 USD/board giá LCSC lẻ. Vượt là phải ghi lý do
  trong README, từng con một.
- Hình dáng: nhỏ nhất mà màn cho phép — màn là mặt và quyết định outline; hệ RP2040 tối thiểu
  nằm sau lưng nó. 2 lớp, JLCPCB, tier ráp Standard, linh kiện một mặt.

# Cách làm việc

- Các block là KIẾN THỨC kỹ thuật, không phải sheet. Đọc BLOCK.md lấy bảng chân, giá trị, ghi
  chú layout, rồi TỰ VẼ schematic và PCB KiCad; symbol/footprint copy vào design/ có ghi nguồn.
  Kiểm tra từng số chân với datasheet trước khi tin. Màn và module cảm ứng là module hoàn
  chỉnh: không thêm linh kiện tích cực bên ngoài.
- Publish tới khi verdict prototype-ready. Nếu sau 3 vòng review vẫn đỏ: DỪNG, publish với
  fab.ready=false, và báo đúng một câu: blocker là gì, trích rule nào, cần tao quyết cái gì.
  Không tự đẻ thêm phần cứng để vượt.
- Thật thà: block rp2040-core mới compile-verified, CHƯA có board nào lên nguồn. Board này có
  thể là cái đầu tiên. Ghi rõ, không nói giảm.

# Bàn giao

Project KiCad trong design/; publish kèm packet nguyên mẫu
("$KICAD_HARNESS_PYTHON" -m kicadpy.publish --manufacturing design/main.kicad_pro); bằng
chứng trong engineering/ và manufacturing.json; README.md bằng lời thường nói (1) mày đã giả
định gì, (2) người phải kiểm tra gì trước khi đặt, (3) não chưa từng bay, (4) board này lệch
Pico ở chỗ nào, nếu có. Người đọc không đọc schematic.
```
