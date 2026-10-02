/* SD Agent — frontend logic (PWA) */
"use strict";

const $ = (id) => document.getElementById(id);

const els = {
  gate: $("gate"), gatePw: $("gate-pw"), gateBtn: $("gate-btn"),
  gateErr: $("gate-err"), app: $("app"),
  viewChat: $("view-chat"), viewGallery: $("view-gallery"),
  msgs: $("msgs"), pill: $("pill"), pillText: $("pill-text"), pillBar: $("pill-bar"),
  input: $("input"), send: $("btn-send"),
  dot: $("dot"), modelname: $("modelname"),
  grid: $("grid"), gcount: $("gcount"), gempty: $("gempty"),
  composer: $("composer"),
  lb: $("lightbox"), lbImg: $("lb-img"), lbName: $("lb-name"),
  lbPrev: $("lb-prev"), lbNext: $("lb-next"), lbClose: $("lb-close"),
  lbMeta: $("lb-meta"), lbPrompt: $("lb-prompt"), lbNeg: $("lb-neg"),
  lbParams: $("lb-params"),
  lbPromptWrap: $("lb-prompt-wrap"), lbNegWrap: $("lb-neg-wrap"),
  lbParamsWrap: $("lb-params-wrap"), lbStage: $("lb-stage"),
  settings: $("settings"), shModels: $("sh-models"), shLoad: $("sh-load"),
  shCur: $("sh-cur"), shSd: $("sh-sd"), shLlm: $("sh-llm"), shKey: $("sh-key"),
  shKeymask: $("sh-keymask"), shSdok: $("sh-sdok"),
  shSys: $("sh-sysprompt"), shSysState: $("sh-sysstate"),
  toast: $("toast"),
};

let images = [];          // gallery list
let busy = false;
let statusTimer = null;   // progress polling while generating
let toastTimer = null;
let authed = false;

/* ------------------------------------------------------------------ util */

function toast(text, isErr) {
  els.toast.textContent = text;
  els.toast.classList.toggle("err", !!isErr);
  els.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { els.toast.hidden = true; }, 2600);
}

