import test from 'node:test';
import assert from 'node:assert/strict';
import {mount,flush,click} from '../../../test/render.js';
import {__setTransportForTesting} from '@/lib/transport.ts';
import PromptsTab from '../PromptsTab.jsx';
import CaseOverviewTab from '../CaseOverviewTab.jsx';
const settle=async () => {for(let i=0;i<4;i++)await flush();};

test('Prompts shows the current and original requests separately, refreshes and clears old project text',async () => {
  const calls=[];
  const restore=__setTransportForTesting({workspace_prompts:async id => {calls.push(id);return {original:{text:'Original no-servo pet.'},current:{source:'conversation',text:'Original no-servo pet.\nUpdate: two servos now.'}};}});
  const ui=mount(PromptsTab,{projectId:'p'});
  try {
    await settle();assert.match(ui.container.querySelector('[data-slot="prompt-text"]').textContent,/two servos/);
    ui.set({original:true});assert.equal(ui.container.querySelector('[data-slot="prompt-text"]').textContent,'Original no-servo pet.');
    click(ui.container.querySelector('[aria-label="Refresh prompts"]'));await settle();assert.equal(calls.length,2);
    ui.set({projectId:'',fallbackText:''});await settle();assert.match(ui.container.textContent,/No prompt found/);
    assert.doesNotMatch(ui.container.textContent,/two servos/);assert.deepEqual(ui.errors,[]);
  } finally {ui.unmount();restore();}
});

test('Case overview embeds the actual HTML with an isolated script-only sandbox and a route back to downloads',() => {
  const tabs=[];const ui=mount(CaseOverviewTab,{artifact:{case:{overviewUrl:'/projects/p/case/motion/motion-view.html?v=1'}},onOpenTab:tab=>tabs.push(tab)});
  try {
    const frame=ui.container.querySelector('iframe');assert.equal(frame.getAttribute('sandbox'),'allow-scripts');assert.match(frame.getAttribute('src'),/motion-view/);
    assert.match(ui.container.querySelector('a').href,/motion-view/);click(ui.container.querySelector('button'));assert.deepEqual(tabs,['case']);
    ui.set({artifact:{}});assert.equal(ui.container.querySelector('iframe'),null);assert.match(ui.container.textContent,/No case overview yet/);
  }finally{ui.unmount();}
});


test('chat fallback keeps later user updates in Current and only the initial request in Original',async () => {
  const restore=__setTransportForTesting({workspace_prompts:async()=>({original:null,current:null,sessions:[]})});
  const ui=mount(PromptsTab,{projectId:'p',fallbackText:'First request',fallbackCurrentText:'First request\nUpdate: moving head'});
  try {await settle();assert.match(ui.container.querySelector('[data-slot="prompt-text"]').textContent,/moving head/);ui.set({original:true});assert.equal(ui.container.querySelector('[data-slot="prompt-text"]').textContent,'First request');}
  finally {ui.unmount();restore();}
});
