// Case exports are saved by the local server: Harness webviews have no download manager.
import fs from "node:fs";
import path from "node:path";
import { deflateRawSync } from "node:zlib";
import { createHash } from "node:crypto";

const MAX_BYTES = 64 * 1024 * 1024;
const MAX_TRIANGLES = 1_000_000;
const printableParts = manifest => manifest.parts.filter(part => part.printable !== false && !/DO_NOT_PRINT/i.test(String(part.name || "")));
const digest = bytes => createHash("sha256").update(bytes).digest("hex");

// Preserve a slicer's real plates/settings, rather than flattening them into our generic mesh ZIP.
// The report comes from the project's slicer verifier; compare its inputs on every export.
export function checkedPrintProject(root, manifest) {
  if (!manifest.printProject) return null;
  const project = fs.realpathSync(root), caseDir = fs.realpathSync(path.join(project, "case"));
  if (!caseDir.startsWith(project + path.sep)) throw new Error("Print project is outside case/");
  const member = (name, extension) => {
    if (typeof name !== "string" || !name || !name.toLowerCase().endsWith(extension)) throw new Error("Invalid print project member");
    const file = fs.realpathSync(path.resolve(caseDir, "out", name));
    if (!file.startsWith(caseDir + path.sep) || !fs.statSync(file).isFile()) throw new Error("Print project is outside case/");
    if (fs.statSync(file).size > MAX_BYTES) throw new Error("Print project is too large");
    return file;
  };
  const file = member(manifest.printProject.file, ".3mf"), report = JSON.parse(fs.readFileSync(member(manifest.printProject.report, ".json"), "utf8"));
  const bytes = fs.readFileSync(file);
  if (bytes.subarray(0, 4).toString("hex") !== "504b0304" || report.passed !== true || report.errors?.length ||
      report.file !== path.relative(project, file) || report.sha256 !== digest(bytes)) throw new Error("Rebuild and verify the arranged print project");
  if (!Array.isArray(manifest.parts) || !manifest.parts.length || manifest.parts.length > 128) throw new Error("Invalid case parts list");
  const parts = printableParts(manifest).filter(part => part.role !== "coupon");
  if (!parts.length || !Array.isArray(report.meshes) || report.meshes.length !== parts.length) throw new Error("Print project must cover every production part, without coupons");
  const names = new Set();
  for (const part of parts) {
    const name = `${part.name}.stl`, mesh = report.meshes.find(mesh => mesh.name === name);
    if (names.has(name) || !mesh || mesh.canonicalSTLSha256 !== digest(fs.readFileSync(caseFile(project, part.stl))) ||
        JSON.stringify(mesh.printRotateDeg) !== JSON.stringify(part.printRotateDeg || [0, 0, 0])) throw new Error("Print project meshes or orientations changed; regenerate the plates");
    names.add(name);
  }
  if (!Array.isArray(report.plates) || !report.plates.length || report.plates.length > 128) throw new Error("Print project has no checked plates");
  const assigned = new Set(), indices = new Set();
  for (const plate of report.plates) {
    if (!Number.isInteger(plate.index) || plate.index < 1 || indices.has(plate.index) || !Array.isArray(plate.parts) || !plate.parts.length) throw new Error("Invalid print plate assignment");
    indices.add(plate.index);
    for (const name of plate.parts) {
      if (!names.has(name) || assigned.has(name)) throw new Error("Print plates contain missing, duplicate or unexpected parts");
      assigned.add(name);
    }
  }
  if (assigned.size !== names.size) throw new Error("Print plates omit a production part");
  return { bytes, file: report.file, plates: report.plates.map(plate => ({
    index: plate.index,
    name: typeof plate.name === "string" ? plate.name : null,
    parts: plate.parts.map(name => name.slice(0, -4)),
  })) };
}

export function preparedPrintProject(root, manifest) {
  return checkedPrintProject(root, manifest)?.bytes || null;
}

