"""Suite-wide: no test touches the network, and the parts engine stays off —
the same discipline as circuitpy's suite. routerlib itself never shells out,
but circuitpy.toolchain is imported for the ruler and reads the pinned
toolchain from disk."""

import os

os.environ.setdefault("CIRCUIT_PARTS_ENGINE", "off")
