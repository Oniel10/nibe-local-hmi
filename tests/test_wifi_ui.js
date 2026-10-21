"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
const source = fs.readFileSync(path.join(__dirname, "../nibe_local/nibe_hmi.js"), "utf8");
async function scenario(responses) {
  let clicks, calls = 0; const leaves = [];
  const timers = new Map(); let id = 0;
  function element() {return {children: [], textContent: "", appendChild(x) {this.children.push(x);}, replaceChildren() {this.children = [];}};}
  const list = element(), status = element(), button = {addEventListener(_, fn) {clicks = fn;}};
  vm.runInNewContext(source, {
    document: {getElementById(key) {return {"ui-context": {dataset: {base: "/ingress", csrf: "mock"}}, "wifi-networks": list, "wifi-status": status, "wifi-rescan": button}[key];}, querySelectorAll() {return [];}, addEventListener() {}, createElement: element},
    window: {addEventListener(name, fn) {if (name === "pagehide") leaves.push(fn);}}, AbortController,
    setTimeout(fn, delay) {timers.set(++id, {fn, delay}); return id;}, clearTimeout(key) {timers.delete(key);},
    fetch: async (url, options) => {assert.equal(url, "/ingress/hmi/api/network"); assert.equal(options.cache, "no-store"); const data = responses[Math.min(calls++, responses.length - 1)]; if (data instanceof Error) throw data; return {ok: true, json: async () => data};}
  });
  const settle = () => new Promise(resolve => setImmediate(resolve)); await settle();
  return {list, status, button, get calls() {return calls;}, async next() {const entry = [...timers].find(([, t]) => t.delay === 5000); assert(entry); timers.delete(entry[0]); entry[1].fn(); await settle();}, async restart() {clicks(); await settle();}, leave() {leaves.forEach(fn => fn());}, timers};
}
(async () => {
  const scan = await scenario([{}, {networks: [{ssid: "<img onerror=alert(1)>", auth: "psk", rssi: -42}]}, {}]);
  assert.match(scan.status.textContent, /Suche läuft/); await scan.next();
  assert.equal(scan.list.children[0].children[0].textContent, "<img onerror=alert(1)>");
  await scan.next(); assert.equal(scan.list.children.length, 1, "Interim empty response preserves results");
  scan.leave(); assert.equal(scan.timers.size, 0, "Navigation stops polling");
  const empty = await scenario([{}]); for (let n = 0; n < 11; n++) await empty.next();
  assert.equal(empty.calls, 12); assert.match(empty.status.textContent, /Suche beendet/); assert.equal(empty.button.disabled, false);
  await empty.restart(); assert.equal(empty.calls, 13); empty.leave();
  const failure = await scenario([new Error("offline")]); assert.match(failure.status.textContent, /nicht erreichbar/); assert.equal(failure.button.disabled, false);
  assert.equal(failure.timers.size, 0); failure.leave();
  console.log("OK: delayed WLAN results, safe text rendering, bounded sequential polling, preserved results, retry, failure and navigation cleanup");
})().catch(error => {console.error(error); process.exitCode = 1;});
