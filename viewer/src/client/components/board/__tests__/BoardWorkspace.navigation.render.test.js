import test from 'node:test';
import assert from 'node:assert/strict';
import {click,flush} from '../../../test/render.js';
import {openWorkspace} from './boardWorkspace.test-helper.js';
import {__setTransportForTesting} from '@/lib/transport.ts';

test('three primary sections route to the right content and remember their last subview', async () => {
  const w=await openWorkspace();
  const restore=__setTransportForTesting({workspace_prompts:async()=>({original:{text:'Original request'},current:{text:'Updated request',source:'conversation'}})});
  const group=id=>w.ui.container.querySelector(`[data-group="${id}"]`);
  const tab=id=>w.ui.container.querySelector(`[data-tab="${id}"]`);
  try {
    assert.deepEqual([...w.ui.container.querySelectorAll('[data-slot="board-group"]')].map(x=>x.textContent.trim()),['Prompts','Electric','Case']);
    assert.equal(group('electric').getAttribute('aria-current'),'true');
    assert.ok(tab('schematic'));assert.equal(tab('prompt-current'),null);
    click(group('prompts'));for(let i=0;i<4;i++)await flush();
    assert.equal(tab('schematic'),null);assert.match(w.ui.container.querySelector('[data-slot="prompts-tab"]').textContent,/Updated request/);
    click(tab('prompt-original'));await flush();assert.match(w.ui.container.querySelector('[data-slot="prompt-text"]').textContent,/Original request/);
    click(group('case'));await flush();assert.ok(w.ui.container.querySelector('[data-slot="case-overview"]'));
    assert.deepEqual([...w.ui.container.querySelectorAll('[data-slot="board-tab"]')].map(x=>x.textContent.trim()),['Overview','3D & Print']);
    click(tab('case'));await flush();assert.ok(w.ui.container.querySelector('[data-slot="case-empty"]'));
    click(group('electric'));await flush();assert.ok(tab('pcb'));assert.equal(tab('pcb').getAttribute('aria-current'),'true');
    click(group('case'));await flush();assert.equal(tab('case').getAttribute('aria-current'),'true');
    click(group('prompts'));await flush();assert.equal(tab('prompt-original').getAttribute('aria-current'),'true');
    assert.deepEqual(w.ui.errors,[]);
  }finally{restore();w.close();}
});
