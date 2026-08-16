// routingHelp — the router asking a person for a decision.
//
// When the copper cannot be finished, the checks produce one finding per
// missing connection. Seven findings is not seven problems; on a real board it
// is usually one gap that four connections all needed. A list of symptoms is
// also not something a person can act on: "a connection was never drawn" says
// nothing about what to change.
//
// `packages/router/src/routerlib/diagnose.py` measures the *reason* and writes
// it into the sidecar as `build.routingHelp`. This module turns that into
// something to decide: what is in the way, how wide it is against how wide it
// has to be, what moving it would do, and — when the measurement supports
// nothing — the honest sentence that says so.
//
// Three rules, the same three the measurement side keeps:
//
//   1. **No number appears here that was not measured.** Every millimetre in
//      this file comes off the sidecar. Nothing is inferred, rounded up into a
//      nicer story, or filled in when it is missing.
//   2. **A suggestion is only shown when it was tried.** `move` exists only
//      when the analysis translated the part and took the geometry again, so
//      "moving U3 0.2mm north opens it" is a report, not advice.
//   3. **"We cannot tell you why" is a finished card.** It gets the same
//      layout as the others and its own next step, because a dead end with no
//      exit is the defect the north star names.
//
// No word in the copy below needs an electronics background: "connection", not
// "net"; "wire", not "trace"; "the part", not "the footprint".

/** What a card is asking for. Ordered: a decision outranks a note. */
export const ASK = Object.freeze({
  MOVE: "move_part",
  GAP: "tight_gap",
  REROUTE: "reroute",
  NO_CHANNEL: "no_channel",
  ROUTER: "router_limit",
  UNKNOWN: "unattributed",
});

const ASK_RANK = {
  [ASK.MOVE]: 0,
  [ASK.GAP]: 1,
  [ASK.REROUTE]: 2,
  [ASK.NO_CHANNEL]: 3,
  [ASK.ROUTER]: 4,
  [ASK.UNKNOWN]: 5,
};

/** "0.2mm", "0.05mm", "-0.09mm" — the sidecar's precision, never more. */
export function mm(value, places = 2) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "";
  const fixed = n.toFixed(places);
  return `${fixed.replace(/\.?0+$/, "") || "0"}mm`;
}

/** "1 connection" / "4 connections". */
export function count(n, one, many = `${one}s`) {
  const value = Number(n) || 0;
  return `${value} ${value === 1 ? one : many}`;
}

/**
 * The name of a connection, as a person can read it.
 *
 * A generated board names most of its own connections `net22`, which carries
 * no meaning to anybody. Saying so is better than printing it as if it were a
 * name — and the real names (`USB_DP`, `V3_3`) are worth keeping exactly.
 */
export function connectionName(name) {
  const text = String(name || "").trim();
  if (!text) return "an unnamed connection";
  if (/^net\d+$/i.test(text)) return "an unnamed connection";
  return text;
}

export function connectionList(names) {
  const list = (Array.isArray(names) ? names : []).map(connectionName);
  if (!list.length) return "";
  if (list.length === 1) return list[0];
  if (list.length === 2) return `${list[0]} and ${list[1]}`;
  return `${list.slice(0, -1).join(", ")} and ${list[list.length - 1]}`;
}

/** "north" -> "up the board"; the compass means nothing on a screen. */
const HEADING_WORDS = {
  north: "up the board",
  south: "down the board",
  east: "to the right",
  west: "to the left",
  "north-east": "up and to the right",
  "north-west": "up and to the left",
  "south-east": "down and to the right",
  "south-west": "down and to the left",
};

export function headingWords(heading) {
  return HEADING_WORDS[String(heading || "").toLowerCase()] || String(heading || "");
}

/**
 * The sidecar's routing help, normalized, or null.
 *
 * Returns null for every shape that is not a diagnosis with something to say:
 * absent, switched off, crashed, or a board where everything connected. A card
 * about nothing is worse than no card.
 */
