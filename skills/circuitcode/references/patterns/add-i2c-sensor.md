# Pattern: add an I2C sensor

**Trigger:** "read temperature", "add a sensor", "measure humidity/pressure/air
quality", "what's the CO2 in here".

**Why this exists:** the pull-up block is easy to forget and easy to
double-place, and both mistakes are invisible in every check we run.

**The three pieces:**

```tsx
import { I2cBus }       from "../blocks/i2c-bus/i2c-bus"
import { SensorBme280 } from "../blocks/sensor-bme280/sensor-bme280"

{/* exactly one bus block per I2C bus */}
<I2cBus rail="V3_3" pcbX={2} pcbY={8} schX={0} schY={4} />

<SensorBme280 rail="V3_3" pcbX={10} pcbY={0} schX={4} schY={0} />
```

1. The MCU block provides the SDA/SCL pins.
2. `i2c-bus` provides the pull-ups — **exactly one instance per bus.**
3. Each sensor block hangs off the same two nets.

**Pitfalls:**

- **Two `i2c-bus` blocks halve the pull-up resistance.** Two sensors share one
  bus block; they do not each get their own. `validate_board_law()` warns, but
  catch it while planning.
- **Address collisions.** Two devices at the same I2C address on one bus is a
  firmware-visible failure that no board check can see. If a user wants two of
  the same sensor, one needs its address strap moved — check the `BLOCK.md` for
  whether the block exposes that, and say so if it doesn't.
- **Only `sensor-bme280` exists today** (temperature, humidity, pressure —
  LCSC C92489). For light, VOC, or CO2, `board_plan()` returns the capability in
  `unavailable`. Report that honestly; a sensor block needs authoring and bench
  verification before it can go on a board someone pays for.
- Keep the sensor away from anything that heats — the regulator, the MCU. A
  temperature sensor next to an LDO measures the LDO. Nothing in the gauntlet
  will tell you this; it is a placement judgement you have to make.