function fmtBytes(n) {
  if (n > 1048576) return (n / 1048576).toFixed(1) + " MB";
  if (n > 1024) return Math.round(n / 1024) + " KB";
  return n + " B";
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function esc(s) { return String(s ?? ""); }

function scrollDown(smooth) {
  requestAnimationFrame(() => {
    els.viewChat.scrollTo({ top: els.viewChat.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  });
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (r.status === 401) { showGate(); throw new Error("locked"); }
  let data = {};
  try { data = await r.json(); } catch { /* ignore */ }
  if (!r.ok) throw new Error(data.error || data.message || `HTTP ${r.status}`);
  return data;
}

/* ------------------------------------------------------------------ gate */

function showGate() {
  authed = false;
  els.app.hidden = true;
  els.gate.hidden = false;
  els.gatePw.value = "";
  setTimeout(() => els.gatePw.focus(), 50);
}

function showApp() {
  authed = true;
  els.gate.hidden = true;
  els.app.hidden = false;
  init();
}

async function tryLogin() {
  const pw = els.gatePw.value;
  if (!pw) return;
  els.gateBtn.disabled = true;
  els.gateErr.hidden = true;
  try {
    await api("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pw }),
    });
    showApp();
  } catch {
    els.gateErr.hidden = false;
    els.gatePw.select();
  } finally {
    els.gateBtn.disabled = false;
  }
}

els.gateBtn.addEventListener("click", tryLogin);
els.gatePw.addEventListener("keydown", (e) => {
  if (e.key === "Enter") tryLogin();
});

/* -------------------------------------------------------------- timeline */

function addBubble(kind, text) {
  const m = el("div", "msg " + kind, esc(text));
  els.msgs.appendChild(m);
  return m;
}

const variantIndex = new Map();   // file name -> generation card record

function addGeneration(evt, idx) {
  const src = evt.src ? evt.src.split("/").pop() : null;
  let rec;
  if (src && variantIndex.has(src)) {
    // a regeneration of an existing image — extend that card's carousel
    rec = variantIndex.get(src);
    if (idx != null) rec.evtIdxs.push(idx);
    for (const f of evt.files || []) {
      const name = f.split("/").pop();
      if (!rec.files.includes(name)) rec.files.push(name);
    }
  } else {
    const card = el("div", "gen");
    if (idx != null) card.dataset.idx = idx;
    card.innerHTML =
      '<div class="gen-viewer">' +
      '<button class="car-arrow car-prev" aria-label="Previous variant">‹</button>' +
      '<img loading="lazy" alt="">' +
      '<button class="car-arrow car-next" aria-label="Next variant">›</button>' +
      '<span class="car-count"></span>' +
      '</div><div class="gen-meta"></div>';
    rec = { card,
            files: (evt.files || []).map((f) => f.split("/").pop()),
            pos: 0, evtIdxs: idx != null ? [idx] : [] };
    rec.card.querySelector(".car-prev").addEventListener("click", (e) => {
      e.stopPropagation();
      slideVariant(rec, -1);
    });
    rec.card.querySelector(".car-next").addEventListener("click", (e) => {
      e.stopPropagation();
      slideVariant(rec, 1);
    });

    const meta = card.querySelector(".gen-meta");
    const g = evt.gen || {};
    const chips = [];
    if (g.model) chips.push([g.model, ""]);
    if (g.width && g.height) chips.push([`${g.width}×${g.height}`, ""]);
    if (g.steps) chips.push([`${g.steps} steps`, ""]);
    if (g.cfg_scale != null) chips.push([`cfg ${g.cfg_scale}`, ""]);
    if (g.sampler_name) chips.push([g.sampler_name, ""]);
    if (g.clip_skip) chips.push([`clip ${g.clip_skip}`, ""]);
    if (g.denoising_strength != null) chips.push([`denoise ${g.denoising_strength}`, ""]);
    if ((g.batch_size || 1) > 1) chips.push([`batch ${g.batch_size}`, ""]);
    if (evt.seed != null) chips.push([`seed ${evt.seed}`, "seed"]);
    if (g.elapsed) chips.push([`${Number(g.elapsed).toFixed(1)} s`, ""]);
    for (const [t, cls] of chips) meta.appendChild(el("span", "chip " + cls, t));

    card.addEventListener("click", (ev) => {
      if (ev.target.tagName !== "BUTTON" && rec.files.length) {
        openLightbox(rec.files[rec.pos]);
      }
    });
    els.msgs.appendChild(card);
    scrollDown(true);
  }
  for (const f of rec.files) variantIndex.set(f, rec);
  renderCarousel(rec);
}

function renderCarousel(rec) {
  const name = rec.files[rec.pos];
  if (!name) return;
  const img = rec.card.querySelector(".gen-viewer img");
  img.dataset.name = name;
  img.src = imgSrcFor(name);
  rec.card.dataset.file = name;
  const many = rec.files.length > 1;
  rec.card.querySelector(".car-prev").style.visibility =
    many ? "visible" : "hidden";
  rec.card.querySelector(".car-next").style.visibility =
    many ? "visible" : "hidden";
  rec.card.querySelector(".car-count").textContent =
    many ? `${rec.pos + 1}/${rec.files.length}` : "";
}

function slideVariant(rec, d) {
  const n = rec.pos + d;
  if (n < 0 || n >= rec.files.length) return;
  rec.pos = n;
  renderCarousel(rec);
}

function addModels(evt) {
  const d = el("div", "models");
  const b = el("b", null, evt.current_model || "?");
  d.appendChild(document.createTextNode("Installed: "));
  d.appendChild(b);
  d.appendChild(document.createTextNode(` · ${evt.models.length} checkpoints`));
  els.msgs.appendChild(d);
  scrollDown(true);
}

function addToolStart(name) {
  const t = el("div", "tool-start");
  t.appendChild(el("span", "tname", name));
  t.appendChild(el("span", null, "running…"));
  t.dataset.tool = name;
  els.msgs.appendChild(t);
  scrollDown(true);
}

function markToolDone(name) {
  for (const t of els.msgs.querySelectorAll(".tool-start")) {
    if (t.dataset.tool === name && !t.classList.contains("done")) {
      t.classList.add("done");
      t.lastChild.textContent = "";
    }
  }
}

function lastTool() {
  const list = els.msgs.querySelectorAll(".tool-start:not(.done)");
  return list.length ? list[list.length - 1].dataset.tool : "";
}

function renderHistory(timeline) {
  els.msgs.textContent = "";
  variantIndex.clear();
  timeline.forEach((evt, i) => {
    if (evt.type === "user") { const m = addBubble("user", evt.text); m.dataset.idx = i; }
    else if (evt.type === "reply") { const m = addBubble("ai", evt.text); m.dataset.idx = i; }
    else if (evt.type === "error") { const m = addBubble("error", evt.text); m.dataset.idx = i; }
    else if (evt.type === "tool_error") { const m = addBubble("error", `✗ ${evt.name} — ${evt.error}`); m.dataset.idx = i; }
    else if (evt.type === "generation") { addGeneration(evt, i); markToolDone(lastTool()); }
    else if (evt.type === "models") { addModels(evt); els.msgs.lastChild.dataset.idx = i; }
    else if (evt.type === "tool_start") { addToolStart(evt.name); els.msgs.lastChild.dataset.idx = i; }
  });
  if (!timeline.length) {
    const hint = el("div", "empty");
    hint.innerHTML = '<div class="empty-spark">✦</div>Describe an image and the agent will draw it.';
    els.msgs.appendChild(hint);
  }
  evtCounter = timeline.length;
  scrollDown(false);
}

/* -------------------------------------------------------- status / pill */

function pill(text, showTrack) {
  els.pill.hidden = false;
  els.pill.classList.toggle("track-hidden", !showTrack);
  els.pillText.textContent = text;
  if (!showTrack) els.pillBar.style.width = "0%";
  scrollDown(true);
}

function pillProgress(pct, text) {
  els.pill.hidden = false;
  els.pill.classList.remove("track-hidden");
  els.pillBar.style.width = Math.min(100, Math.max(2, pct * 100)).toFixed(1) + "%";
  els.pillText.textContent = text;
}

function pillHide() {
  els.pill.hidden = true;
  els.pillBar.style.width = "0%";
}

function startProgressPolling() {
  if (statusTimer) return;
  statusTimer = setInterval(async () => {
    try {
      const p = await api("/api/sd_progress_proxy");
    } catch { /* unused fallback */ }
  }, 5000);
}

function stopProgressPolling() {
  clearInterval(statusTimer);
  statusTimer = null;
}

/* ------------------------------------------------------------------ chat */

function setBusy(b) {
  busy = b;
  els.send.classList.toggle("busy", b);
  els.send.classList.toggle("ready", !b && els.input.value.trim().length > 0);
}

async function sendMessage() {
  const text = els.input.value.trim();
  if (!text || busy) return;
  els.input.value = "";
  autosize();
  await sendText(text);
}

async function sendText(text) {
  if (!text || busy) return;
  setBusy(true);
  switchView("chat");

  // remove empty-state hint if present
  const hint = els.msgs.querySelector(".empty");
  if (hint) hint.remove();

  addBubble("user", text);
  scrollDown(true);
  pill("thinking…", false);

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
    });
    if (resp.status === 401) { showGate(); return; }
    if (resp.status === 400) {
      const d = await resp.json().catch(() => ({}));
      pillHide();
      if (d.error === "no_api_key") {
        addBubble("error", d.message || "Add your OpenRouter API key in Settings.");
        openSettings();
      } else {
        addBubble("error", d.error || d.message || "Request failed.");
      }
      return;
    }
    if (resp.status === 409) {
      pillHide();
      addBubble("error", "The agent is still working on the previous request.");
      return;
    }
    await consumeSSE(resp);
  } catch (e) {
    if (e.message === "locked") return;
    pillHide();
    addBubble("error", "Connection lost — " + e.message +
      " (the turn keeps running on the server; refresh to catch up)");
  } finally {
    pillHide();
    setBusy(false);
    refreshStatus();
  }
}