/** Plate membership from the same checked native 3MF used by Print all. */
export function preparedPrintPlates(root, manifest) {
  return checkedPrintProject(root, manifest)?.plates || null;
}
export function caseFile(root, member) {
  const project = fs.realpathSync(root), out = fs.realpathSync(path.join(project, "case", "out"));
  if (!out.startsWith(path.join(project, "case") + path.sep)) throw new Error("Case output is outside the project");
  if (typeof member !== "string" || !member) throw new Error("Missing case file");
  const file = fs.realpathSync(path.resolve(out, member));
  if (!file.startsWith(out + path.sep) || !fs.statSync(file).isFile()) throw new Error("Case file is outside case/out");
  return file;
}
export function safeName(value) {
  return String(value || "case").replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^[.-]+|[.-]+$/g, "") || "case";
}
export function saveExport(dir, name, bytes) {
  const parsed = path.parse(safeName(name));
  for (let n = 1; ; n++) {
    const target = path.join(dir, `${parsed.name}${n === 1 ? "" : `-${n}`}${parsed.ext}`);
    try { fs.writeFileSync(target, bytes, { flag: "wx" }); return { path: target, dir, filename: path.basename(target), bytes: bytes.length }; }
    catch (error) { if (error.code !== "EEXIST") throw error; }
  }
}
export function readStl(data) {
  if (!data.length || data.length > MAX_BYTES) throw new Error("STL is empty or too large");
  const points = [];
  const count = data.length >= 84 ? data.readUInt32LE(80) : 0;
  if (84 + count * 50 === data.length) {
    if (!count || count > MAX_TRIANGLES) throw new Error("Invalid STL triangle count");
    for (let i = 0; i < count; i++) for (let k = 0; k < 3; k++) {
      const at = 84 + i * 50 + 12 + k * 12;
      points.push([data.readFloatLE(at), data.readFloatLE(at + 4), data.readFloatLE(at + 8)]);
    }
  } else {
    for (const m of data.toString("utf8").matchAll(/\bvertex\s+(\S+)\s+(\S+)\s+(\S+)/g)) {
      points.push(m.slice(1).map(Number));
      if (points.length > MAX_TRIANGLES * 3) throw new Error("STL has too many triangles");
    }
  }
  if (!points.length || points.length % 3 || points.some(p => !p.every(Number.isFinite))) throw new Error("STL contains invalid vertices");
  return points;
}
export function orient(points, rotation = [0, 0, 0]) {
  if (!Array.isArray(rotation) || rotation.length !== 3 || !rotation.every(Number.isFinite)) throw new Error("Invalid print rotation");
  const transformed = points.map(point => {
    let [x, y, z] = point;
    for (let axis = 0; axis < 3; axis++) {
      const angle = rotation[axis] * Math.PI / 180, c = Math.cos(angle), s = Math.sin(angle);
      if (axis === 0) [y, z] = [y * c - z * s, y * s + z * c];
      if (axis === 1) [x, z] = [x * c + z * s, -x * s + z * c];
      if (axis === 2) [x, y] = [x * c - y * s, x * s + y * c];
    }
    return [x, y, z];
  });
  const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
  for (const p of transformed) for (let axis = 0; axis < 3; axis++) { min[axis] = Math.min(min[axis], p[axis]); max[axis] = Math.max(max[axis], p[axis]); }
  return { points: transformed.map(p => p.map((v, i) => v - min[i])), size: max.map((v, i) => v - min[i]) };
}
const xml = s => String(s).replace(/[<>&"']/g, c => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&apos;" }[c]));
const num = n => String(Number(n.toFixed(6)));
function meshXml(points) {
  const vertices = [], triangles = [], index = new Map();
  for (let at = 0; at < points.length; at += 3) {
    const triangle = points.slice(at, at + 3).map(p => {
      const key = p.map(num).join(" ");
      if (!index.has(key)) { index.set(key, vertices.length); vertices.push(p); }
      return index.get(key);
    });
    triangles.push(`<triangle v1="${triangle[0]}" v2="${triangle[1]}" v3="${triangle[2]}"/>`);
  }
  return `<mesh><vertices>${vertices.map(p => `<vertex x="${num(p[0])}" y="${num(p[1])}" z="${num(p[2])}"/>`).join("")}</vertices><triangles>${triangles.join("")}</triangles></mesh>`;
}
function crc32(data) {
  let crc = 0xffffffff;
  for (const byte of data) { crc ^= byte; for (let k = 0; k < 8; k++) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1)); }
  return (crc ^ 0xffffffff) >>> 0;
}
// Three small OPC members; regular ZIP (not ZIP64), deflate, UTF-8 names.
function zip(files) {
  const local = [], central = []; let offset = 0;
  for (const [name, text] of Object.entries(files)) {
    const filename = Buffer.from(name), data = Buffer.from(text), packed = deflateRawSync(data), crc = crc32(data);
    const head = Buffer.alloc(30); head.writeUInt32LE(0x04034b50); head.writeUInt16LE(20, 4); head.writeUInt16LE(0x800, 6); head.writeUInt16LE(8, 8); head.writeUInt16LE(33, 12);
    head.writeUInt32LE(crc, 14); head.writeUInt32LE(packed.length, 18); head.writeUInt32LE(data.length, 22); head.writeUInt16LE(filename.length, 26);
    local.push(head, filename, packed);
    const entry = Buffer.alloc(46); entry.writeUInt32LE(0x02014b50); entry.writeUInt16LE(20, 4); entry.writeUInt16LE(20, 6); entry.writeUInt16LE(0x800, 8); entry.writeUInt16LE(8, 10); entry.writeUInt16LE(33, 14);
    entry.writeUInt32LE(crc, 16); entry.writeUInt32LE(packed.length, 20); entry.writeUInt32LE(data.length, 24); entry.writeUInt16LE(filename.length, 28); entry.writeUInt32LE(offset, 42);
    central.push(entry, filename); offset += head.length + filename.length + packed.length;
  }
  const directory = Buffer.concat(central), end = Buffer.alloc(22); end.writeUInt32LE(0x06054b50); end.writeUInt16LE(central.length / 2, 8); end.writeUInt16LE(central.length / 2, 10); end.writeUInt32LE(directory.length, 12); end.writeUInt32LE(offset, 16);
  return Buffer.concat([...local, directory, end]);
}
export function printAll3mf(root, manifest) {
  if (!Array.isArray(manifest.parts) || !manifest.parts.length || manifest.parts.length > 128) throw new Error("Invalid case parts list");
  const printable = printableParts(manifest);
  if (!printable.length) throw new Error("The case has no printable parts");
  let bytes = 0;
  const parts = printable.map(part => {
    const file = caseFile(root, part.stl);
    bytes += fs.statSync(file).size;
    if (bytes > MAX_BYTES) throw new Error("Case meshes are too large");
    return { name: part.name, ...orient(readStl(fs.readFileSync(file)), part.printRotateDeg) };
  });
  const width = Math.max(...parts.map(p => p.size[0]), Math.sqrt(parts.reduce((n, p) => n + (p.size[0] + 8) * (p.size[1] + 8), 0)) * 1.3);
  let x = 0, y = 0, rowHeight = 0;
  const objects = [], items = [];
  parts.forEach((part, i) => {
    if (x && x + part.size[0] > width) { x = 0; y += rowHeight + 8; rowHeight = 0; }
    const id = i + 1;
    objects.push(`<object id="${id}" type="model" name="${xml(part.name)}">${meshXml(part.points)}</object>`);
    items.push(`<item objectid="${id}" transform="1 0 0 0 1 0 0 0 1 ${num(x)} ${num(y)} 0"/>`);
    x += part.size[0] + 8; rowHeight = Math.max(rowHeight, part.size[1]);
  });
  return zip({
    "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>',
    "_rels/.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>',
    "3D/3dmodel.model": `<?xml version="1.0" encoding="UTF-8"?><model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"><resources>${objects.join("")}</resources><build>${items.join("")}</build></model>`,
  });
}
