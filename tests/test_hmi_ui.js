"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const handlers = {}, events = {}, requests = [], fetches = [], redirects = [];
const status = {textContent: "", appendChild() {}};
const password = {type: "password"};
const attributes = {};
const eye = {dataset: {localEye: "pw"}, addEventListener(name, fn) {events[name] = fn;}, setAttribute(k,v) {attributes[k] = v;}};
const submit = {disabled: false};
const form = {elements: {firmware: {files: []}}, querySelector() {return submit;}, addEventListener(name, fn) {handlers[name] = fn;}};
const document = {
  hidden: false,
  getElementById(id) {return {"ui-context": {dataset: {base: "/api/hassio_ingress/test", csrf: "mock-csrf"}}, "firmware-status": status, "firmware-file": form, pw: password}[id];},
  querySelectorAll() {return [eye];},
  addEventListener(name, fn) {handlers[name] = fn;},
  createElement() {return {};}
};
class XHR {
  constructor() {this.headers = {}; this.handlers = {}; this.upload = {addEventListener() {}}; requests.push(this);}
  open(method,url) {this.method = method; this.url = url;}
  setRequestHeader(k,v) {this.headers[k] = v;}
  addEventListener(k,v) {this.handlers[k] = v;}
  send(body) {this.body = body;}
}
const context = {
  document, XMLHttpRequest: XHR,
  fetch: async url => {fetches.push(url);return {ok: true,json: async()=>({state:"idle",message:"Kein Vorgang",bytes:0})};},
  location: {assign: url=>redirects.push(url)},
  window: {addEventListener() {}}, setTimeout() {}, clearTimeout() {}, console
};
(async () => {
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,"../nibe_local/nibe_hmi.js"),"utf8"),context);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(requests.length,0,"Opening the firmware page must not stage or flash anything");
  assert.deepEqual(fetches,["/api/hassio_ingress/test/hmi/firmware/status"]);
  events.click(); assert.equal(password.type,"text"); assert.equal(attributes["aria-pressed"],"true");
  document.hidden=true;handlers.visibilitychange();assert.equal(password.type,"password");
  form.elements.firmware.files=[{name:"wrong.zip",size:1024}];handlers.submit({preventDefault(){}});
  assert.equal(requests.length,0,"An invalid file must not be sent");
  form.elements.firmware.files=[{name:"mock.swu",size:513*1024*1024}];handlers.submit({preventDefault(){}});
  assert.equal(requests.length,0,"Oversized file must not be sent");
  const file={name:"mock.swu",size:1024};form.elements.firmware.files=[file];
  assert.equal(requests.length,0,"Selecting a file must not send it");
  handlers.submit({preventDefault(){}});
  assert.equal(requests.length,1);const request=requests[0];
  assert.equal(request.method,"POST");assert.equal(request.url,"/api/hassio_ingress/test/hmi/firmware/stage");
  assert.equal(request.headers["X-CSRF-Token"],"mock-csrf");assert.equal(request.headers["X-File-Length"],"1024");
  assert.equal(request.body,file);assert.equal(submit.disabled,true);
  request.handlers.error();assert.equal(submit.disabled,false);assert.equal(requests.length,1,"Network error must not retry");
  request.status=200;request.handlers.load();assert.deepEqual(redirects,["/api/hassio_ingress/test/hmi/firmware"]);
  assert(!requests.some(r=>r.url.endsWith("/firmware") || r.url.endsWith("/action")),"Staging cannot issue a firmware flash request");
  console.log("OK: explicit file staging, stream length/CSRF, no auto flash/retry, masked local password and visibility reset");
})().catch(error=>{console.error(error);process.exitCode=1;});