function consumeSSE(resp, handler) {
  const handle = handler || handleEvent;
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  const next = () => reader.read().then(({ done, value }) => {
    if (done) return;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const line = buf.slice(0, i).trim();
      buf = buf.slice(i + 2);
      if (!line.startsWith("data:")) continue;
      let evt;
      try { evt = JSON.parse(line.slice(5)); } catch { continue; }
      handle(evt);
    }
    return next();
  });
  return next();
}

let evtCounter = 0;       // next timeline index for live events

function tagIdx(node) {
  node.dataset.idx = evtCounter++;
  return node;
}

function handleEvent(evt) {
  switch (evt.type) {
    case "user":
      evtCounter++;
      break; // already shown
    case "status":
      pill(esc(evt.text), false);
      break;
    case "progress": {
      const pct = Math.round((evt.progress || 0) * 100);
      let t = `generating ${pct}%`;
      if (evt.eta != null && evt.eta > 0) t += ` · ~${Math.ceil(evt.eta)}s`;
      pillProgress(evt.progress || 0, t);
      break;
    }
    case "tool_start":
      markToolDone(lastTool());
      addToolStart(evt.name);
      tagIdx(els.msgs.lastChild);
      pill(evt.name + "…", false);
      break;
    case "generation":
      markToolDone(evt.gen && (evt.gen.denoising_strength != null) ? "edit_image" : "generate_image");
      addGeneration(evt, evtCounter++);
      pill("done — loading images…", false);
      break;
    case "models":
      markToolDone("list_sd_models");
      addModels(evt);
      tagIdx(els.msgs.lastChild);
      break;
    case "tool_error":
      markToolDone(lastTool());
      addBubble("error", `✗ ${evt.name} — ${evt.error}`);
      break;
    case "reply":
      stopProgressPolling();
      markToolDone(lastTool());
      tagIdx(addBubble("ai", evt.text));
      break;
    case "error":
      stopProgressPolling();
      markToolDone(lastTool());
      tagIdx(addBubble("error", evt.text));
      break;
    case "done":
      stopProgressPolling();
      pillHide();
      break;
  }
  if (evt.type !== "progress") scrollDown(true);
}

