#!/usr/bin/env python3
"""Mirror JLCPCB's stocked-and-popular parts into a committed local catalog.

Why this exists (Dee, 2026-08-10): *"how do we have a list of most popular
electronic components so we don't have to reinvent the wheels."* JLCPCB's
**Basic** and **Preferred** libraries already are that list — they are the
parts the assembler keeps loaded, filtered by real demand and real stock, and
Basic parts carry no per-line feeder fee. Designing from them is both cheaper
and more likely to actually get built.

Why mirror rather than query live: jlcsearch cold queries measured 47-90s
(recon r5). Nothing that slow can sit inside a design loop, and a free public
service should not be in the user's critical path at all. So we snapshot it,
commit the snapshot, and refresh deliberately.

Usage:
    python packages/parts-catalog/fetch_catalog.py            # all categories
    python packages/parts-catalog/fetch_catalog.py ldos leds  # some

Writes packages/parts-catalog/catalog/<category>.json plus index.json.
Network only here — nothing else in the repo fetches parts at build time.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

BASE = "https://jlcsearch.tscircuit.com"
OUT_DIR = Path(__file__).resolve().parent / "catalog"
TIMEOUT_S = 180
PER_CATEGORY_LIMIT = 400

#: The categories worth carrying for the boards we build. Not all 53 — a
#: catalog nobody reads is just weight. Add one when a block needs it.
CATEGORIES = [
    "resistors", "capacitors", "leds", "diodes", "mosfets", "bjt_transistors",
    "ldos", "voltage_regulators", "boost_converters", "buck_boost_converters",
    "microcontrollers", "arm_processors", "risc_v_processors",
    "wifi_modules", "ble_modules", "ble_chips",
    "usb_c_connectors", "headers", "jst_connectors", "fpc_connectors",
    "wire_to_board_connectors", "battery_holders",
    "switches", "potentiometers", "relays", "fuses",
    "led_drivers", "led_with_ic", "oled_display", "lcd_display",
    "gas_sensors", "accelerometers", "gyroscopes", "microphones",
    "photo_diodes", "adcs", "dacs", "io_expanders",
    "analog_switches", "analog_multiplexers", "resistor_arrays",
]

#: Fields we keep. The upstream rows carry a long parametric tail plus a raw
#: `attributes` JSON blob; we store the identity, the sourcing facts, and the
#: parametrics that a chooser actually filters on.
KEEP = (
    "lcsc", "mfr", "package", "description", "stock", "is_basic",
    "is_preferred", "price", "price1",
    # parametric tails worth keeping where present
    "output_voltage_min", "output_voltage_max", "output_current_max",
    "dropout_voltage", "input_voltage_min", "input_voltage_max",
    "quiescent_current", "resistance", "capacitance", "voltage", "tolerance",
    "color", "wavelength", "forward_voltage", "current", "power",
    "channels", "interface", "frequency", "flash_size", "ram_size",
)


def fetch(url: str) -> dict | None:
    """GET JSON via curl. urllib breaks under this sandbox's TLS interception,
    so the repo convention is to shell out."""
    try:
        proc = subprocess.run(
            ["curl", "-s", "--max-time", str(TIMEOUT_S), url],
            capture_output=True, text=True, timeout=TIMEOUT_S + 15,
        )
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def normalize(row: dict) -> dict:
    out = {}
    for key in KEEP:
        value = row.get(key)
        if value not in (None, "", []):
            out[key] = value
    lcsc = out.pop("lcsc", None)
    if lcsc is None:
        return {}
    # Upstream strips the C; the rest of the repo speaks C-numbers.
    out["lcsc"] = f"C{lcsc}"
    if "price1" in out and "price" not in out:
        out["price"] = out.pop("price1")
    elif "price1" in out:
        out.pop("price1")
    if isinstance(out.get("price"), (int, float)):
        out["price"] = round(float(out["price"]), 6)
    return out


def fetch_category(name: str) -> list[dict]:
    payload = fetch(f"{BASE}/{name}/list.json?limit={PER_CATEGORY_LIMIT}")
    if not isinstance(payload, dict):
        return []
    rows: list[dict] = []
    for value in payload.values():
        if isinstance(value, list):
            rows = [r for r in value if isinstance(r, dict)]
            break
    kept = []
    for row in rows:
        # The whole point of the mirror: stocked parts the assembler already
        # keeps loaded. Everything else is a special order.
        if not (row.get("is_basic") or row.get("is_preferred")):
            continue
        if not row.get("stock"):
            continue
        norm = normalize(row)
        if norm:
            kept.append(norm)
    kept.sort(key=lambda r: (not r.get("is_basic"), -(r.get("stock") or 0)))
    return kept


def main(argv: list[str]) -> int:
    categories = argv[1:] or CATEGORIES
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    index: dict[str, dict] = {}
    total = 0
    for i, name in enumerate(categories, 1):
        started = time.time()
        rows = fetch_category(name)
        elapsed = time.time() - started
        if not rows:
            print(f"[{i}/{len(categories)}] {name}: no rows ({elapsed:.0f}s)", flush=True)
            continue
        (OUT_DIR / f"{name}.json").write_text(
            json.dumps(rows, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
        basic = sum(1 for r in rows if r.get("is_basic"))
        index[name] = {"parts": len(rows), "basic": basic}
        total += len(rows)
        print(f"[{i}/{len(categories)}] {name}: {len(rows)} parts "
              f"({basic} basic) in {elapsed:.0f}s", flush=True)
    if index:
        (OUT_DIR / "index.json").write_text(
            json.dumps(
                {
                    "source": BASE,
                    "fetched": time.strftime("%Y-%m-%d"),
                    "filter": "is_basic or is_preferred, in stock",
                    "categories": index,
                    "total_parts": total,
                },
                indent=1, sort_keys=True,
            ) + "\n",
            encoding="utf-8",
        )
    print(f"total {total} parts across {len(index)} categories")
    return 0 if index else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
