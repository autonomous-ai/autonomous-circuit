// The Firmware tab, mounted for real: the empty state says when firmware
// appears, and a firmware tree opens on its entry point with every file a
// click away. `fetch` is the one stub, at the same seam the app crosses.

import assert from "node:assert/strict";
import test from "node:test";

import { click, flush, mount } from "../../../test/render.js";
import FirmwareTab, { languageFor, pickFirstFile } from "../FirmwareTab.jsx";

const FILES = {
  "/projects/p/firmware/README.md": "# Flash it\n\nHold BOOTSEL, drop the UF2.",
  "/projects/p/firmware/platformio.ini": "[env:pico]\nboard = pico",
  "/projects/p/firmware/src/main.c": "int main(void) { return 0; }",
};

function firmwareArtifact() {
  const files = Object.keys(FILES)
    .sort()
    .map((rel) => ({ file: rel.replace("/projects/p/firmware/", ""), url: `${rel}?v=1-1`, bytes: FILES[rel].length }));
  return { firmware: { files, readmeUrl: "/projects/p/firmware/README.md?v=1-1", truncated: false } };
}

function stubFetch() {
  const calls = [];
  const original = globalThis.fetch;
  globalThis.fetch = async (input) => {
    const url = String(input);
    calls.push(url);
    const text = FILES[url.split("?")[0]];
    if (text === undefined) return { ok: false, status: 404, text: async () => "" };
    return { ok: true, status: 200, text: async () => text };
  };
  return { calls, restore: () => { globalThis.fetch = original; } };
}

async function settleFetches() {
  // mount → effect → fetch → then → setState: a few microtask turns.
  for (let i = 0; i < 4; i += 1) await flush();
}

test("without a firmware tree the tab says when one appears, and why not sooner", async () => {
  const ui = mount(FirmwareTab, { artifact: {}, sidecar: { fab: { ready: false } } });
  try {
    const empty = ui.container.querySelector('[data-slot="firmware-empty"]');
    assert.ok(empty, "the empty state renders");
    assert.match(empty.textContent, /No firmware yet/);
    assert.match(empty.textContent, /after the board checks out/);

    ui.set({ sidecar: { fab: { ready: true } } });
    assert.match(ui.container.textContent, /The board checks out\. Firmware is the next thing/);

    // An empty tree is no tree.
    ui.set({ artifact: { firmware: { files: [] } } });
    assert.ok(ui.container.querySelector('[data-slot="firmware-empty"]'));
    assert.deepEqual(ui.errors, []);
  } finally {
    ui.unmount();
  }
});

test("a firmware tree opens on main.*, renders the README, and switches files on click", async () => {
  const net = stubFetch();
  const ui = mount(FirmwareTab, { artifact: firmwareArtifact(), sidecar: { fab: { ready: true } }, boardName: "main" });
  try {
    await settleFetches();
    assert.equal(ui.container.querySelector('[data-slot="firmware-empty"]'), null);

    const buttons = [...ui.container.querySelectorAll('[data-slot="firmware-file"]')];
    assert.deepEqual(buttons.map((b) => b.dataset.file), ["README.md", "platformio.ini", "src/main.c"]);
    const current = buttons.find((b) => b.getAttribute("aria-current") === "true");
    assert.equal(current?.dataset.file, "src/main.c", "the entry point opens first, not the README");

    // The README is fetched for the top card, main.c for the code pane — and nothing else yet.
    assert.deepEqual(
      net.calls.map((u) => u.split("?")[0]).sort(),
      ["/projects/p/firmware/README.md", "/projects/p/firmware/src/main.c"],
    );
    assert.match(ui.container.querySelector('[data-slot="firmware-readme"]').textContent, /Hold BOOTSEL/);
    assert.match(ui.container.querySelector('[data-slot="chat-code-block"]').textContent, /int main/);

    click(buttons[1]);
    await settleFetches();
    assert.ok(net.calls.some((u) => u.startsWith("/projects/p/firmware/platformio.ini")));
    assert.match(ui.container.querySelector('[data-slot="chat-code-block"]').textContent, /env:pico/);
    const nowCurrent = [...ui.container.querySelectorAll('[data-slot="firmware-file"]')].find(
      (b) => b.getAttribute("aria-current") === "true",
    );
    assert.equal(nowCurrent?.dataset.file, "platformio.ini");
    assert.deepEqual(ui.errors, []);
  } finally {
    ui.unmount();
    net.restore();
  }
});