/* --------------------------------------------------------------- gallery */

async function loadGallery() {
  try {
    const d = await api("/api/gallery");
    images = d.images || [];
    renderGallery();
  } catch (e) {
    if (e.message !== "locked") toast("Gallery failed: " + e.message, true);
  }
}

function renderGallery() {
  els.grid.textContent = "";
  els.gcount.textContent = images.length
    ? `${images.length} image${images.length === 1 ? "" : "s"}`
    : "";
  els.gempty.hidden = images.length > 0;
  for (const im of images) {
    const img = document.createElement("img");
    img.src = "/thumb/" + im.name;
    img.loading = "lazy";
    img.alt = im.name;
    img.title = im.name;
    img.addEventListener("click", () => openLightbox(im.name));
    els.grid.appendChild(img);
  }
}

/* -------------------------------------------------------------- lightbox */

let lbIndex = -1;

function openLightbox(name) {
  lbIndex = images.findIndex((x) => x.name === name);
  if (lbIndex < 0) { lbIndex = 0; images.unshift({ name, seed: null }); }
  showLightbox();
  els.lb.hidden = false;
}

function closeLightbox() { els.lb.hidden = true; }

function showLightbox() {
  const im = images[lbIndex];
  if (!im) return;
  els.lbName.textContent = im.name;
  els.lbImg.src = "/outputs/" + im.name;
  els.lbPrev.style.visibility = lbIndex > 0 ? "visible" : "hidden";
  els.lbNext.style.visibility = lbIndex < images.length - 1 ? "visible" : "hidden";

  els.lbMeta.textContent = "";
  els.lbPrompt.textContent = "";
  els.lbNeg.textContent = "";
  els.lbParams.textContent = "";
  [els.lbPromptWrap, els.lbNegWrap, els.lbParamsWrap].forEach((w) => { w.hidden = true; });

  api("/api/image_info?name=" + encodeURIComponent(im.name)).then((info) => {
    const meta = els.lbMeta;
    const add = (t, cls) => { if (t) meta.appendChild(el("span", "chip " + (cls || ""), t)); };
    if (info.width) add(`${info.width}×${info.height}`);
    if (info.seed) add(`seed ${info.seed}`, "seed");
    if (info.bytes) add(fmtBytes(info.bytes));
    if (info.mtime) add(new Date(info.mtime * 1000).toLocaleString());
    if (info.prompt) { els.lbPrompt.textContent = info.prompt; els.lbPromptWrap.hidden = false; }
    if (info.negative) { els.lbNeg.textContent = info.negative; els.lbNegWrap.hidden = false; }
    if (info.params) { els.lbParams.textContent = info.params; els.lbParamsWrap.hidden = false; }
    im.seed = info.seed || im.seed;
  }).catch(() => { /* metadata is optional */ });
}

function lbMove(d) {
  const ni = lbIndex + d;
  if (ni >= 0 && ni < images.length) { lbIndex = ni; showLightbox(); }
}

/* -------------------------------------------------------------- settings */