export function readRoutingHelp(sidecar) {
  const raw = sidecar?.build?.routingHelp;
  if (!raw || typeof raw !== "object") return null;
  if (raw.ran !== true) {
    // It did not run. That is worth keeping so the UI can say "we did not
    // check" rather than implying there was nothing to find.
    return {
      ran: false,
      reason: String(raw.reason || ""),
      asks: [],
      unrouted: 0,
      routable: 0,
      notes: [],
    };
  }
  const asks = Array.isArray(raw.asks) ? raw.asks : [];
  return {
    ran: true,
    reason: "",
    unrouted: Number(raw.unroutedNets) || 0,
    routable: Number(raw.routableNets) || 0,
    connected: Number(raw.connectedNets) || 0,
    resolutionMm: Number(raw.resolutionMm) || 0,
    seconds: Number(raw.seconds) || 0,
    notes: Array.isArray(raw.notes) ? raw.notes.map(String) : [],
    asks: [...asks].sort(
      (a, b) =>
        (ASK_RANK[a?.kind] ?? 9) - (ASK_RANK[b?.kind] ?? 9) ||
        (b?.nets?.length || 0) - (a?.nets?.length || 0),
    ),
  };
}

function pinchNames(ask) {
  const between = ask?.pinch?.between;
  if (!Array.isArray(between) || !between.length) return ["something", ""];
  const first = String(between[0]?.label || "something");
  const second = between[1] ? String(between[1].label) : "";
  return [first, second];
}

/**
 * One ask, written out: a title, a sentence of what was measured, the evidence
 * underneath it, and the words that make the next thing happen.
 *
 * @returns {{
 *   id: string, kind: string, tone: "decision"|"note",
 *   title: string, body: string, evidence: string[],
 *   nets: string[], at: {x:number,y:number,layer:string}|null,
 *   action: {label: string, request: string}|null,
 * }}
 */
