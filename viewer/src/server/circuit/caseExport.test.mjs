import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { caseFile, readStl, orient, printAll3mf, saveExport } from "./caseExport.mjs";

const vertices = [[0, 0, 5], [1, 0, 5], [0, 2, 5], [0, 0, 8]];
const faces = [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]];
export function tetraStl() {
  return Buffer.from(`solid test\n${faces.map(f => `facet normal 0 0 1\nouter loop\n${f.map(i => `vertex ${vertices[i].join(" ")}`).join("\n")}\nendloop\nendfacet`).join("\n")}\nendsolid test`);
}
test("ASCII and binary STL are read; print rotation places the flipped face on z=0", () => {
  const ascii = readStl(tetraStl());
  const binary = Buffer.alloc(84 + faces.length * 50); binary.writeUInt32LE(faces.length, 80);
  ascii.forEach((p, i) => p.forEach((v, k) => binary.writeFloatLE(v, 84 + Math.floor(i / 3) * 50 + 12 + (i % 3) * 12 + k * 4)));
  assert.deepEqual(readStl(binary), ascii);
  const placed = orient(ascii, [180, 0, 0]);
  assert.ok(Math.abs(placed.points[0][2] - 3) < 1e-6);
  assert.ok(Math.abs(Math.min(...placed.points.map(p => p[2]))) < 1e-6);
  assert.throws(() => readStl(Buffer.from("solid empty")), /invalid vertices/);
  assert.throws(() => readStl(Buffer.from("vertex NaN 0 0")), /invalid vertices/);
  assert.throws(() => orient(ascii, [0, NaN, 0]), /Invalid print rotation/);
});
test("3MF is a valid OPC ZIP with separate named, rotated meshes laid out without overlap", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "case-3mf-"));
  fs.mkdirSync(path.join(root, "case/out"), { recursive: true });
  fs.writeFileSync(path.join(root, "case/out/front.stl"), tetraStl());
  const manifest = { parts: [
    { name: "front & shell", stl: "front.stl", printRotateDeg: [180, 0, 0] },
    { name: "coupon", stl: "front.stl" },
    { name: "ref_keycap__DO_NOT_PRINT", stl: "missing-reference.stl" },
    { name: "reference", printable: false, stl: "missing-reference.stl" },
  ] };
  const file = path.join(root, "all.3mf"); fs.writeFileSync(file, printAll3mf(root, manifest));
  const model = JSON.parse(execFileSync("python3", ["-c", `
import zipfile,xml.etree.ElementTree as E,json,sys
with zipfile.ZipFile(sys.argv[1]) as z:
 assert z.testzip() is None
 assert set(z.namelist())=={'[Content_Types].xml','_rels/.rels','3D/3dmodel.model'}
 for p in z.namelist(): E.fromstring(z.read(p))
 m=E.fromstring(z.read('3D/3dmodel.model')); ns={'m':'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'}
 objects=[{'name':o.attrib['name'],'vertices':[v.attrib for v in o.findall('m:mesh/m:vertices/m:vertex',ns)],'triangles':len(o.findall('m:mesh/m:triangles/m:triangle',ns))} for o in m.findall('m:resources/m:object',ns)]
 print(json.dumps({'unit':m.attrib['unit'],'objects':objects,'items':[i.attrib for i in m.findall('m:build/m:item',ns)]}))
`, file], { encoding: "utf8" }));
  assert.equal(model.unit, "millimeter");
  assert.deepEqual(model.objects.map(o => [o.name, o.vertices.length, o.triangles]), [["front & shell", 4, 4], ["coupon", 4, 4]]);
  assert.equal(model.items.length, 2);
  assert.ok(Number(model.items[1].transform.split(" ")[9]) >= 9);
  for (const object of model.objects) assert.equal(Math.min(...object.vertices.map(v => Number(v.z))), 0);
});
test("case exports refuse traversal/symlinks and never overwrite a saved file", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "case-path-")), out = path.join(root, "case/out"); fs.mkdirSync(out, { recursive: true });
  fs.writeFileSync(path.join(root, "private.stl"), "private");
  fs.symlinkSync(path.join(root, "private.stl"), path.join(out, "escape.stl"));
  assert.throws(() => caseFile(root, "../../private.stl"), /outside/);
  assert.throws(() => caseFile(root, "escape.stl"), /outside/);
  const a = saveExport(root, "front.stl", Buffer.from("one")), b = saveExport(root, "front.stl", Buffer.from("two"));
  assert.equal(fs.readFileSync(a.path, "utf8"), "one"); assert.equal(b.filename, "front-2.stl");
});

test('a case with more than 32 printable clips and coupons fits the existing bounded manifest contract',() => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'case-many-parts-'));
  try {
    const out=path.join(root,'case/out');fs.mkdirSync(out,{recursive:true});
    fs.writeFileSync(path.join(out,'part.stl'),'solid part\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid part');
    const manifest={parts:Array.from({length:40},(_,i)=>({name:`clip-${i}`,stl:'part.stl'}))};
    const file=path.join(root,'many.3mf');fs.writeFileSync(file,printAll3mf(root,manifest));
    execFileSync('python3',['-c','import zipfile,sys,xml.etree.ElementTree as E; z=zipfile.ZipFile(sys.argv[1]); assert z.testzip() is None; r=E.fromstring(z.read("3D/3dmodel.model")); n={"m":"http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}; assert len(r.findall("m:resources/m:object",n))==40; assert len(r.findall("m:build/m:item",n))==40',file],{encoding:'utf8'});
    assert.throws(()=>printAll3mf(root,{parts:Array.from({length:129},()=>manifest.parts[0])}),/Invalid case parts list/);
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});