async function openSettings() {
  els.settings.hidden = false;
  try {
    // refresh=1 → server asks SD to rescan its models dir, fresh list
    const s = await api("/api/status?refresh=1");
    els.shSdok.textContent = s.sd_ok ? "· connected" : "· unreachable";
    els.shCur.textContent = s.current_model ? `· now: ${s.current_model}` : "";
    els.shLlm.value = (s.llm || []).join(", ");
    els.shModels.textContent = "";
    if (s.models.length) {
      for (const m of s.models) {
        const o = el("option", null, m);
        o.value = m;
        if (m === s.current_model) o.selected = true;
        els.shModels.appendChild(o);
      }
      els.shLoad.disabled = false;
      els.shModelhint = undefined; // keep hint as-is
    } else {
      els.shModels.appendChild(el("option", null, s.sd_ok ? "(no models)" : "(server unreachable)"));
      els.shLoad.disabled = true;
    }
    // masked key display (GET /api/settings)
    try {
      const cfg = await api("/api/settings");
      els.shKeymask.textContent = cfg.key_masked ? `· ${cfg.key_masked}` : "· not set";
      els.shSd.value = cfg.sd_url || "";
      if (!els.shLlm.value) els.shLlm.value = (cfg.llm || []).join(", ");
      els.shSys.value = cfg.system_prompt || "";
      els.shSysState.textContent = cfg.system_prompt_custom ? "· custom" : "· default";
    } catch { /* optional */ }
  } catch (e) {
    if (e.message !== "locked") toast("Status failed: " + e.message, true);
  }
}

async function saveSettings(patch, btn, okMsg) {
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Saving…";
  try {
    const d = await api("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    toast(okMsg);
    refreshStatus();
    return d;
  } catch (e) {
    if (e.message !== "locked") toast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}

async function loadModel() {
  const title = els.shModels.value;
  if (!title || els.shLoad.disabled) return;
  els.shLoad.disabled = true;
  els.shLoad.textContent = "Loading…";
  try {
    const d = await api("/api/model", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: title }),
    });
    els.shCur.textContent = `· now: ${d.current_model || title}`;
    els.modelname.textContent = d.current_model || title;
    toast("Checkpoint loaded");
  } catch (e) {
    if (e.message !== "locked") toast("Load failed: " + e.message, true);
  } finally {
    els.shLoad.disabled = false;
    els.shLoad.textContent = "Load";
    refreshStatus();
  }
}

/* ---------------------------------------------------------------- status */

async function refreshStatus() {
  try {
    const s = await api("/api/status");
    els.dot.classList.toggle("on", !!s.sd_ok);
    els.dot.classList.toggle("off", !s.sd_ok);
    els.modelname.textContent = s.sd_ok
      ? (s.current_model || "no checkpoint")
      : "server unreachable";
    return s;
  } catch (e) {
    if (e.message === "locked") return;
    els.dot.className = "dot off";
    els.modelname.textContent = "offline";
  }
}

/* ----------------------------------------------------------------- views */

function switchView(v) {
  for (const t of document.querySelectorAll(".tab")) {
    t.classList.toggle("active", t.dataset.view === v);
  }
  els.viewChat.hidden = v !== "chat";
  els.viewGallery.hidden = v !== "gallery";
  els.composer.hidden = v !== "chat";
  if (v === "gallery") loadGallery();
}

/* ------------------------------------------------------------- appearance */

// compact shows native-resolution images; verbose uses small thumbnails
function imgSrcFor(name) {
  const compact = document.body.classList.contains("compact");
  return (compact ? "/outputs/" : "/thumb/") + name;
}

function refreshGenSrcs() {
  for (const img of els.msgs.querySelectorAll(".gen-viewer img")) {
    if (img.dataset.name) img.src = imgSrcFor(img.dataset.name);
  }
}

function applyAppearance() {
  const mode = localStorage.getItem("appearance") || "verbose";
  document.body.classList.toggle("compact", mode === "compact");
  for (const b of document.querySelectorAll("#sh-appearance button")) {
    b.classList.toggle("on", b.dataset.mode === mode);
  }
  refreshGenSrcs();
}

for (const b of document.querySelectorAll("#sh-appearance button")) {
  b.addEventListener("click", () => {
    localStorage.setItem("appearance", b.dataset.mode);
    applyAppearance();
    toast(b.dataset.mode === "compact" ? "Compact mode" : "Verbose mode");
  });
}

/* ------------------------------------------------------------------ misc */

function autosize() {
  const t = els.input;
  // collapse to 0 first so scrollHeight reports true CONTENT height
  // (measuring at the current height can return the box height on mobile)
  t.style.height = "0px";
  // +2px for the 1px top/bottom borders (border-box)
  t.style.height = Math.min(t.scrollHeight + 2, 110) + "px";
}

async function newChat() {
  try {
    await api("/api/clear", { method: "POST" });
    els.msgs.textContent = "";
    renderHistory([]);
    toast("New chat");
  } catch (e) {
    if (e.message !== "locked") toast("Failed: " + e.message, true);
  }
}

function copyText(text) {
  navigator.clipboard.writeText(text)
    .then(() => toast("Copied"))
    .catch(() => toast("Copy failed", true));
}

