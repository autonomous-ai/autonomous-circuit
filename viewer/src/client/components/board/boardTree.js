// boardTree — the workspace navigator's model: Project → Board → Components /
// Nets, built from the catalog plus the circuit index. DOM-free and
// transport-free so node:test covers it directly.
//
// Ported in shape from the donor's workbench/sidebar.js
// (`buildSidebarDirectoryTree` / `listSidebarItems` / `collectAncestorDirectoryIds`):
// path-shaped ids so ancestors are derivable by string split, one numeric
// collator everywhere, and a search that filters the *items* and then rebuilds
// the tree so no empty group survives a query.
//
// Where it deliberately goes deeper: Vibe's tree bottoms out at a file. Ours
// cannot — one board is 134 parts and 75 nets, and `boardIndex` already
// resolves every one of them to geometry in both domains. So a board expands
// into its real electrical contents, and every leaf carries a `select`
// descriptor that is fed straight to the workspace's existing selection
// handler. The tree is a third door into one selection store, never a fork.

/** One numeric-aware collator for every sort in the tree (donor rule). */
const COLLATOR_OPTIONS = { numeric: true, sensitivity: "base" };

function compareLabels(a, b) {
  return String(a || "").localeCompare(String(b || ""), undefined, COLLATOR_OPTIONS);
}

/**
 * Ids are `/`-joined paths so ancestors fall out of a string split, exactly
 * like the donor's directory ids. A segment that contains a slash of its own
 * (a board file, a synthetic net key) is escaped so the path stays parseable.
 */