test("a file that fails to load says so instead of showing a stale one", async () => {
  const net = stubFetch();
  const artifact = firmwareArtifact();
  artifact.firmware.files.push({ file: "src/gone.c", url: "/projects/p/firmware/src/gone.c?v=1-1", bytes: 0 });
  const ui = mount(FirmwareTab, { artifact });
  try {
    await settleFetches();
    const gone = [...ui.container.querySelectorAll('[data-slot="firmware-file"]')].find((b) => b.dataset.file === "src/gone.c");
    click(gone);
    await settleFetches();
    assert.ok(ui.container.querySelector('[data-slot="firmware-error"]'));
    assert.equal(ui.container.querySelector('[data-slot="chat-code-block"]'), null);
  } finally {
    ui.unmount();
    net.restore();
  }
});

test("languageFor reads the basename before the extension", () => {
  assert.equal(languageFor("Makefile"), "makefile");
  assert.equal(languageFor("src/CMakeLists.txt"), "cmake");
  assert.equal(languageFor("src/main.c"), "c");
  assert.equal(languageFor("src/main.ino"), "cpp");
  assert.equal(languageFor("code.py"), "python");
  assert.equal(languageFor("platformio.ini"), "ini");
  assert.equal(languageFor("weird.xyz"), "text");
});

test("pickFirstFile prefers the entry point, then any source, never the README", () => {
  const readme = { file: "README.md" };
  const ini = { file: "platformio.ini" };
  const main = { file: "src/main.cpp" };
  assert.equal(pickFirstFile([readme, ini, main]), main);
  assert.equal(pickFirstFile([readme, ini]), ini);
  assert.equal(pickFirstFile([readme, {file: "WIRING.md"}, ini]), ini);
  assert.equal(pickFirstFile([readme]), readme);
  assert.equal(pickFirstFile([]), null);
});

test('the wiring guide is pinned, resolves its own links and clears stale instructions when the project changes',async () => {
  const original=globalThis.fetch;
  const texts={...FILES,
    '/projects/p/firmware/README.md':'# Firmware\n[Wiring](WIRING.md)',
    '/projects/p/firmware/WIRING.md':'# Connect the display\nSCL → GPIO4.\n[Bring-up](../engineering/bringup.md)\n[Unsafe](javascript:alert)'};
  globalThis.fetch=async input => {const text=texts[String(input).split('?')[0]];return {ok:text!==undefined,status:text===undefined?404:200,text:async()=>text||''};};
  const artifact=firmwareArtifact();artifact.firmware.wiringUrl='/projects/p/firmware/WIRING.md?v=2';
  const ui=mount(FirmwareTab,{artifact});
  try {
    await settleFetches();const guide=ui.container.querySelector('[data-slot="firmware-wiring"]');
    assert.match(guide.textContent,/SCL → GPIO4/);
    assert.ok(ui.container.querySelector('[data-slot="firmware-wiring"] + [data-slot="firmware-readme"]'));
    const links=[...guide.querySelectorAll('a')];
    assert.equal(links[0].getAttribute('href'),'/projects/p/engineering/bringup.md');
    assert.doesNotMatch(links[1].getAttribute('href') || '',/javascript/i);
    assert.equal(ui.container.querySelector('[data-slot="firmware-readme"] a').getAttribute('href'),'/projects/p/firmware/WIRING.md');
    ui.set({artifact:{firmware:{...artifact.firmware,wiringUrl:'/projects/q/firmware/WIRING.md?v=1'}}});
    assert.doesNotMatch(ui.container.querySelector('[data-slot="firmware-wiring"]').textContent,/GPIO4/);
    await settleFetches();assert.match(ui.container.querySelector('[data-slot="firmware-wiring"]').textContent,/could not be read/);
    ui.set({artifact:firmwareArtifact()});await settleFetches();assert.equal(ui.container.querySelector('[data-slot="firmware-wiring"]'),null);
    assert.deepEqual(ui.errors,[]);
  }finally{ui.unmount();globalThis.fetch=original;}
});

test('Markdown asset URLs retain the existing safe URL handling',async () => {
  const {markdownAssetUrl}=await import('../../chat/Markdown.jsx');
  const base='/projects/p/firmware/WIRING.md?v=2';
  assert.equal(markdownAssetUrl('../case/wiring.png',base),'/projects/p/case/wiring.png');
  assert.equal(markdownAssetUrl('https://example.com/spec',base),'https://example.com/spec');
  assert.equal(markdownAssetUrl('javascript:alert(1)',base),'');
  assert.equal(markdownAssetUrl('data:text/html,hello',base),'');
  assert.equal(markdownAssetUrl('#power',base),'#power');
  assert.equal(markdownAssetUrl('../wiring.md',''),'../wiring.md');
});