async function deleteImage(name) {
  if (!confirm("Delete " + name + "?")) return;
  try {
    await api("/api/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    images.splice(lbIndex, 1);
    if (!images.length) closeLightbox();
    else if (lbIndex >= images.length) lbIndex = images.length - 1;
    if (els.lb.hidden) loadGallery(); else showLightbox();
    toast("Deleted");
  } catch (e) {
    if (e.message !== "locked") toast("Delete failed: " + e.message, true);
  }
}

/* --------------------------------------------- context menu (hold / right-click) */

const ctxEl = $("ctx"), ctxCard = $("ctx-card");
let lpTimer = null, lpStart = null;

function openCtx(items, previewSrc) {
  ctxCard.textContent = "";
  if (previewSrc) {
    const pv = el("div", "ctx-preview");
    const img = document.createElement("img");
    img.src = previewSrc;
    pv.appendChild(img);
    ctxCard.appendChild(pv);
  }
  for (const it of items) {
    const b = el("button", it.danger ? "danger" : "", it.label);
    b.addEventListener("click", () => { closeCtx(); it.action(); });
    ctxCard.appendChild(b);
  }
  ctxEl.hidden = false;
}

function closeCtx() { ctxEl.hidden = true; }

ctxEl.addEventListener("click", (e) => { if (e.target === ctxEl) closeCtx(); });

function ctxItemsFor(el) {
  if (el.classList.contains("gen")) {
    const name = el.dataset.file;
    const items = [];
    if (name) items.push({ label: "↻ Regenerate", action: () => openRegen(name) });
    if (name) items.push({ label: "✎ Edit in chat", action: () => {
      switchView("chat");
      els.input.value = `Edit ${name} — `;
      autosize(); els.input.focus(); els.send.classList.add("ready");
    }});
    items.push({ label: "🗑 Delete from chat", danger: true, action: () => deleteChatEvent(el) });
    return { items, previewSrc: name ? "/thumb/" + name : null };
  }
  if (el.classList.contains("msg")) {
    const items = [];
    if (!el.classList.contains("error")) {
      items.push({ label: "⧉ Copy", action: () => copyText(el.textContent) });
    }
    items.push({ label: "🗑 Delete", danger: true, action: () => deleteChatEvent(el) });
    return { items, previewSrc: null };
  }
  return null;
}

async function deleteChatEvent(node) {
  const idx = node.dataset.idx;
  if (idx == null) { node.remove(); return; }
  let idxs = [+idx], variants = false;
  if (node.classList.contains("gen")) {
    const rec = variantIndex.get(node.dataset.file);
    if (rec && rec.card === node && rec.evtIdxs.length > 1) {
      variants = true;
      idxs = rec.evtIdxs.slice();
    }
  }
  if (!confirm(variants
    ? "Remove this image and its regenerations from the chat?"
    : "Remove this from the chat?")) return;
  try {
    await api("/api/delete_event", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ indices: idxs }),
    });
    if (variants) {
      const rec = variantIndex.get(node.dataset.file);
      if (rec && rec.card === node) {
        for (const f of rec.files) variantIndex.delete(f);
      }
    }
    node.remove();
  } catch (e) {
    if (e.message !== "locked") toast("Delete failed: " + e.message, true);
  }
}

els.msgs.addEventListener("contextmenu", (e) => {
  const target = e.target.closest(".gen, .msg");
  if (!target) return;
  e.preventDefault();
  const ctx = ctxItemsFor(target);
  if (ctx) openCtx(ctx.items, ctx.previewSrc);
});

els.msgs.addEventListener("touchstart", (e) => {
  const target = e.target.closest(".gen, .msg");
  if (!target || busy) return;
  lpStart = { x: e.touches[0].clientX, y: e.touches[0].clientY, target };
  lpTimer = setTimeout(() => {
    lpTimer = null;
    if (navigator.vibrate) navigator.vibrate(25);
    const ctx = ctxItemsFor(lpStart.target);
    if (ctx) openCtx(ctx.items, ctx.previewSrc);
    lpStart = null;
  }, 550);
}, { passive: true });

els.msgs.addEventListener("touchmove", (e) => {
  if (!lpTimer || !lpStart) return;
  const dx = e.touches[0].clientX - lpStart.x;
  const dy = e.touches[0].clientY - lpStart.y;
  if (dx * dx + dy * dy > 144) { clearTimeout(lpTimer); lpTimer = null; }
}, { passive: true });

els.msgs.addEventListener("touchend", () => {
  if (lpTimer) { clearTimeout(lpTimer); lpTimer = null; }
}, { passive: true });

/* ------------------------------------------------------- regenerate sheet */