export function seg(value) {
  return String(value ?? "").replace(/%/g, "%25").replace(/\//g, "%2F");
}

/** "a/b/c" → ["a", "a/b", "a/b/c"] — the chain to auto-expand to a node. */
export function ancestorIds(id) {
  const parts = String(id || "").split("/").filter(Boolean);
  const out = [];
  let current = "";
  for (const part of parts) {
    current = current ? `${current}/${part}` : part;
    out.push(current);
  }
  return out;
}

// --- component grouping ----------------------------------------------------

// Reference-designator letters are IEEE-315's, which is the category an EE
// already reads without being taught one. `order` is the sequence a schematic
// is usually read in (actives, then passives, then everything mechanical),
// not alphabetical — alphabetical would put Batteries above ICs.
const REFDES_GROUPS = [
  { prefix: "U", label: "Integrated circuits" },
  { prefix: "Q", label: "Transistors" },
  { prefix: "D", label: "Diodes" },
  { prefix: "LED", label: "LEDs" },
  { prefix: "R", label: "Resistors" },
  { prefix: "C", label: "Capacitors" },
  { prefix: "L", label: "Inductors" },
  { prefix: "FB", label: "Ferrite beads" },
  { prefix: "Y", label: "Crystals" },
  { prefix: "X", label: "Oscillators" },
  { prefix: "J", label: "Connectors" },
  { prefix: "SW", label: "Switches" },
  { prefix: "K", label: "Relays" },
  { prefix: "F", label: "Fuses" },
  { prefix: "BT", label: "Batteries" },
  { prefix: "LS", label: "Sounders" },
  { prefix: "ANT", label: "Antennas" },
  { prefix: "M", label: "Motors" },
  { prefix: "TP", label: "Test points" },
  { prefix: "H", label: "Mounting hardware" },
];

const REFDES_LABEL = new Map(REFDES_GROUPS.map((entry) => [entry.prefix, entry.label]));
const REFDES_ORDER = new Map(REFDES_GROUPS.map((entry, index) => [entry.prefix, index]));

/** "U12" → "U"; "TP3" → "TP"; "" or "12" → "?" (an unlabelled part still needs a home). */
export function refdesPrefix(refdes) {
  const match = /^([A-Za-z]+)/.exec(String(refdes || "").trim());
  return match ? match[1].toUpperCase() : "?";
}

/** Human label for a refdes prefix; unknown prefixes keep their own letter. */
export function refdesGroupLabel(prefix) {
  return REFDES_LABEL.get(prefix) || (prefix === "?" ? "Unlabelled" : prefix);
}

/**
 * Components bucketed by refdes prefix, buckets in schematic-reading order
 * (known prefixes first in REFDES_GROUPS order, then unknown ones alphabetically,
 * then "?"), members sorted numerically so R2 precedes R10.
 *
 * @param {Array<{refdes: string}>} components
 * @returns {Array<{prefix: string, label: string, components: object[]}>}
 */
export function groupComponents(components) {
  const buckets = new Map();
  for (const component of Array.isArray(components) ? components : []) {
    const prefix = refdesPrefix(component?.refdes);
    const bucket = buckets.get(prefix);
    if (bucket) bucket.push(component);
    else buckets.set(prefix, [component]);
  }
  return [...buckets.entries()]
    .map(([prefix, list]) => ({
      prefix,
      label: refdesGroupLabel(prefix),
      components: [...list].sort((a, b) => compareLabels(a.refdes, b.refdes)),
    }))
    .sort((a, b) => {
      const rankA = REFDES_ORDER.has(a.prefix) ? REFDES_ORDER.get(a.prefix) : a.prefix === "?" ? 9999 : 500;
      const rankB = REFDES_ORDER.has(b.prefix) ? REFDES_ORDER.get(b.prefix) : b.prefix === "?" ? 9999 : 500;
      return rankA - rankB || compareLabels(a.label, b.label);
    });
}

// --- net grouping ----------------------------------------------------------

// Name shapes used only when the source layer did not set is_power/is_ground —
// an unnamed or synthetic net still belongs in the right bucket.
const GROUND_NAMES = /^(a|d|p|)gnd\d*$|^gnd|^vss|^ground$|^0v$/i;
const POWER_NAMES = /^(vcc|vdd|vbus|vin|vbat|vsys|av\w*|\+?\d+v\d*|\d+v\d+)$/i;

/** "power" | "ground" | "signal" for one indexed net. Flags win over names. */
export function netClass(net) {
  if (net?.isGround) return "ground";
  if (net?.isPower) return "power";
  const name = String(net?.name || "").trim();
  if (!name) return "signal";
  if (GROUND_NAMES.test(name)) return "ground";
  if (POWER_NAMES.test(name.replace(/^\+/, ""))) return "power";
  return "signal";
}

const NET_CLASS_ORDER = [
  { id: "power", label: "Power" },
  { id: "ground", label: "Ground" },
  { id: "signal", label: "Signal" },
];

/**
 * Nets bucketed by class, buckets always in Power / Ground / Signal order,
 * empty buckets dropped. Members keep the index's own sort (pin count desc,
 * then name) — the busiest net first is the useful order on a net list.
 *
 * @returns {Array<{id: string, label: string, nets: object[]}>}
 */
export function groupNets(nets) {
  const buckets = new Map(NET_CLASS_ORDER.map((entry) => [entry.id, []]));
  for (const net of Array.isArray(nets) ? nets : []) {
    buckets.get(netClass(net))?.push(net);
  }
  return NET_CLASS_ORDER.filter((entry) => buckets.get(entry.id).length).map((entry) => ({
    id: entry.id,
    label: entry.label,
    nets: buckets.get(entry.id),
  }));
}

// --- tree assembly ---------------------------------------------------------

function node(fields) {
  return { children: [], expandable: false, ...fields };
}

/**
 * The children of one board node, from an already-built circuit index.
 * Returns [] when the index has not landed — a board with no contents renders
 * as a leaf rather than an expandable node that opens onto nothing.
 */
export function buildBoardChildren(boardId, index) {
  if (!index || !index.stats?.elements) return [];
  const children = [];

  const componentGroups = groupComponents(index.components);
  if (componentGroups.length) {
    const componentsId = `${boardId}/components`;
    children.push(
      node({
        id: componentsId,
        kind: "group",
        label: "Components",
        count: index.components.length,
        expandable: true,
        children: componentGroups.map((group) => {
          const groupId = `${componentsId}/${seg(group.prefix)}`;
          return node({
            id: groupId,
            kind: "group",
            label: group.label,
            count: group.components.length,
            expandable: true,
            children: group.components.map((component) =>
              node({
                id: `${groupId}/${seg(component.key)}`,
                kind: "component",
                label: component.refdes || component.key,
                sublabel: component.value || component.mpn || component.ftype || "",
                select: { kind: "component", key: component.key },
              }),
            ),
          });
        }),
      }),
    );
  }

  const netGroups = groupNets(index.nets);
  if (netGroups.length) {
    const netsId = `${boardId}/nets`;
    children.push(
      node({
        id: netsId,
        kind: "group",
        label: "Nets",
        count: index.nets.length,
        expandable: true,
        children: netGroups.map((group) => {
          const groupId = `${netsId}/${group.id}`;
          return node({
            id: groupId,
            kind: "group",
            label: group.label,
            netClass: group.id,
            count: group.nets.length,
            expandable: true,
            children: group.nets.map((net) =>
              node({
                id: `${groupId}/${seg(net.key)}`,
                kind: "net",
                label: net.name,
                sublabel: net.pinCount ? `${net.pinCount} pins` : "",
                netClass: group.id,
                unnamed: net.unnamed === true,
                select: { kind: "net", key: net.key },
              }),
            ),
          });
        }),
      }),
    );
  }

  return children;
}

/**
 * The whole navigator.
 *
 * The active project renders from the live catalog the workspace already
 * holds; every other project is a collapsed header until it is expanded, at
 * which point the caller has fetched its catalog through
 * `project_catalog_read` and hands it over in `projectCatalogs`. Only the
 * *selected* board expands into components and nets — building 220 leaf nodes
 * for a board nobody is looking at is work for nothing.
 *
 * @param {{
 *   projects?: Array<{id: string, name: string}>,
 *   currentProjectId?: string|null,
 *   boardEntries?: object[],                 // active project's boards
 *   projectCatalogs?: Map<string, {status: string, boards: object[], error?: string}>,
 *   selectedFile?: string,
 *   index?: object|null,                     // circuit index for the selected board
 *   boardStatusOf?: (entry: object) => string,
 *   boardLabelOf?: (entry: object) => string,
 *   boardCountsOf?: (entry: object) => {error: number, warning: number}|null,
 * }} input
 * @returns {object[]} root nodes
 */
export function buildBoardTree({
  projects = [],
  currentProjectId = "",
  boardEntries = [],
  projectCatalogs = new Map(),
  selectedFile = "",
  index = null,
  boardStatusOf = () => "pending",
  boardLabelOf = (entry) => String(entry?.file || ""),
  boardCountsOf = () => null,
} = {}) {
  const list = Array.isArray(projects) ? projects : [];
  return list.map((project) => {
    const active = project.id === currentProjectId;
    const projectId = `p:${seg(project.id)}`;
    const cached = projectCatalogs.get(project.id) || null;
    const boards = active ? boardEntries : cached?.boards || [];

    const children = boards.map((entry) => {
      const boardId = `${projectId}/b:${seg(entry.file)}`;
      const selected = active && entry.file === selectedFile;
      const counts = selected ? boardCountsOf(entry) : null;
      const boardChildren = selected ? buildBoardChildren(boardId, index) : [];
      return node({
        id: boardId,
        kind: "board",
        label: boardLabelOf(entry),
        projectId: project.id,
        boardFile: entry.file,
        status: boardStatusOf(entry),
        selected,
        counts,
        expandable: boardChildren.length > 0,
        children: boardChildren,
      });
    });

    return node({
      id: projectId,
      kind: "project",
      label: project.name || "Untitled project",
      projectId: project.id,
      active,
      // A foreign project is always expandable: expanding it is what triggers
      // the catalog fetch, so it must be openable before we know what's inside.
      expandable: active ? children.length > 0 : true,
      loading: !active && cached?.status === "loading",
      error: !active ? String(cached?.error || "") : "",
      children,
    });
  });
}

// --- search ----------------------------------------------------------------

function matches(target, query) {
  return String(target || "").toLowerCase().includes(query);
}

/**
 * Prune the tree to nodes matching `query`, keeping any ancestor of a match
 * (so a hit on `GND` still shows Project → Board → Nets → Ground → GND) and
 * keeping the whole subtree of a node that matches itself. Empty query returns
 * the tree unchanged, by identity.
 */
export function filterTree(nodes, query) {
  const needle = String(query || "").trim().toLowerCase();
  if (!needle) return nodes;
  const walk = (list) => {
    const out = [];
    for (const item of list || []) {
      const self = matches(item.label, needle) || matches(item.sublabel, needle);
      const kept = self ? item.children : walk(item.children);
      if (self || kept.length) {
        out.push({ ...item, children: kept, expandable: kept.length > 0 });
      }
    }
    return out;
  };
  return walk(nodes);
}

/**
 * Flatten to the rows a list actually renders: depth-first, descending only
 * into expanded nodes. `forceExpand` is the donor's search behaviour — while a
 * query is active everything is open, because a hidden match is not a match.
 *
 * @returns {Array<{node: object, depth: number, expanded: boolean}>}
 */
export function visibleRows(nodes, expandedIds, { forceExpand = false } = {}) {
  const open = expandedIds instanceof Set ? expandedIds : new Set(expandedIds || []);
  const rows = [];
  const walk = (list, depth) => {
    for (const item of list || []) {
      const expanded = item.expandable && (forceExpand || open.has(item.id));
      rows.push({ node: item, depth, expanded });
      if (expanded) walk(item.children, depth + 1);
    }
  };
  walk(nodes, 0);
  return rows;
}

/**
 * The id of the node holding `selection` on the selected board, or "" — used
 * to auto-expand and scroll the tree to whatever the canvas just selected.
 * Cheap: it walks the already-built tree rather than re-deriving the grouping.
 */
export function findSelectionNodeId(nodes, selection) {
  if (!selection?.kind || !selection.key) return "";
  const walk = (list) => {
    for (const item of list || []) {
      if (item.select && item.select.kind === selection.kind && item.select.key === selection.key) {
        return item.id;
      }
      const hit = walk(item.children);
      if (hit) return hit;
    }
    return "";
  };
  return walk(nodes);
}

/** Ids to open so `nodeId` is visible: its ancestors, not the node itself. */
export function expansionForNode(nodeId) {
  const chain = ancestorIds(nodeId);
  chain.pop();
  return chain;
}
