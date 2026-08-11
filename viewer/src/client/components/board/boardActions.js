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
 * The three members a factory builds from. Gated on `fab.ready` — see below.
 */
const FAB_PACKET_IDS = new Set(["gerbers", "bom", "cpl"]);

/**
 * The one sentence a locked packet member shows instead of its hint.
 * Named here so the menu and its test read the same words.
 */
export const PACKET_LOCKED_REASON =
  "Locked until every check passes — the Overview tab lists what is left.";

/**
 * The packet members present on this board, in the order a fab consumes them.
 *
 * **The three a factory builds from are gated on `fab.ready`.** The Fab tab
 * has always withheld them on an unready board — "never hand the user gerbers
 * the pipeline wouldn't ship" — but this menu handed the same file over from
 * the top bar, under the words "this is what a factory loads", on a board with
 * 89 findings stopping the order. Two surfaces, one gate, opposite answers;
 * the header won because it is the one you reach first.
 *
 * The KiCad project and the 3D model stay open for the same reason "Open in
 * KiCad" does: they are for looking at the board, not for paying to have it
 * made, and a board that is not ready is exactly the one worth opening.
 *
 * @param {string} stem
 * @param {object|null} artifact
 * @param {{fabReady?: boolean}} [gate]
 * @returns {Array<{id: string, label: string, url: string, filename: string,
 *   hint: string, enabled: boolean, reason: string}>}
 */
export function packetDownloads(stem, artifact, { fabReady = false } = {}) {
  const name = String(stem || "board");
  return [
    // Plain word first, trade term in brackets. Someone who has never ordered
    // a board does not know what a gerber or a CPL is, and someone who arrived
    // from Altium is scanning for exactly those words — leading with the plain
    // name costs the second reader nothing and rescues the first.
    {
      id: "gerbers",
      label: "Board files (gerbers)",
      url: String(artifact?.gerbersUrl || ""),
      filename: `${name}-gerbers.zip`,
      hint: "the picture of every layer — this is what a factory loads",
    },
    {
      id: "bom",
      label: "Parts list (BOM)",
      url: String(artifact?.bomUrl || ""),
      filename: `${name}-bom.csv`,
      hint: "every part, with the catalogue number to order it by",
    },
    {
      id: "cpl",
      label: "Placement file (CPL)",
      url: String(artifact?.cplUrl || ""),
      filename: `${name}-cpl.csv`,
      hint: "where each part sits — only needed if the factory solders them on",
    },
    {
      id: "kicad",
      label: "KiCad project",
      url: String(artifact?.kicadProjectUrl || ""),
      filename: `${name}-kicad.zip`,
      hint: "the whole design, to open in KiCad and edit by hand",
    },
    {
      id: "glb",
      label: "3D model",
      url: String(artifact?.glbUrl || ""),
      filename: `${name}.glb`,
      hint: "the board and its parts, for checking it fits in a case",
    },
  ]
    .filter((entry) => entry.url)
    .map((entry) => {
      const locked = FAB_PACKET_IDS.has(entry.id) && !fabReady;
      return { ...entry, enabled: !locked, reason: locked ? PACKET_LOCKED_REASON : "" };
    });
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
  const fabReady = sidecar?.fab?.ready === true;
  const packet = packetDownloads(stem, artifact, { fabReady });
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
      // Every `reason` is read by someone who has just found a grey button and
      // wants to know what to do about it, so each one names the state and the
      // way out of it in words that need no electronics background.
      reason: kicadUrl ? "" : "The KiCad files appear once the board has finished building.",
      note: `Unzip and open ${stem || "board"}.kicad_pro`,
    },
    {
      id: "packet",
      kind: "menu",
      label: "Download",
      items: packet,
      enabled: packet.length > 0,
      reason: packet.length ? "" : "Nothing to download until the board has been built.",
    },
    {
      id: "order",
      kind: "tab",
      target: "fab",
      label: "Order at JLCPCB",
      enabled: fabReady && hasOrder,
      reason: !hasOrder
        ? fabReady
          ? "The step-by-step ordering guide has not been written yet. Ask the chat to rebuild the board."
          : "Not ready to order yet — some checks still fail. The Overview tab lists what is left."
        : fabReady
          ? ""
          : "Not ready to order yet — some checks still fail. The Overview tab lists what is left.",
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