export function helpCard(ask, { board = "" } = {}) {
  const kind = String(ask?.kind || ASK.UNKNOWN);
  const nets = Array.isArray(ask?.nets) ? ask.nets.map(String) : [];
  const who = connectionList(nets);
  const many = nets.length !== 1;
  const [first, second] = pinchNames(ask);
  const usable = ask?.pinch?.usableMm;
  const needed = ask?.neededMm;
  const evidence = Array.isArray(ask?.evidence) ? ask.evidence.map(String) : [];
  const at = ask?.at
    ? { x: Number(ask.at[0]), y: Number(ask.at[1]), layer: String(ask.layer || "") }
    : null;
  const boardBit = board ? ` on ${board}` : "";

  if (kind === ASK.MOVE && ask?.move) {
    const move = ask.move;
    const where = headingWords(move.heading);
    return {
      id: `${kind}:${nets.join(",")}`,
      kind,
      tone: "decision",
      title: `Move ${move.part} ${mm(move.distanceMm)} ${where}`,
      body:
        `${who} ${many ? "have" : "has"} to pass between ${first} and ${second}, ` +
        `where there is ${mm(usable)} of space and ${mm(needed)} is needed. ` +
        `We moved ${move.part} ${mm(move.distanceMm)} ${where} and measured again: ` +
        `the space becomes ${mm(move.afterUsableMm)}.`,
      evidence,
      nets,
      at,
      action: {
        label: `Move ${move.part}`,
        request:
          `Move ${move.part} ${mm(move.distanceMm)} ${where} ` +
          `(${mm(move.dxMm)} across, ${mm(move.dyMm)} up)${boardBit}, then rebuild ` +
          `and run the checks. This is to open the ${mm(usable)} gap between ` +
          `${first} and ${second} so ${who} can be wired; ${mm(needed)} is needed ` +
          `there. If moving it breaks something else, say what and stop.`,
      },
    };
  }

  if (kind === ASK.GAP) {
    return {
      id: `${kind}:${nets.join(",")}`,
      kind,
      tone: "decision",
      title: `${count(nets.length, "connection")} cannot fit between ${first}${second ? ` and ${second}` : ""}`,
      body:
        `There is ${mm(usable)} of space there and ${mm(needed)} is needed. ` +
        `Nothing we tried moving opened it, so this one is a choice about the ` +
        `layout rather than something we can do for you.`,
      evidence,
      nets,
      at,
      action: {
        label: "Ask for options",
        request:
          `${who} cannot be wired${boardBit}: the gap between ${first}` +
          `${second ? ` and ${second}` : ""} is ${mm(usable)} and ${mm(needed)} is ` +
          `needed. Moving either side was tried and ran into something else. ` +
          `What are the options — a bigger board, a different part, or a ` +
          `different layout? Give me the tradeoff for each, and do not guess.`,
      },
    };
  }

  if (kind === ASK.REROUTE) {
    return {
      id: `${kind}:${nets.join(",")}`,
      kind,
      tone: "decision",
      title: `Two wires already drawn are in the way of ${count(nets.length, "connection")}`,
      body:
        `${who} ${many ? "have" : "has"} to pass between the ${first} wire and the ` +
        `${second} wire, which leave ${mm(usable)} between them where ${mm(needed)} ` +
        `is needed. No part has to move — one of those two wires has to take a ` +
        `different path.`,
      evidence,
      nets,
      at,
      action: {
        label: "Re-route the wires in the way",
        request:
          `Re-route ${first} or ${second}${boardBit} so ${who} can get through. ` +
          `They currently leave ${mm(usable)} between them and ${mm(needed)} is ` +
          `needed. Then rebuild and run the checks.`,
      },
    };
  }

  if (kind === ASK.ROUTER) {
    return {
      id: `${kind}:${nets.join(",")}`,
      kind,
      tone: "decision",
      title: `${count(nets.length, "connection")} had room but ${many ? "were" : "was"} not drawn`,
      body:
        `There is space on the board for ${many ? "these" : "this"}. Nothing has to ` +
        `move — the step that draws the copper stopped before it found the way.`,
      evidence,
      nets,
      at,
      action: {
        label: "Try drawing them again",
        request:
          `Lay out the copper again${boardBit} with the router working harder: ` +
          `${who} ${many ? "have" : "has"} room on the board and ${many ? "were" : "was"} ` +
          `left undrawn. Then rebuild and run the checks.`,
      },
    };
  }

  // no_channel and unattributed. Both are honest dead ends, and a dead end
  // still gets a next step — that is the whole point of the second bar.
  const honest =
    kind === ASK.NO_CHANNEL
      ? `There is no way across at all: no gap of any width reaches ${many ? "them" : "it"}.`
      : `We measured the board around ${many ? "them" : "it"} and could not tie the ` +
        `failure to any two things, so we are not going to guess.`;
  return {
    id: `${kind}:${nets.join(",")}`,
    kind,
    tone: "note",
    title: `${count(nets.length, "connection")} failed and we cannot say why`,
    body: honest,
    evidence,
    nets,
    at,
    action: {
      label: "Look at it together",
      request:
        `${who} could not be wired${boardBit} and the analysis could not find ` +
        `the cause. Here is what it did measure:\n` +
        evidence.map((line) => `- ${line}`).join("\n") +
        `\n\nWork out what is blocking ${many ? "them" : "it"} and tell me what ` +
        `you find. If you cannot tell either, say so rather than guessing.`,
    },
  };
}

/** Every ask as a card, decisions first. */
export function helpCards(help, options = {}) {
  const asks = help?.asks;
  if (!Array.isArray(asks)) return [];
  return asks.map((ask) => helpCard(ask, options));
}

/**
 * The verdict strip's line, when what is left is missing copper.
 *
 * The strip answers "can I get this made?". For a board with unconnected
 * connections the true answer is "not yet, and here is the decision that would
 * change it" — a request, not a defect count.
 *
 * Returns null whenever there is nothing measured to say, so the strip falls
 * back to its normal wording rather than inventing a softer one.
 */
export function helpVerdict(help, { board = "" } = {}) {
  if (!help?.ran) return null;
  const cards = helpCards(help, { board });
  if (!cards.length) return null;
  const first = cards[0];
  const rest = cards.length - 1;
  return {
    headline: `${count(help.unrouted, "connection")} missing — ${first.tone === "decision" ? "one decision would help" : "cause unknown"}`,
    line: `${first.title}. ${first.body}${rest > 0 ? ` (${rest} more like this.)` : ""}`,
    card: first,
    cards,
    action: first.action,
  };
}

/**
 * One chat message for every card at once, for the "ask about all of it"
 * button. Kept in the order the cards are in, so the decision with a measured
 * answer is what the model reads first.
 */
export function helpRequestAll(help, options = {}) {
  const cards = helpCards(help, options);
  if (!cards.length) return "";
  return cards
    .map((card) => card.action?.request)
    .filter(Boolean)
    .join("\n\n");
}
