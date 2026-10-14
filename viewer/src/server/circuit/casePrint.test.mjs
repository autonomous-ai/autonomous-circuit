import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import {casePrintStatus, caseStatus} from './catalog.mjs';

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'handwired-print-'));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  const out = path.join(root, 'case/out'), reports = path.join(root, 'case/reports');
  fs.mkdirSync(out, {recursive: true}); fs.mkdirSync(reports);
  const sha = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
  const save = (file, value) => fs.writeFileSync(path.join(root, file), JSON.stringify(value));
  const manifest = {verification: {nativePCBCheckerApplies: false, report: '../reports/mechanical.json'}, parts: [
    {name:'shell',step:'shell.step',stl:'shell.stl'}, {name:'ref',printable:false,step:'ref.step',stl:'ref.stl'}]};
  for (const name of ['shell.step','shell.stl','ref.step','ref.stl']) fs.writeFileSync(path.join(out,name), name);
  save('case/out/parts.json', manifest);
  const mechanical = {passed:true, scope:'handwired assembly', collisions:[], partShas: Object.fromEntries(manifest.parts.flatMap(p => [p.step,p.stl]).map(n => [n,sha(path.join(out,n))]))};
  save('case/reports/mechanical.json', mechanical);
  const receipt = {passed:true, errors:[], stem:'main', meshValidation:'surface-v1', validationScope:'handwired-print', intentSha:null,
    verificationReport:'case/reports/mechanical.json', verificationSha:sha(path.join(reports,'mechanical.json')),
    partShas: Object.fromEntries(['parts.json', ...Object.keys(mechanical.partShas)].map(n => [n,sha(path.join(out,n))]))};
  save('case/reports/print-check.json', receipt);
  return {root, out, reports, sha, save, manifest, mechanical, receipt};
}

test('handwired print readiness does not certify PCB fit or require a PCB publication', t => {
  const f=fixture(t);
  assert.equal(casePrintStatus(f.root,'main').state,'passed');
  assert.equal(caseStatus(f.root,'main').state,'stale');
  assert.equal(casePrintStatus(f.root,'other').state,'stale');
  fs.unlinkSync(path.join(f.reports,'print-check.json'));
  assert.equal(casePrintStatus(f.root).state,'stale','declaration and mechanical pass alone cannot unlock');
});

test('every mesh, reference, manifest, receipt and mechanical report stays bound', t => {
  const f=fixture(t);
  for (const name of ['shell.stl','ref.step','parts.json']) {
    const file=path.join(f.out,name), original=fs.readFileSync(file);
    fs.appendFileSync(file,'\nchanged'); assert.equal(casePrintStatus(f.root).state,'stale',name); fs.writeFileSync(file,original);
  }
  f.save('case/reports/print-check.json', {...f.receipt,partShas:{}});
  assert.equal(casePrintStatus(f.root).state,'stale','receipt cannot omit hash coverage');
  f.save('case/reports/print-check.json',f.receipt);
  f.save('case/reports/mechanical.json',{...f.mechanical,passed:false});
  assert.equal(casePrintStatus(f.root).state,'stale','changed report requires revalidation');
  f.receipt.verificationSha=f.sha(path.join(f.reports,'mechanical.json')); f.save('case/reports/print-check.json',f.receipt);
  assert.equal(casePrintStatus(f.root).state,'failed');
});

test('print intent, report escapes and symlinks cannot authorize export', t => {
  const f=fixture(t);
  f.save('case/interfaces.json',{printer:{bedMm:[10,10,10]}});
  assert.equal(casePrintStatus(f.root).state,'stale'); fs.unlinkSync(path.join(f.root,'case/interfaces.json'));
  const outside=path.join(f.root,'outside.json'); f.save('outside.json', f.mechanical);
  f.manifest.verification.report='../../outside.json'; f.save('case/out/parts.json',f.manifest);
  assert.equal(casePrintStatus(f.root).state,'stale');
  f.manifest.verification.report='../reports/mechanical.json'; f.save('case/out/parts.json',f.manifest);
  fs.unlinkSync(path.join(f.reports,'mechanical.json')); fs.symlinkSync(outside,path.join(f.reports,'mechanical.json'));
  assert.equal(casePrintStatus(f.root).state,'stale');
});