let regenName = null;
const regenEl = $("regen"), regenText = $("regen-text");

function openRegen(name) {
  regenName = name;
  regenText.value = "";
  regenEl.hidden = false;
  setTimeout(() => regenText.focus(), 60);
}

$("regen-cancel").addEventListener("click", () => { regenEl.hidden = true; });
regenEl.addEventListener("click", (e) => { if (e.target === regenEl) regenEl.hidden = true; });
$("regen-go").addEventListener("click", () => {
  const name = regenName, instr = regenText.value.trim();
  regenEl.hidden = true;
  if (!name) return;
  runRegen(name, instr);
});
regenText.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    $("regen-go").click();
  }
});

/* ------------------------------------- direct regeneration (no chat message) */

async function runRegen(name, instr) {
  if (busy) { toast("Busy — wait for the current job", true); return; }
  setBusy(true);
  switchView("chat");
  pill(instr ? "applying change…" : "regenerating…", false);
  try {
    const resp = await fetch("/api/regenerate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, instruction: instr }),
    });
    if (resp.status === 401) { showGate(); return; }
    if (resp.status === 409) {
      pillHide();
      toast("The agent is still working — try again shortly", true);
      return;
    }
    if (!resp.ok) {
      const d = await resp.json().catch(() => ({}));
      pillHide();
      toast(d.error || "Regeneration failed", true);
      return;
    }
    await consumeSSE(resp, (evt) => {
      switch (evt.type) {
        case "progress": {
          const pct = Math.round((evt.progress || 0) * 100);
          let t = `regenerating ${pct}%`;
          if (evt.eta != null && evt.eta > 0) t += ` · ~${Math.ceil(evt.eta)}s`;
          pillProgress(evt.progress || 0, t);
          break;
        }
        case "regen_done": {
          const rec = variantIndex.get(evt.src);
          if (rec) {
            if (!rec.files.includes(evt.file)) rec.files.push(evt.file);
            if (evt.idx != null) rec.evtIdxs.push(evt.idx);
            rec.pos = rec.files.length - 1;      // show the new variant
            renderCarousel(rec);
            rec.card.scrollIntoView({ behavior: "smooth", block: "center" });
          }
          evtCounter++;                          // timeline gained one event
          if (!images.some((x) => x.name === evt.file)) {
            images.unshift({ name: evt.file, seed: evt.seed });
          }
          toast("Image regenerated");
          break;
        }
        case "regen_error":
          pillHide();
          toast(evt.error, true);
          break;
        case "done":
          pillHide();
          break;
      }
    });
  } catch (e) {
    if (e.message === "locked") return;
    pillHide();
    toast("Connection lost — " + e.message, true);
  } finally {
    setBusy(false);
    refreshStatus();
  }
}

/* ----------------------------------------------------------------- wires */

els.send.addEventListener("click", sendMessage);
els.input.addEventListener("input", () => {
  autosize();
  els.send.classList.toggle("ready", els.input.value.trim().length > 0);
});
els.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

