import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { CANT_BUILD_YET, STARTERS, buildStarterPrompt, starterById } from "./starters.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BLOCKS_DIR = path.resolve(HERE, "../../../../../packages/golden-blocks/blocks");

/** The released catalog, read from the repo rather than copied into a list
 *  here — a second copy of the catalog is a copy that goes stale silently. */
function releasedBlockIds() {
  return new Set(
    fs
      .readdirSync(BLOCKS_DIR, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name),
  );
}

test("STARTERS carries four board archetypes with complete cards", () => {
  assert.deepEqual(
    STARTERS.map((s) => s.id),
    ["big_button", "macropad", "air_monitor", "blinky_badge"],
  );
  for (const starter of STARTERS) {
    assert.ok(starter.title, `${starter.id} has a title`);
    assert.ok(starter.pitch, `${starter.id} has a pitch`);
    assert.ok(starter.parts, `${starter.id} previews the parts it will use`);
    assert.ok(starter.brief.length > 40, `${starter.id} brief is a real request`);
    assert.ok(!("emoji" in starter), `${starter.id} carries no emoji`);
  }
});

// The front-door bug this file exists to prevent: two of the original four
// cards named parts with no golden block behind them (a DRV8833 motor driver
// and an ESP32-S3), so half the one-tap options ended in a refusal the user
// had no way to predict. The blocks directory is the authority.
test("every starter is buildable out of released golden blocks", () => {
  const released = releasedBlockIds();
  assert.ok(released.size >= 8, "found the golden-block catalog on disk");
  for (const starter of STARTERS) {
    assert.ok(Array.isArray(starter.blocks) && starter.blocks.length, `${starter.id} declares its blocks`);
    for (const id of starter.blocks) {
      assert.ok(released.has(id), `${starter.id} needs block "${id}", which is not in ${BLOCKS_DIR}`);
    }
  }
});

test("no starter asks for a part class the library does not have", () => {
  // Named, not inferred: each of these is a `CANT_BUILD_YET` row, and a card
  // that mentions one is a card that ends in a refusal.
  const offCatalog = /motor|servo|pump|relay|esp32|wi-?fi|bluetooth|oled|lcd|display|screen/i;
  for (const starter of STARTERS) {
    const text = `${starter.title} ${starter.pitch} ${starter.parts} ${starter.brief}`;
    assert.ok(!offCatalog.test(text), `${starter.id} mentions an off-catalog part class`);
  }
});

test("starters stay inside the safety envelope (no mains, no bare batteries)", () => {
  for (const starter of STARTERS) {
    const text = `${starter.pitch} ${starter.brief}`.toLowerCase();
    assert.ok(!/mains|110v|120v|220v|230v/.test(text), `${starter.id} avoids mains`);
    assert.ok(!/lipo|18650|battery/.test(text), `${starter.id} avoids raw battery power`);
  }
});

test("CANT_BUILD_YET names the boundary, the reason, and a way forward", () => {
  assert.ok(CANT_BUILD_YET.length >= 4);
  for (const row of CANT_BUILD_YET) {
    assert.ok(row.ask, `${row.id} names the ask`);
    assert.ok(row.why.length > 20, `${row.id} explains why not`);
    // A dead end is a defect: every row has to leave the reader somewhere.
    assert.ok(row.instead.length > 10, `${row.id} offers the nearest thing we can do`);
  }
});

test("buildStarterPrompt = the brief plus the spec→build→fab ask", () => {
  const prompt = buildStarterPrompt(starterById("macropad"));
  assert.ok(prompt.startsWith("Design a 3×3 macropad"));
  assert.ok(prompt.includes("Spec the circuit first"));
  assert.ok(prompt.includes("JLCPCB"));
  assert.equal(buildStarterPrompt(null), "");
});

test("starterById resolves ids and misses safely", () => {
  assert.equal(starterById("air_monitor")?.title, "Desk air monitor");
  assert.equal(starterById("nope"), null);
});
