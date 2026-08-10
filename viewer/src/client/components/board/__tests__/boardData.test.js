import assert from "node:assert/strict";
import test from "node:test";
import {
  blockingWarnings,
  buildViewContextNote,
  lcscNumber,
  lcscUrl,
  normalizeParts,
  normalizeWarnings,
  parseBomCsv,
  parseCsv,
  partsByLcsc,
  warningNoteText,
} from "../boardData.js";

test("parseCsv handles quoted cells, embedded commas, and CRLF", () => {
  const rows = parseCsv('a,"b,c",d\r\n"say ""hi""",2,3\n\n');
  assert.deepEqual(rows, [
    ["a", "b,c", "d"],
    ['say "hi"', "2", "3"],
  ]);
});

test("parseBomCsv reads the JLCPCB fab columns case-insensitively", () => {
  const csv = [
    "Comment,Designator,Footprint,LCSC Part #",
    '100nF,"C1,C2",0402,C1525',
    "ESP32-S3-MINI-1,U1,ESP32-S3-MINI-1,C2913206",
  ].join("\n");
  const rows = parseBomCsv(csv);
  assert.equal(rows.length, 2);
  assert.deepEqual(rows[0], {
    comment: "100nF",
    designator: "C1,C2",
    footprint: "0402",
    lcsc: "C1525",
  });
  assert.equal(rows[1].lcsc, "C2913206");
  assert.deepEqual(parseBomCsv(""), []);
});

test("lcscNumber / lcscUrl parse the C-number and build the JLCPCB link", () => {
  assert.equal(lcscNumber("C165948"), "165948");
  assert.equal(lcscNumber(" c2040 "), "2040");
  assert.equal(lcscNumber("165948"), "");
  assert.equal(lcscNumber(""), "");
  assert.equal(lcscUrl("C165948"), "https://jlcpcb.com/partdetail/C165948");
  assert.equal(lcscUrl("nope"), "");
});

test("normalizeParts accepts {parts: []}, bare arrays, and id-keyed maps", () => {
  const fromList = normalizeParts({
    parts: [
      {
        id: "ldo-3v3",
        lcsc: "C6186",
        mfr: "AMS1117-3.3",
        package: "SOT-223",
        basic: true,
        stock: 1490000,
        unit_price_usd: 0.15,
        stock_checked: "2026-08-10",
      },
    ],
  });
  assert.equal(fromList.length, 1);
  assert.equal(fromList[0].lcsc, "C6186");
  assert.equal(fromList[0].basic, true);
  assert.equal(fromList[0].unitPriceUsd, 0.15);
  assert.equal(fromList[0].checked, "2026-08-10");

  const fromMap = normalizeParts({ "usb-c": { lcsc: "C165948", basic: false } });
  assert.equal(fromMap[0].id, "usb-c");
  assert.equal(fromMap[0].basic, false);

  const fromArray = normalizeParts([{ id: "x", unitPriceUsd: 1.2 }]);
  assert.equal(fromArray[0].unitPriceUsd, 1.2);

  assert.deepEqual(normalizeParts(null), []);
});

test("partsByLcsc indexes normalized parts by bare digits for BOM joins", () => {
  const map = partsByLcsc(normalizeParts([{ id: "a", lcsc: "C6186" }, { id: "b" }]));
  assert.equal(map.get("6186")?.id, "a");
  assert.equal(map.size, 1);
});

test("normalizeWarnings keeps the closed severity set and defaults unknown to warning", () => {
  const sidecar = {
    validation: {
      warnings: [
        { part: "U3.pin7", kind: "source_trace_not_connected_error", detail: "d", severity: "error" },
        { part: "R2", kind: "power_budget", severity: "weird" },
        { severity: "info" },
      ],
    },
  };
  const warnings = normalizeWarnings(sidecar);
  assert.equal(warnings[0].severity, "error");
  assert.equal(warnings[1].severity, "warning");
  assert.equal(warnings[2].severity, "info");
  // validation omitted when empty (contract) → [].
  assert.deepEqual(normalizeWarnings({}), []);
  assert.deepEqual(normalizeWarnings(null), []);
});

test("blockingWarnings = every error plus unverified_gerbers", () => {
  const sidecar = {
    validation: {
      warnings: [
        { part: "U3.pin7", kind: "source_trace_not_connected_error", severity: "error" },
        { part: "board", kind: "unverified_gerbers", severity: "warning" },
        { part: "R2", kind: "power_budget", severity: "warning" },
        { part: "board", kind: "kicad_unavailable", severity: "info" },
      ],
    },
  };
  assert.deepEqual(
    blockingWarnings(sidecar).map((w) => w.kind),
    ["source_trace_not_connected_error", "unverified_gerbers"],
  );
});

test("warningNoteText pre-fills the contract's fix-request shape", () => {
  assert.equal(
    warningNoteText({ part: "U3.pin7", kind: "source_trace_not_connected_error" }),
    "U3.pin7 (source_trace_not_connected_error): ",
  );
  assert.equal(warningNoteText({}), "board (warning): ");
});

test("buildViewContextNote names the tab and board; empty without a board", () => {
  assert.equal(
    buildViewContextNote({ board: "main", tab: "pcb" }),
    "[Viewer context: pcb view of board main]",
  );
  assert.equal(
    buildViewContextNote({ board: "main" }),
    "[Viewer context: board view of board main]",
  );
  assert.equal(buildViewContextNote({}), "");
});