for (const t of document.querySelectorAll(".tab")) {
  t.addEventListener("click", () => switchView(t.dataset.view));
}
$("btn-refresh").addEventListener("click", loadGallery);
$("btn-new").addEventListener("click", newChat);
$("btn-settings").addEventListener("click", openSettings);
$("sh-close").addEventListener("click", () => { els.settings.hidden = true; });
els.settings.addEventListener("click", (e) => {
  if (e.target === els.settings) els.settings.hidden = true;
});
$("sh-save-key").addEventListener("click", () => {
  const v = els.shKey.value.trim();
  if (v) saveSettings({ openrouter_key: v }, $("sh-save-key"), "API key saved")
    .then(() => { els.shKey.value = ""; });
});
$("sh-save-llm").addEventListener("click", () => {
  const v = els.shLlm.value.trim();
  if (v) saveSettings({ llm: v }, $("sh-save-llm"), "LLM chain saved");
});
$("sh-save-sd").addEventListener("click", () => {
  const v = els.shSd.value.trim();
  if (v) saveSettings({ sd_url: v }, $("sh-save-sd"), "SD URL saved");
});
$("sh-save-sys").addEventListener("click", () => {
  const v = els.shSys.value.trim();
  if (!v) {
    toast("Clear the box or use Reset to go back to the default", true);
    return;
  }
  saveSettings({ system_prompt: v }, $("sh-save-sys"), "System message saved")
    .then((d) => {
      if (d) els.shSysState.textContent =
        d.system_prompt_custom ? "· custom" : "· default";
    });
});
$("sh-reset-sys").addEventListener("click", async () => {
  if (!confirm("Restore the default system message?")) return;
  const btn = $("sh-reset-sys"), orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Resetting…";
  try {
    const d = await api("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ system_prompt_reset: true }),
    });
    els.shSys.value = d.system_prompt || "";
    els.shSysState.textContent = "· default";
    toast("System message reset to default");
  } catch (e) {
    if (e.message !== "locked") toast("Reset failed: " + e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
});
$("sh-load").addEventListener("click", loadModel);
$("sh-logout").addEventListener("click", async () => {
  try { await api("/api/logout", { method: "POST" }); } catch { /* ignore */ }
  showGate();
});

els.lbClose.addEventListener("click", closeLightbox);
els.lbPrev.addEventListener("click", () => lbMove(-1));
els.lbNext.addEventListener("click", () => lbMove(1));
$("lb-edit").addEventListener("click", () => {
  const im = images[lbIndex];
  if (!im) return;
  closeLightbox();
  switchView("chat");
  els.input.value = `Edit ${im.name} — `;
  autosize();
  els.input.focus();
  els.send.classList.add("ready");
});
$("lb-download").addEventListener("click", () => {
  const im = images[lbIndex];
  if (im) {
    const a = document.createElement("a");
    a.href = "/outputs/" + im.name;
    a.download = im.name;
    a.click();
  }
});
$("lb-delete").addEventListener("click", () => {
  const im = images[lbIndex];
  if (im) deleteImage(im.name);
});
for (const b of document.querySelectorAll(".copybtn")) {
  b.addEventListener("click", () => copyText($(b.dataset.copy).textContent));
}

// swipe between gallery images
let touchX = null;
els.lbStage.addEventListener("touchstart", (e) => { touchX = e.touches[0].clientX; }, { passive: true });
els.lbStage.addEventListener("touchend", (e) => {
  if (touchX == null) return;
  const dx = e.changedTouches[0].clientX - touchX;
  if (Math.abs(dx) > 50) lbMove(dx < 0 ? 1 : -1);
  touchX = null;
}, { passive: true });

document.addEventListener("keydown", (e) => {
  if (!els.lb.hidden) {
    if (e.key === "Escape") closeLightbox();
    if (e.key === "ArrowLeft") lbMove(-1);
    if (e.key === "ArrowRight") lbMove(1);
  } else if (e.key === "Escape" && !els.settings.hidden) {
    els.settings.hidden = true;
  } else if (e.key === "Escape" && !ctxEl.hidden) {
    closeCtx();
  } else if (e.key === "Escape" && !regenEl.hidden) {
    regenEl.hidden = true;
  }
});

/* ------------------------------------------------------------------ init */

let inited = false;

async function init() {
  if (inited) return;
  inited = true;
  switchView("chat");                     // ensure chat + composer are shown
  applyAppearance();
  autosize();
  refreshStatus();
  try {
    const h = await api("/api/history");
    if (h.timeline && h.timeline.length) {
      renderHistory(h.timeline);
      images = timelineImages(h.timeline);
    } else {
      renderHistory([]);
    }
    if (h.busy) { setBusy(true); pill("catching up…", false); }
    const s = await refreshStatus();
    if (s && !s.has_key) {
      addBubble("error", "Welcome! Add your OpenRouter API key in Settings (⚙) to start generating.");
      openSettings();
    }
  } catch { /* fresh start */ }
  setInterval(refreshStatus, 30000);
}

// pull image names out of a restored timeline (newest first)
function timelineImages(timeline) {
  const seen = new Set();
  const out = [];
  for (const evt of timeline) {
    if (evt.type !== "generation") continue;
    for (const f of evt.files || []) {
      const n = f.split("/").pop();
      if (!seen.has(n)) { seen.add(n); out.push({ name: n, seed: evt.seed }); }
    }
  }
  return out.reverse();
}

/* ------------------------------------------------------------ bootstrap */

(async function bootstrap() {
  try {
    const a = await api("/api/auth");
    if (a.authed) showApp();
    else showGate();
  } catch {
    showGate();
  }
})();

/* --------------------------------------------------------------- PWA SW */

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => { /* optional */ });

    // When a new service worker takes over (e.g. right after an update),
    // this page was rendered with possibly-stale assets — reload once.
    navigator.serviceWorker.addEventListener("message", (e) => {
      if (e.data && e.data.type === "sw-takeover" &&
          !sessionStorage.getItem("sw-reloaded")) {
        sessionStorage.setItem("sw-reloaded", "1");
        location.reload();
      }
    });
  });
}
