// boardActions — what you can do with a finished board, as data. DOM-free and
// transport-free so node:test covers the gating rules directly.
//
// Vibe's equivalent is the FloatingToolBar's Slice plate / Publish project /
// Open in <slicer> trio (CadWorkspace.js:7492). The shape worth copying is that
// each action computes its own `can…` gate and its own live label rather than
// the parent branching at render time; the actions themselves do not port —
// a board is not sliced and there is no plate.
//
// Our three:
//   Open in KiCad    the exact analogue of "Open in OrcaSlicer"
//   Packet           the files a fab actually needs
//   Order at JLCPCB  the exact-clicks walkthrough, gated on fab-readiness
//
// One honest difference from the donor: Vibe is a Tauri app and shells out
// (`printer_open_in_studio`). Circuit is a web app and the server implements no
// shell-open — the whole slicer/printer command family was deleted in the port
// (viewer/src/server/circuit/http.mjs). So "Open in KiCad" downloads the
// project and says what to do with it. See VIBE-NOTES §7 for the server change
// that would make it a real hand-off.

/** The fab quote page an ORDER.md walkthrough ends at. */
export const JLCPCB_QUOTE_URL = "https://cart.jlcpcb.com/quote";

/**
 * The packet members present on this board, in the order a fab consumes them.
 * @returns {Array<{id: string, label: string, url: string, filename: string, hint: string}>}
 */
export function packetDownloads(stem, artifact) {
  const name = String(stem || "board");
  return [
    {
      id: "gerbers",
      label: "Gerbers",
      url: String(artifact?.gerbersUrl || ""),
      filename: `${name}-gerbers.zip`,
      hint: "copper, mask, silkscreen and drill — what the fab loads",
    },
    {
      id: "bom",
      label: "BOM",
      url: String(artifact?.bomUrl || ""),
      filename: `${name}-bom.csv`,
      hint: "Comment, Designator, Footprint, LCSC Part #",
    },
    {
      id: "cpl",
      label: "CPL",
      url: String(artifact?.cplUrl || ""),
      filename: `${name}-cpl.csv`,
      hint: "placement centroids — assembly only",
    },
    {
      id: "kicad",
      label: "KiCad project",
      url: String(artifact?.kicadProjectUrl || ""),
      filename: `${name}-kicad.zip`,
      hint: "schematic + board + project",
    },
    {
      id: "glb",
      label: "3D model",
      url: String(artifact?.glbUrl || ""),
      filename: `${name}.glb`,
      hint: "board and parts, for mechanical fit",
    },
  ].filter((entry) => entry.url);
}

/**
 * The top-bar actions and their gates.
 *
 * `kind` tells the caller how to run it: `download` hands the URL to the
 * browser, `tab` switches the workspace to a tab, `link` opens a new window.
 * `enabled: false` always comes with a `reason` — a greyed button that will not
 * say why is worse than no button.
 *
 * @param {{stem?: string, artifact?: object|null, sidecar?: object|null}} input
 */
export function boardActions({ stem = "board", artifact = null, sidecar = null } = {}) {
  const kicadUrl = String(artifact?.kicadProjectUrl || "");
  const packet = packetDownloads(stem, artifact);
  const fabReady = sidecar?.fab?.ready === true;
  const hasOrder = Boolean(artifact?.orderUrl);

  return [
    {
      id: "open-kicad",
      kind: "download",
      label: "Open in KiCad",
      url: kicadUrl,
      filename: `${stem || "board"}-kicad.zip`,
      // Deliberately NOT gated on fab-readiness. Withholding the fab packet
      // stops someone paying for an unverified board; withholding the KiCad
      // project would only stop them looking at it — and a board that is not
      // ready is exactly the one an engineer wants open in a real tool.
      enabled: Boolean(kicadUrl),
      reason: kicadUrl ? "" : "No kicad-project.zip in the packet yet — rebuild the board to export one",
      note: `Unzip and open ${stem || "board"}.kicad_pro`,
    },
    {
      id: "packet",
      kind: "menu",
      label: "Packet",
      items: packet,
      enabled: packet.length > 0,
      reason: packet.length ? "" : "No build artifacts yet",
    },
    {
      id: "order",
      kind: "tab",
      target: "fab",
      label: "Order at JLCPCB",
      enabled: fabReady && hasOrder,
      reason: !hasOrder
        ? "ORDER.md is written once the packet is fab-ready"
        : fabReady
          ? ""
          : "Fix the blocking findings first — the packet is not fab-ready",
      href: JLCPCB_QUOTE_URL,
    },
  ];
}

/** The subset that should render at all. A permanently absent action is noise. */
export function visibleBoardActions(actions) {
  return (Array.isArray(actions) ? actions : []).filter(
    (action) => action.enabled || action.id === "order" || action.id === "open-kicad",
  );
}
