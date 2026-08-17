// Open the whole board workspace on a real example project, with a real
// server behind it and nothing on the wire.
//
// The edit loop is five pieces in a line — canvas → `BoardWorkspace` →
// `usePlacementEditor` → HTTP → `planSourceWrite` — and every bug this suite
// exists for lived in a *join*, not in a piece. `PcbCanvas` never imported
// `canvasPointer.js`; `snapDelta` was used and never imported (8fb33cd). Both
// pieces on either side of those joins were fully tested and green. So this
// mounts the workspace the way `main.jsx` does and drives it by clicking,
// rather than composing the parts itself — a harness that wires the pieces
// together cannot fail when the app forgets to.
//
// Two things are stubbed, both at the transport seam and neither in the middle
// of the loop:
//
//   - `fetch` serves the example directory out of memory and answers
//     `POST /api/board_source_write` with the SERVER's own `planSourceWrite`.
//     The compare-and-swap, the byte-range checks and the SOURCE_CHANGED
//     refusal are the shipped ones, so a client edit the real server would
//     reject fails here too.
//   - `transport` answers the two polls the workspace makes on mount
//     (`build_status`, `build_revisions`), which have nothing to do with
//     editing and would otherwise `fetch` a route that does not exist.
//
// The catalog is not hand-written either: `scanProjectCatalog` is the real
// scanner, pointed at `examples/<name>/`, so the entry shape and every
// `?v=<mtime>-<size>` token are the ones the app gets in production.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { click, flush, mount, settle } from "../../../test/render.js";
import { scanProjectCatalog } from "../../../../server/circuit/catalog.mjs";
import { planSourceWrite } from "../../../../server/circuit/http.mjs";
import { buildBoardIndex } from "../../../lib/boardIndex.js";
import { selectBoardEntries } from "../../../lib/boardModel.js";
import { bindPlacements, parseBoardSource } from "../boardSource.js";

const REPO = path.resolve(fileURLToPath(new URL("../../../../../..", import.meta.url)));

/** Loud, not skipped. A render proof that opts out when the board is missing
 *  is the false floor this suite has shipped once already. */
export function requireExample(name) {
  const dir = path.join(REPO, "examples", name);
  for (const file of ["boards/main.circuit.json", "boards/main.tsx", "boards/main.board.json"]) {
    assert.ok(fs.existsSync(path.join(dir, file)), `missing examples/${name}/${file}`);
  }
  return dir;
}

/**
 * The Circuit server, in memory.
 *
 * `board_source_write` is the real one minus the filesystem: same planner,
 * same refusals, same "return the file as it now stands". `agentWrites` is the
 * other half of what makes that honest — the file can change under the client
 * between its read and its write, which is the whole reason the command is a
 * compare-and-swap.
 */
function fakeServer({ projectDir, projectId, boardFile }) {
  const source = fs.readFileSync(path.join(projectDir, boardFile), "utf8");
  const overrides = new Map([[boardFile, source]]);
  const requests = [];

  const server = {
    requests,
    /** Every board_source_write body, parsed, oldest first. */
    get writes() {
      return requests.filter((one) => one.command === "board_source_write").map((one) => one.body);
    },
    /** The board file as this server now holds it. */
    get source() {
      return overrides.get(boardFile);
    },
    /** What an agent editing the same file mid-session does to it. */
    agentWrites(text) {
      overrides.set(boardFile, text);
    },
    /** Refuse the next write with this `{code, message}`, once. */
    refuseNextWrite: null,
    /** Every `board_fast_check` body, oldest first — the gate is asked for a
     *  verdict after an edit, and "was it asked at all" is the join that was
     *  missing for a day: the command existed, worked, and no client code
     *  named it. */
    get checks() {
      return requests.filter((one) => one.command === "board_fast_check").map((one) => one.body);
    },
    /** What the gate answers next. Shaped like the real one. */
    nextCheck: {
      ok: true,
      status: "legal",
      reason: "",
      geometry: "as_built",
      counts: { error: 0, warning: 2, info: 7 },
      warnings: [],
      moves: [],
      checked: ["circuit.json element scan"],
      notChecked: [{ what: "the copper pour", why: "cutouts were computed for the old position" }],
      elapsedMs: 120,
    },
  };

  const respond = (status, body) =>
    new Response(typeof body === "string" ? body : JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    });

  globalThis.fetch = async (input, init = {}) => {
    const url = String(input);
    const method = String(init.method || "GET").toUpperCase();
    const [pathname] = url.split("?");

    if (method === "POST") {
      const command = pathname.replace("/api/", "");
      const body = JSON.parse(init.body || "{}");
      requests.push({ method, pathname, command, body });
      if (command === "board_net_widths") {
        // Measured server-side by a Python leg that fans 180 bearings out of
        // every pad; the numbers here are terminal-keyboard's real ones so the
        // UI is exercised against a shape it will actually receive.
        return respond(200, {
          ok: true,
          nets: (body.nets || []).map((net) => ({
            net,
            rail: true,
            ceiling_mm: 0.4,
            ceiling_at: "U3.IOVDD6",
            ceiling_capped: false,
            narrowest_mm: 0.2,
            declared_mm: null,
            pads: 25,
          })),
          powerFloorMm: 0.5,
          minTraceMm: 0.1,
        });
      }
      if (command === "board_fast_check") {
        const answer = server.nextCheck;
        if (answer?.throws) return respond(500, { code: "GATE_DOWN", message: answer.throws });
        return respond(200, { ...answer, moves: body.moves || [] });
      }
      if (command !== "board_source_write") return respond(404, { code: "NOT_FOUND", message: command });
      if (server.refuseNextWrite) {
        const refusal = server.refuseNextWrite;
        server.refuseNextWrite = null;
        return respond(409, refusal);
      }
      const current = overrides.get(body.file);
      if (current === undefined) return respond(400, { code: "INVALID_ARGUMENT", message: "not a board source" });
      const planned = planSourceWrite(current, body.edits, body.sourceLength);
      if (!planned.ok) {
        return respond(planned.code === "SOURCE_CHANGED" ? 409 : 400, planned);
      }
      overrides.set(body.file, planned.text);
      return respond(200, { file: body.file, text: planned.text, sourceLength: planned.text.length });
    }

    requests.push({ method, pathname });
    const prefix = `/projects/${projectId}/`;
    if (!pathname.startsWith(prefix)) return respond(404, "not this server");
    const rel = decodeURIComponent(pathname.slice(prefix.length));
    if (overrides.has(rel)) return respond(200, overrides.get(rel));
    const abs = path.join(projectDir, rel);
    if (!fs.existsSync(abs)) return respond(404, "no such file");
    return respond(200, fs.readFileSync(abs, "utf8"));
  };

  return server;
}

