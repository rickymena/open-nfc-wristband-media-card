"use strict";

const $ = (id) => document.getElementById(id);
const el = {
  reader: $("reader"), dot: $("dot"), statusText: $("statusText"),
  facts: $("facts"), uid: $("uid"), product: $("product"),
  capacity: $("capacity"), access: $("access"),
  contentBox: $("contentBox"), content: $("content"),
  form: $("writeForm"), url: $("url"), ref: $("ref"), title: $("title"),
  titleWarn: $("titleWarn"), writeBtn: $("writeBtn"), lockBtn: $("lockBtn"),
  result: $("result"),
  configPanel: $("configPanel"), cfgSilicon: $("cfgSilicon"),
  cfgMirror: $("cfgMirror"), cfgCounter: $("cfgCounter"),
  cfgPassword: $("cfgPassword"), cfgLock: $("cfgLock"),
  mirrorMode: $("mirrorMode"), mirrorPage: $("mirrorPage"), mirrorByte: $("mirrorByte"),
  mirrorCalcBtn: $("mirrorCalcBtn"), mirrorBtn: $("mirrorBtn"),
  counterOnBtn: $("counterOnBtn"), counterOffBtn: $("counterOffBtn"),
  authWarn: $("authWarn"), pwd: $("pwd"), pack: $("pack"), auth0: $("auth0"),
  protectReads: $("protectReads"), acceptLockout: $("acceptLockout"),
  pwdSetBtn: $("pwdSetBtn"), pwdOffBtn: $("pwdOffBtn"), cfgResult: $("cfgResult"),
  unlockBtn: $("unlockBtn"), historyBtn: $("historyBtn"),
  historyMineBtn: $("historyMineBtn"), historyOut: $("historyOut"),
  historyPath: $("historyPath"),
};

// Characters a mirror occupies, so a placeholder can be sized to match.
const MIRROR_WIDTH = { off: 0, uid: 14, counter: 6, both: 21 };

let tag = { present: false, writable: false };
let busy = false;

// A title forces a Smart Poster record, which iOS ignores entirely. Surface
// that the moment someone types into the field rather than after they write.
el.title.addEventListener("input", () => {
  el.titleWarn.hidden = el.title.value.trim() === "";
});

function setButtons() {
  const ready = tag.present && !busy;
  el.writeBtn.disabled = !ready || !tag.writable;
  el.lockBtn.disabled = !ready || !tag.writable;
  el.writeBtn.textContent = busy ? "Writing..." : "Write tag";
  for (const b of [el.mirrorBtn, el.counterOnBtn, el.counterOffBtn,
                   el.pwdSetBtn, el.pwdOffBtn]) {
    b.disabled = !ready || !tag.writable;
  }
  // Unlock is the one action that only makes sense on a locked tag.
  el.unlockBtn.disabled = !tag.present || busy || tag.writable;
}

function renderConfig(state) {
  const c = state.config;
  el.configPanel.hidden = !state.present || !c;
  if (!c) return;

  el.cfgSilicon.textContent = state.genuine
    ? "genuine NXP"
    : "compatible clone - mirroring, counter and passwords may be absent";
  el.cfgMirror.textContent = c.mirrorEnabled
    ? `${c.mirror} at page ${c.mirrorPage}, byte ${c.mirrorByte}`
    : "off";
  el.cfgCounter.textContent = (c.counter ? "enabled" : "disabled")
    + (c.counterProtected ? ", password protected" : "");
  el.cfgPassword.textContent = c.protectionActive
    ? `ACTIVE from page ${c.auth0}, protects ${c.protectReads ? "reads and writes" : "writes"}`
    : `inactive (AUTH0 = 0x${c.auth0.toString(16).toUpperCase().padStart(2, "0")})`;
  el.cfgLock.textContent = c.cfglck ? "LOCKED permanently" : "open";

  // Only warn about authentication when the reader genuinely cannot do it.
  el.authWarn.hidden = state.canAuthenticate !== false;
}

// Work out where a mirror placeholder sits inside the URL typed above. The
// NDEF TLV header is 2 bytes and the URI record header is 5, so the first URL
// character lands 7 bytes past the start of the NDEF TLV.
function locateMirror() {
  const width = MIRROR_WIDTH[el.mirrorMode.value];
  if (!width) { showCfg(false, "Pick a mirror mode first."); return; }
  const url = el.url.value.trim();
  if (!url) { showCfg(false, "Type the URL above first."); return; }
  const scheme = ["https://www.", "http://www.", "https://", "http://"]
    .find((s) => url.startsWith(s));
  const rest = scheme ? url.slice(scheme.length) : url;
  if (rest.length < width) { showCfg(false, "URL is shorter than the mirror."); return; }
  const start = (tag.ndefOffset || 0) + 7 + rest.length - width;
  const page = Math.floor(start / 4);
  el.mirrorPage.value = page;
  el.mirrorByte.value = start % 4;
  showCfg(true, `Placeholder would start at page ${page}, byte ${start % 4}. ` +
    `Make sure the last ${width} characters of the URL are placeholder text.`);
}

function showCfg(ok, message, notes) {
  el.cfgResult.hidden = false;
  el.cfgResult.className = "result " + (ok ? "ok" : "err");
  let html = escapeHtml(message);
  if (notes && notes.length) {
    html += "<ul>" + notes.map((n) => `<li>${escapeHtml(n)}</li>`).join("") + "</ul>";
  }
  el.cfgResult.innerHTML = html;
}

