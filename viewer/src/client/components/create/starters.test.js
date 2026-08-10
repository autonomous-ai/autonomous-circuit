import assert from "node:assert/strict";
import test from "node:test";
import { STARTERS, buildStarterPrompt, starterById } from "./starters.js";

test("STARTERS carries the four v1 board archetypes with complete cards", () => {
  assert.deepEqual(
    STARTERS.map((s) => s.id),
    ["macropad", "air_monitor", "motor_driver", "blinky_badge"],
  );
  for (const starter of STARTERS) {
    assert.ok(starter.title, `${starter.id} has a title`);
    assert.ok(starter.pitch, `${starter.id} has a pitch`);
    assert.ok(starter.brief.length > 40, `${starter.id} brief is a real request`);
  }
});

test("starters stay inside the safety envelope (no mains, no bare batteries)", () => {
  for (const starter of STARTERS) {
    const text = `${starter.pitch} ${starter.brief}`.toLowerCase();
    assert.ok(!/mains|110v|120v|220v|230v/.test(text), `${starter.id} avoids mains`);
    assert.ok(!/lipo|18650|battery/.test(text), `${starter.id} avoids raw battery power`);
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
