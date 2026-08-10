"""Suite-wide default: the tscircuit parts engine is OFF so no test ever
touches the network (parts resolve only from parts.json + block pins —
contract §1). The e2e assertions account for the consequences honestly:
offline BOMs carry no supplier part numbers unless the lock provides them.

Tests use the REAL pinned toolchain on disk (exactly as dramapy demands real
ffmpeg — never mock circuitpy or the CLI); kicad-dependent tests skip, not
fail, when kicad-cli is absent."""

import os

os.environ["CIRCUIT_PARTS_ENGINE"] = "off"
