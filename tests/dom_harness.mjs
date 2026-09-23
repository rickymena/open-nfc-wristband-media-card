// Loads the web UI's app.js against a stub DOM and drives it with realistic
// SSE payloads. Catches what static checks cannot: a function that is
// referenced but never defined, a null element dereference, a typo inside
// render(). Any of those leave the page frozen on its placeholder markup,
// which looks identical to "the server is not sending events".
//
// Usage: node dom_harness.mjs <index.html> <app.js>
import fs from "fs";

const [htmlPath, jsPath] = process.argv.slice(2);
const html = fs.readFileSync(htmlPath, "utf8");
const js = fs.readFileSync(jsPath, "utf8");

const ids = new Set([...html.matchAll(/id="([^"]+)"/g)].map((m) => m[1]));
const makeNode = (id) => ({
  id, hidden: false, disabled: false, value: "", textContent: "", innerHTML: "",
  className: "", checked: false, style: {},
  classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
  addEventListener() {},
});
const nodes = new Map([...ids].map((i) => [i, makeNode(i)]));

let source = null;
globalThis.document = { getElementById: (id) => nodes.get(id) ?? null };
globalThis.EventSource = class { constructor(url) { this.url = url; source = this; } };
globalThis.fetch = async () => ({ json: async () => ({ ok: true, message: "ok", notes: [] }) });
globalThis.confirm = () => true;
globalThis.alert = () => {};

const fail = (stage, err) => {
  console.error(`FAIL [${stage}] ${err.constructor.name}: ${err.message}`);
  process.exit(1);
};

try { new Function(js)(); } catch (e) { fail("load", e); }
if (!source) fail("load", new Error("app.js never opened an EventSource"));

const idle = {
  reader: "Test Reader", readers: ["Test Reader"], present: false, error: null,
};
const present = {
  ...idle, present: true, uid: "04:59:6C:E2:D2:20:91", genuine: true,
  product: "NTAG213", capacity: 144, formatted: true, writable: true,
  records: ["URI   https://example.com"], ndefOffset: 5, canAuthenticate: false,
  lastUserPage: 39,
  config: {
    mirror: "uid", mirrorEnabled: true, mirrorPage: 15, mirrorByte: 0,
    counter: true, counterProtected: false, auth0: 255, protectionActive: false,
    protectReads: false, cfglck: false, authlim: 0, strongModulation: true,
    configPage: 41,
  },
};
const clone = { ...present, genuine: false, uid: "53:77:C2:74:A3:00:01", config: null };
const errored = { ...idle, error: "No PC/SC reader found." };

for (const [name, state] of [["idle", idle], ["present", present],
                             ["clone-no-config", clone], ["error", errored]]) {
  try { source.onmessage({ data: JSON.stringify(state) }); }
  catch (e) { fail(`render:${name}`, e); }
}

try { source.onerror(); } catch (e) { fail("onerror", e); }

console.log("ok");