/**
 * Mount the workspace on `examples/<example>`, switch to the PCB tab and turn
 * move mode on — the state every edit-loop test starts from.
 *
 * The tab and the tool are *clicked*, not passed in as props, because "does
 * the move-parts button reach move mode" is one of the joins under test.
 */
export async function openWorkspace({ example = "hydrate-coaster", projectId = "p1" } = {}) {
  const projectDir = requireExample(example);
  const boardFile = "boards/main.tsx";
  const server = fakeServer({ projectDir, projectId, boardFile });

  const { setTransport, resetTransport } = await import("../../../lib/transport.ts");
  setTransport({
    build_status: async () => null,
    build_revisions: async () => ({ revisions: [], trend: null }),
  });

  const { useProjectsStore } = await import("../../../store/projects.ts");
  useProjectsStore.setState({
    projects: [{ id: projectId, name: example, createdAt: 0, updatedAt: 0, hasModel: true }],
    currentProjectId: projectId,
    status: "ready",
  });

  const catalog = scanProjectCatalog({ projectDir, projectId });
  const boardEntries = selectBoardEntries(catalog);
  assert.equal(boardEntries.length, 1, `expected one board entry in examples/${example}`);

  const BoardWorkspace = (await import("../BoardWorkspace.jsx")).default;
  const ui = mount(BoardWorkspace, {
    manifestRevision: catalog.revision || 1,
    boardEntries,
    partsEntry: null,
    catalogHydrated: true,
  });

  // The sidecar, the IR, parts.json, product.json and the board source load in
  // a chain: each one's `setState` runs the next one's effect. One flush per
  // link, plus a macrotask so anything on a timer lands too.
  for (let i = 0; i < 8; i += 1) {
    await flush();
    await settle();
  }

  const find = (selector) => ui.container.querySelector(selector);
  const tab = [...ui.container.querySelectorAll('[data-slot="board-tab"]')].find(
    (node) => node.textContent.trim() === "PCB",
  );
  assert.ok(tab, "no PCB tab in the workspace");
  click(tab);
  await flush();

  const editTool = find('[data-tool="edit"]');
  assert.ok(editTool, "no move-parts tool on the PCB viewport rail");
  click(editTool);
  for (let i = 0; i < 4; i += 1) {
    await flush();
    await settle();
  }

  const bar = find('[data-slot="placement-edit-bar"]');
  assert.ok(bar, "clicking the move-parts tool did not open the edit strip");
  assert.equal(bar.dataset.ready, "true", `the board file never opened: ${bar.textContent}`);

  const source = fs.readFileSync(path.join(projectDir, boardFile), "utf8");
  const index = buildBoardIndex(
    JSON.parse(fs.readFileSync(path.join(projectDir, "boards/main.circuit.json"), "utf8")),
  );
  const placements = bindPlacements(parseBoardSource(source).placements, index);

  return {
    ui,
    server,
    source,
    index,
    placements,
    boardFile,
    find,
    /** Text of the element at `selector`, or "" — an absent panel reads as
     *  silence rather than throwing three lines from the assertion. */
    text: (selector) => find(selector)?.textContent ?? "",
    get canvas() {
      const node = find('[data-slot="pcb-canvas"]');
      assert.ok(node, "the PCB canvas is not on screen");
      return node;
    },
    /**
     * The pan/zoom the canvas is actually painting, read back off its own
     * `<g transform>`. Not a callback: what a pointer at a client coordinate
     * hits is decided by the transform on screen, so that is what the test
     * converts through.
     */
    get view() {
      const group = [...this.canvas.querySelectorAll("svg > g")]
        .map((node) => /translate\(([-\d.]+) ([-\d.]+)\) scale\(([-\d.]+) /.exec(node.getAttribute("transform") || ""))
        .find(Boolean);
      assert.ok(group, "the canvas painted no board — nothing to point at");
      return { tx: Number(group[1]), ty: Number(group[2]), scale: Number(group[3]) };
    },
    /** Board millimetres → the client pixel a pointer must arrive at. */
    at(x, y) {
      const { tx, ty, scale } = this.view;
      return { clientX: tx + x * scale, clientY: ty - y * scale };
    },
    async settle(times = 3) {
      for (let i = 0; i < times; i += 1) {
        await flush();
        await settle();
      }
    },
    close() {
      ui.unmount();
      resetTransport();
    },
  };
}