function render(state) {
  tag = state;
  el.reader.textContent = state.reader || "no reader";

  el.dot.className = "dot";
  if (state.error) {
    el.dot.classList.add("error");
    el.statusText.textContent = state.error;
  } else if (state.present) {
    el.dot.classList.add("present");
    el.statusText.textContent = "Tag on reader";
  } else {
    el.dot.classList.add("waiting");
    el.statusText.textContent = "Hold a tag against the reader...";
  }

  el.facts.hidden = !state.present;
  if (state.present) {
    el.uid.innerHTML = escapeHtml(state.uid || "unknown") +
      (state.genuine === false
        ? ' <span class="badge clone" title="Byte 0 of a UID is the manufacturer code; NXP is 04">clone</span>'
        : "");
    el.product.textContent = state.product || "unknown";
    el.capacity.textContent = state.formatted
      ? `${state.capacity} bytes` : "not NDEF-formatted";
    el.access.innerHTML = state.writable
      ? "read/write"
      : 'read-only <span class="badge locked">locked</span>';
  }

  renderConfig(state);

  const records = state.records || [];
  el.contentBox.hidden = !state.present || records.length === 0;
  el.content.textContent = records.join("\n");

  setButtons();
}

function showResult(ok, message, notes) {
  el.result.hidden = false;
  el.result.className = "result " + (ok ? "ok" : "err");
  let html = escapeHtml(message);
  if (notes && notes.length) {
    html += "<ul>" + notes.map((n) => `<li>${escapeHtml(n)}</li>`).join("") + "</ul>";
  }
  el.result.innerHTML = html;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function postCfg(action, body) {
  busy = true; setButtons();
  try {
    const res = await fetch("/api/" + action, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    showCfg(data.ok, data.message, data.notes);
  } catch (err) {
    showCfg(false, "Request failed: " + err.message);
  } finally {
    busy = false; setButtons();
  }
}

async function post(action, body) {
  busy = true; setButtons();
  try {
    const res = await fetch("/api/" + action, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    showResult(data.ok, data.message, data.notes);
  } catch (err) {
    showResult(false, "Request failed: " + err.message);
  } finally {
    busy = false; setButtons();
  }
}

el.form.addEventListener("submit", (e) => {
  e.preventDefault();
  post("write-url", {
    url: el.url.value.trim(),
    ref: el.ref.value.trim(),
    title: el.title.value.trim(),
  });
});

el.lockBtn.addEventListener("click", () => {
  if (!confirm(
      "Soft lock this tag?\n\n" +
      "Phones will refuse to write it. This reader still can.\n\n" +
      "The capability container is one-time programmable, so this CANNOT be undone."
  )) return;
  post("lock", {});
});

el.mirrorCalcBtn.addEventListener("click", locateMirror);

el.mirrorBtn.addEventListener("click", () => {
  postCfg("mirror", {
    mode: el.mirrorMode.value,
    page: Number(el.mirrorPage.value),
    byte: Number(el.mirrorByte.value),
  });
});

el.counterOnBtn.addEventListener("click", () => postCfg("counter", { enabled: true }));
el.counterOffBtn.addEventListener("click", () => postCfg("counter", { enabled: false }));

el.pwdSetBtn.addEventListener("click", () => {
  if (!confirm(
      "Set a password on this tag?\n\n" +
      "The password cannot be read back. If you lose it, or this reader cannot " +
      "authenticate, the tag may become permanently unwritable."
  )) return;
  postCfg("password", {
    action: "set",
    password: el.pwd.value.trim(),
    pack: el.pack.value.trim() || "0000",
    auth0: Number(el.auth0.value),
    protectReads: el.protectReads.checked,
    acceptLockout: el.acceptLockout.checked,
  });
});

el.pwdOffBtn.addEventListener("click", () => {
  postCfg("password", { action: "disable", acceptLockout: el.acceptLockout.checked });
});

el.unlockBtn.addEventListener("click", () => {
  if (!confirm(
      "Try to clear the read-only flag?\n\n" +
      "On genuine NXP silicon this cannot work - the capability container " +
      "is one-time programmable. Clone chips often allow it. Either way the " +
      "result is verified by reading the byte back."
  )) return;
  post("unlock", {});
});

async function loadHistory(mine) {
  const q = mine && tag.uid ? `?uid=${encodeURIComponent(tag.uid)}` : "";
  el.historyOut.textContent = "Loading...";
  try {
    const res = await fetch("/api/history" + q);
    const data = await res.json();
    el.historyPath.textContent = data.path || "";
    const rows = (data.entries || []);
    if (!rows.length) { el.historyOut.textContent = "No entries yet."; return; }
    // Newest first reads better on screen than the file's append order.
    el.historyOut.textContent = rows.reverse().map((e) => {
      const mark = e.ok ? "ok  " : "FAIL";
      const bits = [e.ts, mark, e.action];
      if (e.uid) bits.push(e.uid);
      if (e.url) bits.push(e.url);
      if (!e.ok && e.message) bits.push("-- " + e.message);
      return bits.join("  ");
    }).join("\n");
  } catch (err) {
    el.historyOut.textContent = "Could not load history: " + err.message;
  }
}

el.historyBtn.addEventListener("click", () => loadHistory(false));
el.historyMineBtn.addEventListener("click", () => loadHistory(true));

// Live tag presence. EventSource reconnects on its own if the server restarts.
const events = new EventSource("/api/events");
events.onmessage = (e) => render(JSON.parse(e.data));
events.onerror = () => {
  el.dot.className = "dot error";
  el.statusText.textContent = "Lost connection to the server...";
  el.reader.textContent = "reconnecting";
};
