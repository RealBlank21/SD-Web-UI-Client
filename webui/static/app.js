/* SD Agent — frontend logic (PWA) */
"use strict";

const $ = (id) => document.getElementById(id);

const els = {
  gate: $("gate"), gatePw: $("gate-pw"), gateBtn: $("gate-btn"),
  gateErr: $("gate-err"), app: $("app"),
  viewChars: $("view-chars"), viewChat: $("view-chat"),
  pageGallery: $("page-gallery"),
  charSearch: $("char-search"), charlist: $("charlist"),
  charempty: $("charempty"),
  chatScroll: $("chat-scroll"), chatCharname: $("chat-charname"),
  btnHistory: $("btn-history"), pageHist: $("page-hist"),
  btnAutoimg: $("btn-autoimg"),
  pageChform: $("page-chform"), chTitle: $("ch-title"),
  cfBack: $("cf-back"), cfSave: $("cf-save"),
  hsBack: $("hs-back"), hsNew: $("hs-new"), hsList: $("hs-list"),
  hsTitle: $("hs-title"), hsSelect: $("hs-select"),
  hsAll: $("hs-all"), hsDel: $("hs-del"),
  msgs: $("msgs"), pill: $("pill"), pillText: $("pill-text"), pillBar: $("pill-bar"),
  input: $("input"), send: $("btn-send"),
  grid: $("grid"), gcount: $("gcount"), gempty: $("gempty"),
  gfolders: $("gfolders"), gcrumbs: $("pg-crumbs"), gtotal: $("gtotal"),
  gemptyText: $("gempty-text"),
  pgTitle: $("pg-title"), pgNewFolder: $("pg-newfolder"),
  pgSelect: $("pg-select"), pgAll: $("pg-all"), pgMove: $("pg-move"),
  pgDel: $("pg-del"), pgTag: $("pg-tag"),
  gSearch: $("g-search"),
  foldSheet: $("foldsheet"), foldTitle: $("fold-title"), foldSub: $("fold-sub"),
  foldName: $("fold-name"), foldOk: $("fold-ok"), foldCancel: $("fold-cancel"),
  foldPick: $("foldpick"), fpTitle: $("fp-title"), fpList: $("fp-list"),
  fpCancel: $("fp-cancel"),
  composer: $("composer"),
  lb: $("lightbox"), lbImg: $("lb-img"), lbName: $("lb-name"),
  lbPrev: $("lb-prev"), lbNext: $("lb-next"), lbClose: $("lb-close"),
  lbInfo: $("lb-info"), lbSheet: $("lb-sheet"),
  lbMeta: $("lb-meta"), lbPrompt: $("lb-prompt"), lbNeg: $("lb-neg"),
  lbParams: $("lb-params"),
  lbPromptWrap: $("lb-prompt-wrap"), lbNegWrap: $("lb-neg-wrap"),
  lbParamsWrap: $("lb-params-wrap"), lbStage: $("lb-stage"),
  pageSettings: $("page-settings"), shModels: $("sh-models"), shLoad: $("sh-load"),
  psList: $("ps-list"), psCount: $("ps-count"),
  pagePersona: $("page-persona"), ppTitle: $("pp-title"),
  ppName: $("pp-name"), ppDesc: $("pp-desc"), ppDelete: $("pp-delete"),
  scList: $("sc-list"), scCount: $("sc-count"),
  pageScenario: $("page-scenario"), scsTitle: $("scs-title"),
  scsName: $("scs-name"), scsDesc: $("scs-desc"), scsFirst: $("scs-first"),
  scsDelete: $("scs-delete"),
  shCur: $("sh-cur"), shSd: $("sh-sd"), shLlm: $("sh-llm"), shKey: $("sh-key"),
  shKeymask: $("sh-keymask"), shSdok: $("sh-sdok"),
  shArch: $("sh-arch"), shComps: $("sh-comps"),
  shCompActions: $("sh-comp-actions"),
  shUsername: $("sh-username"),
  shSys: $("sh-sysprompt"), shSysState: $("sh-sysstate"),
  chName: $("ch-name"),
  chAvatarImg: $("ch-avatar-img"), chAvatarBtn: $("ch-avatar-btn"),
  chAvatarFile: $("ch-avatar-file"), chAvatarAi: $("ch-avatar-ai"),
  chAvatarClear: $("ch-avatar-clear"),
  chCoverImg: $("ch-cover-img"), chCoverBtn: $("ch-cover-btn"),
  chCoverFile: $("ch-cover-file"), chCoverAi: $("ch-cover-ai"),
  chCoverClear: $("ch-cover-clear"), chAiFab: $("ch-aifab"),
  aiSheet: $("aisheet"), aiTitle: $("ai-title"), aiHint: $("ai-hint"),
  aiText: $("ai-text"), aiGo: $("ai-go"), aiCancel: $("ai-cancel"),
  scsCoverImg: $("scs-cover-img"), scsCoverBtn: $("scs-cover-btn"),
  scsCoverFile: $("scs-cover-file"), scsCoverAi: $("scs-cover-ai"),
  scsCoverClear: $("scs-cover-clear"),
  chAppearance: $("ch-appearance"), chPersona: $("ch-persona"),
  chGreeting: $("ch-greeting"), chModel: $("ch-model"), chSize: $("ch-size"),
  chTemp: $("ch-temp"), chMaxtok: $("ch-maxtok"),
  chDelete: $("ch-delete"),
  toast: $("toast"),
};

let images = [];          // gallery list
let busy = false;
let statusTimer = null;   // progress polling while generating
let toastTimer = null;
let authed = false;
let chars = [];           // character cards
let activeCharId = "";

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

/* markdown → safe HTML (AI bubbles). Escape everything first, then apply a
   compact renderer: headings, lists, blockquotes, hr, fenced + inline code,
   bold, italic, strike, http(s) links. Single newlines become <br> —
   roleplay replies rely on line breaks. */

function escHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;",
      '"': "&quot;", "'": "&#39;" }[c]));
}

function mdInline(t) {
  // t is HTML-escaped, code spans already lifted out as placeholders
  t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/__([^_]+)__/g, "<strong>$1</strong>");
  t = t.replace(/(^|[^*\w])\*([^*]+)\*/g, "$1<em>$2</em>");
  t = t.replace(/(^|[^_\w])_([^_]+)_/g, "$1<em>$2</em>");
  t = t.replace(/~~([^~]+)~~/g, "<del>$1</del>");
  t = t.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  return t;
}

function mdToHtml(src) {
  const blocks = [];
  let t = escHtml(src);
  t = t.replace(/```[^\n]*\n?([\s\S]*?)(?:```|$)/g, (_, code) => {
    blocks.push("<pre><code>" + code.replace(/\n+$/, "") + "</code></pre>");
    return "\u0000B" + (blocks.length - 1) + "\u0000";
  });
  t = t.replace(/`([^`\n]+)`/g, (_, c) => {
    blocks.push("<code>" + c + "</code>");
    return "\u0000C" + (blocks.length - 1) + "\u0000";
  });

  const out = [];
  let para = [];
  let quote = [];
  let list = null;                      // "ul" | "ol"
  const flushPara = () => {
    if (para.length) {
      out.push("<p>" + para.map(mdInline).join("<br>") + "</p>");
      para = [];
    }
  };
  const flushQuote = () => {
    if (quote.length) {
      out.push("<blockquote><p>" + quote.map(mdInline).join("<br>") +
        "</p></blockquote>");
      quote = [];
    }
  };
  const closeList = () => { if (list) { out.push("</" + list + ">"); list = null; } };

  for (const line of t.split("\n")) {
    const bm = line.match(/^\u0000B(\d+)\u0000\s*$/);
    if (bm) {
      flushPara(); flushQuote(); closeList();
      out.push(blocks[+bm[1]]);
      continue;
    }
    if (/^\s*$/.test(line)) { flushPara(); flushQuote(); closeList(); continue; }
    const h = line.match(/^#{1,4}\s+(.+)$/);
    if (h) {
      flushPara(); flushQuote(); closeList();
      out.push("<h4>" + mdInline(h[1]) + "</h4>");
      continue;
    }
    if (/^\s*(---+|\*\*\*+|___+)\s*$/.test(line)) {
      flushPara(); flushQuote(); closeList();
      out.push("<hr>");
      continue;
    }
    const q = line.match(/^\s*&gt;\s?(.*)$/);
    if (q) { flushPara(); closeList(); quote.push(q[1]); continue; }
    const ul = line.match(/^\s*[-*•]\s+(.+)$/);
    const ol = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (ul || ol) {
      flushPara(); flushQuote();
      const want = ul ? "ul" : "ol";
      if (list !== want) { closeList(); out.push("<" + want + ">"); list = want; }
      out.push("<li>" + mdInline((ul || ol)[1]) + "</li>");
      continue;
    }
    closeList();
    para.push(line);
  }
  flushPara(); flushQuote(); closeList();
  return out.join("").replace(/\u0000([BC])(\d+)\u0000/g,
    (_, k, i) => blocks[+i]);
}

function scrollDown(smooth) {
  requestAnimationFrame(() => {
    els.chatScroll.scrollTo({ top: els.chatScroll.scrollHeight, behavior: smooth ? "smooth" : "auto" });
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
  const m = el("div", "msg " + kind);
  m.dataset.raw = text;
  if (kind === "ai" && text) {
    // markdown-rendered; raw text kept for the copy menu
    m.classList.add("md");
    m.innerHTML = mdToHtml(text);
  } else {
    m.textContent = text;
  }
  els.msgs.appendChild(m);
  return m;
}

const variantIndex = new Map();   // image rel path -> generation card record

// '/outputs/<rel>' -> '<rel>'; a bare name passes through (older saves)
function relFromUrl(url) {
  const s = String(url || "");
  return s.startsWith("/outputs/") ? decodeURIComponent(s.slice(9)) : s;
}

function addGeneration(evt, idx) {
  // every file may be gone (its folder was deleted) — keep the event so
  // timeline indices stay stable, but draw nothing for it
  if (!(evt.files || []).length) return;
  const src = evt.src ? relFromUrl(evt.src) : null;
  let rec;
  if (src && variantIndex.has(src)) {
    // a regeneration of an existing image — extend that card's carousel
    rec = variantIndex.get(src);
    if (idx != null) rec.evtIdxs.push(idx);
    for (const f of evt.files || []) {
      const name = relFromUrl(f);
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
      '</div>';
    rec = { card,
            files: (evt.files || []).map(relFromUrl),
            pos: 0, evtIdxs: idx != null ? [idx] : [] };
    rec.card.querySelector(".car-prev").addEventListener("click", (e) => {
      e.stopPropagation();
      slideVariant(rec, -1);
    });
    rec.card.querySelector(".car-next").addEventListener("click", (e) => {
      e.stopPropagation();
      slideVariant(rec, 1);
    });

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

/* opening cover image of a fresh chat (character's or scenario's
   "first image") — static picture, no carousel / regen menu */
function addCoverCard(evt) {
  if (!evt.src) return;
  const card = el("div", "cover-card");
  const img = document.createElement("img");
  img.loading = "lazy";
  img.alt = "";
  img.src = evt.src;
  card.appendChild(img);
  els.msgs.appendChild(card);
  scrollDown(true);
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
    else if (evt.type === "cover") { addCoverCard(evt); }
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

/* Tapping send while a turn is running stops it. The local SSE reader is
 * dropped immediately so the UI unsticks at once; the server flag stops the
 * next LLM round and /sdapi/v1/interrupt cuts a live SD job short. */
let busyStop = false;
let busyAbort = null;

function stopGeneration() {
  if (!busy) return;
  busyStop = true;
  els.send.classList.add("stopping");
  els.send.classList.remove("busy");
  pill("stopping…", false);
  if (busyAbort) { busyAbort.abort(); busyAbort = null; }
  api("/api/abort", { method: "POST" }).catch(() => {});
}

function setBusy(b) {
  busy = b;
  if (!b) { busyStop = false; busyAbort = null; }
  els.send.classList.toggle("busy", b);
  els.send.classList.toggle("stopping", !!busyStop);
  els.send.setAttribute("aria-label", b ? "Stop generating" : "Send");
  els.send.classList.toggle("ready", !b && els.input.value.trim().length > 0);
}

async function sendMessage() {
  if (busy) { stopGeneration(); return; }
  const text = els.input.value.trim();
  if (!text) return;
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

  busyAbort = new AbortController();
  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
      signal: busyAbort.signal,
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
    if (busyStop || e.message === "locked") return;   // we asked for this
    pillHide();
    addBubble("error", "Connection lost — " + e.message +
      " (the turn keeps running on the server; refresh to catch up)");
  } finally {
    busyAbort = null;
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

let evtCounter = 0;       // fallback index for streamed-only events

/* Streamed events carry the index the server gave them ('idx'). Counting
 * them here instead would drift: tool_start / progress / status are streamed
 * but never stored, so a local counter overshoots and every later delete
 * lands out of range ("bad index"). Prefer the server's number. */
function tagIdx(node, idx) {
  node.dataset.idx = idx != null ? idx : evtCounter++;
  return node;
}

function handleEvent(evt) {
  switch (evt.type) {
    case "user":
      if (evt.idx == null) evtCounter++;
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
      pill(evt.name + "…", false);
      break;
    case "generation":
      markToolDone(evt.gen && (evt.gen.denoising_strength != null) ? "edit_image" : "generate_image");
      addGeneration(evt, evt.idx != null ? evt.idx : evtCounter++);
      pill("done — loading images…", false);
      break;
    case "models":
      markToolDone("list_sd_models");
      addModels(evt);
      tagIdx(els.msgs.lastChild, evt.idx);
      break;
    case "tool_error":
      markToolDone(lastTool());
      addBubble("error", `✗ ${evt.name} — ${evt.error}`);
      tagIdx(els.msgs.lastChild, evt.idx);
      break;
    case "reply":
      stopProgressPolling();
      markToolDone(lastTool());
      tagIdx(addBubble("ai", evt.text), evt.idx);
      break;
    case "error":
      stopProgressPolling();
      markToolDone(lastTool());
      tagIdx(addBubble("error", evt.text), evt.idx);
      break;
    case "stopped":
      stopProgressPolling();
      markToolDone(lastTool());
      pillHide();
      addBubble("ai", "*stopped*");
      break;
    case "done":
      stopProgressPolling();
      pillHide();
      break;
  }
  if (evt.type !== "progress") scrollDown(true);
}

/* --------------------------------------------------------------- gallery
 *
 * The gallery mirrors the real folder tree inside outputs/. An image is
 * identified by its "rel" path ("ai_2026….png" at the root,
 * "Anime/portrait.png" in a folder) and the root of outputs/ doubles as the
 * unfiled inbox — new generations always land there.
 */

let galFolder = "";             // current folder rel path ("" = root)
let galFolders = [];            // subfolders of galFolder
let galTree = [];               // every folder, for the move-target picker
let galTotal = 0;
let galSelecting = false;
const galSelected = new Set();  // rels selected in select mode
let galSearchQuery = "";        // comma-separated keywords, "" = not searching
let galSearchSeq = 0;           // ignores stale in-flight searches
let galSearchInfo = null;       // hit counts of the last search

/* Prompt search. Keywords are comma-separated and ANDed, and it walks the
 * whole subtree of the folder being browsed — so searching inside "Venti"
 * never reaches the gallery root. No folder navigation happens here: the
 * grid simply shows flat results from wherever they live. */
function setGalSearch(q, run) {
  galSearchQuery = q || "";
  if (run !== false) runGallerySearch();
}

let galSearchTimer = null;
function onGalSearchInput() {
  clearTimeout(galSearchTimer);
  const v = els.gSearch.value;
  galSearchTimer = setTimeout(() => setGalSearch(v), 220);
}

async function runGallerySearch() {
  const q = galSearchQuery.trim();
  const seq = ++galSearchSeq;
  if (!q) {                       // back to plain folder listing
    galSearchQuery = "";
    return loadGallery();
  }
  try {
    const d = await api("/api/gallery?folder=" + encodeURIComponent(galFolder)
                        + "&q=" + encodeURIComponent(q));
    if (seq !== galSearchSeq) return;         // a newer keystroke won
    images = (d.images || []).map((im) => ({
      rel: im.rel, name: im.name, seed: im.seed, bytes: im.bytes,
      mtime: im.mtime, folder: im.folder,
    }));
    galSearchInfo = { total: d.total || 0, scanned: d.scanned || 0,
                      truncated: !!d.truncated };
    galFolders = [];
    renderGallery();
  } catch (e) {
    if (seq !== galSearchSeq) return;
    if (e.message !== "locked") toast("Search failed: " + e.message, true);
  }
}

function baseName(rel) {
  return String(rel || "").split("/").pop();
}

function joinFolder(parent, name) {
  return parent ? parent + "/" + name : name;
}

async function loadGallery(folder) {
  // only a string navigates (and leaves search mode behind). Anything else —
  // notably the click event a bare addEventListener("click", loadGallery)
  // hands over — is a plain refresh, so it must not clobber galFolder.
  if (typeof folder === "string") {
    galFolder = folder;
    if (galSearchQuery) setGalSearch("", false);
  }
  if (galSearchQuery) return runGallerySearch();
  try {
    const q = "/api/gallery?tree=1&folder=" + encodeURIComponent(galFolder);
    const d = await api(q);
    images = (d.images || []).map((im) => ({
      rel: im.rel, name: im.name, seed: im.seed, bytes: im.bytes,
      mtime: im.mtime,
    }));
    galFolders = d.folders || [];
    galTree = d.tree || [];
    galTotal = d.total || 0;
    // drop selections that are no longer in this folder
    const here = new Set(images.map((im) => im.rel));
    for (const r of [...galSelected]) if (!here.has(r)) galSelected.delete(r);
    renderGallery();
  } catch (e) {
    if (e.message !== "locked") toast("Gallery failed: " + e.message, true);
  }
}

function renderGallery() {
  renderCrumbs();
  renderFolders();
  renderGrid();
  const n = images.length;
  if (galSearchQuery) {
    const total = galSearchInfo ? galSearchInfo.total : n;
    els.gcount.textContent = n
      ? `${total} match${total === 1 ? "" : "es"} for "${galSearchQuery}"`
      : `No matches for "${galSearchQuery}"`;
    els.gtotal.textContent = galSearchInfo && galSearchInfo.truncated
      ? `showing first ${n}` : "";
  } else {
    els.gcount.textContent = n
      ? `${n} image${n === 1 ? "" : "s"}`
      : (galFolder ? "No images"
                  : (galFolders.length ? "No unfiled images" : "0 images"));
    els.gtotal.textContent =
      galFolder && galTotal !== n ? `${galTotal} in gallery` : "";
  }
  // the big empty state only when there is nothing to show at all
  const bare = !n && !galFolders.length;
  els.gempty.hidden = !bare;
  if (bare) {
    els.gemptyText.textContent = galSearchQuery
      ? "Nothing here matches. Try fewer keywords, or search from Gallery."
      : (galFolder
        ? "This folder is empty."
        : "No images yet. Ask the agent to draw something.");
  }
  els.pgSelect.hidden = galSelecting || !n;
  // filing by keyword acts on the folder you are browsing, which is
  // ambiguous while a recursive search is on screen
  els.pgTag.hidden = galSelecting || !n || !!galSearchQuery;
  galTitleSync();
}

function renderCrumbs() {
  const bar = els.gcrumbs;
  bar.textContent = "";
  const segs = galFolder ? galFolder.split("/") : [];
  const root = el("button", null, "Gallery");
  root.addEventListener("click", () => loadGallery(""));
  root.classList.toggle("here", !segs.length);
  bar.appendChild(root);
  let acc = "";
  segs.forEach((s, i) => {
    acc = joinFolder(acc, s);
    const last = i === segs.length - 1;
    const sep = el("span", "sep", "/");
    bar.appendChild(sep);
    if (last) {
      bar.appendChild(el("span", "here", s));
    } else {
      const b = el("button", null, s);
      const to = acc;
      b.addEventListener("click", () => loadGallery(to));
      bar.appendChild(b);
    }
  });
}

function renderFolders() {
  els.gfolders.textContent = "";
  if (!galFolders.length) return;
  for (const f of galFolders) {
    const rel = joinFolder(galFolder, f.name);
    const t = el("button", "gtile");
    t.innerHTML =
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';
    const txt = el("div", "gt-text");
    txt.appendChild(el("b", null, f.name));
    txt.appendChild(el("span", null, f.count === 1 ? "1 image" : f.count + " images"));
    t.appendChild(txt);
    t.dataset.folder = rel;
    t.addEventListener("click", () => {
      if (galSelecting) { toast("Exit select mode first", true); return; }
      loadGallery(rel);
    });
    els.gfolders.appendChild(t);
  }
}

function renderGrid() {
  els.grid.textContent = "";
  images.forEach((im, i) => {
    const cell = el("div", "gcell");
    const img = document.createElement("img");
    img.src = "/thumb/" + im.rel;
    // eager for the first screenful (a lazy image inserted as the page
    // reveals can be left unfetched), lazy for the long tail
    if (i >= 24) img.loading = "lazy";
    img.decoding = "async";
    img.alt = im.name;
    img.title = im.name;
    // on the cell, not the img, so the ✓ badge is tappable too
    cell.addEventListener("click", () => {
      if (galSelecting) { toggleGalPick(im.rel, cell); return; }
      openLightbox(im.rel);
    });
    cell.appendChild(el("span", "g-check", "✓"));
    cell.appendChild(img);
    cell.dataset.rel = im.rel;
    cell.dataset.name = im.name;
    // a search hit can live in a subfolder — label it with the path from
    // the folder being browsed, so the grid stays self-explanatory
    if (im.folder !== undefined && im.folder !== galFolder) {
      const rel = galFolder && im.folder.startsWith(galFolder + "/")
        ? im.folder.slice(galFolder.length + 1) + "/"
        : (im.folder ? im.folder + "/" : "");
      if (rel) cell.appendChild(el("span", "g-loc", rel));
    }
    if (galSelected.has(im.rel)) cell.classList.add("sel");
    els.grid.appendChild(cell);
  });
}

/* ---- select mode (mirror of the history page) ---- */

function galTitleSync() {
  const n = galSelected.size;
  els.pgTitle.textContent = galSelecting
    ? (n ? `${n} selected` : "Select images")
    : (galSearchQuery ? "Search"
                     : (galFolder ? baseName(galFolder) : "Gallery"));
  els.pgMove.classList.toggle("armed", n > 0);
  els.pgDel.classList.toggle("armed", n > 0);
}

function toggleGalPick(rel, cell) {
  if (galSelected.has(rel)) {
    galSelected.delete(rel);
    cell.classList.remove("sel");
  } else {
    galSelected.add(rel);
    cell.classList.add("sel");
  }
  galTitleSync();
}

function setGalSelect(on) {
  galSelecting = on;
  galSelected.clear();
  els.pageGallery.classList.toggle("selecting", on);
  els.pgSelect.hidden = on || !images.length;
  els.pgNewFolder.hidden = on;
  els.pgTag.hidden = on;
  els.pgAll.hidden = !on;
  els.pgMove.hidden = !on;
  els.pgDel.hidden = !on;
  for (const c of els.grid.querySelectorAll(".gcell.sel")) {
    c.classList.remove("sel");
  }
  galTitleSync();
}

function galleryBack() {
  if (galSelecting) { setGalSelect(false); return; }
  if (galSearchQuery) { els.gSearch.value = ""; setGalSearch(""); return; }
  if (galFolder) { loadGallery(parentOf(galFolder)); return; }
  els.pageGallery.hidden = true;
}

function parentOf(rel) {
  const i = rel.lastIndexOf("/");
  return i < 0 ? "" : rel.slice(0, i);
}

/* ---- folder + image actions ---- */

async function galleryOp(body, okMsg) {
  try {
    const d = await api("/api/gallery/folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (okMsg) toast(okMsg);
    await reloadChat();               // chat cards point at moved files
    await loadGallery();
    return d;
  } catch (e) {
    if (e.message !== "locked") toast(e.message, true);
    throw e;
  }
}

async function newFolderIn(parent) {
  openFoldSheet("create", parent, "");
}

function openFoldSheet(mode, folder, currentName) {
  foldMode = mode;
  foldTarget = folder;
  if (mode === "tag") {
    els.foldTitle.textContent = "File by keyword";
    els.foldOk.textContent = "File them";
    els.foldSub.textContent =
      `Moves images here whose positive prompt mentions the keyword into ` +
      `a new folder named after it.${folder ? ` Scoped to ${baseName(folder)}.` : ""}`;
    els.foldName.placeholder = "e.g. chibi";
  } else {
    els.foldTitle.textContent = mode === "rename" ? "Rename folder" : "New folder";
    els.foldOk.textContent = mode === "rename" ? "Rename" : "Create";
    els.foldSub.textContent = mode === "rename"
      ? "Renaming also moves the images inside it."
      : (folder ? `Inside ${baseName(folder)}`
                : "Folders group your images in the gallery.");
    els.foldName.placeholder = "Folder name";
  }
  els.foldName.value = currentName || "";
  els.foldSheet.hidden = false;
  setTimeout(() => { els.foldName.focus(); els.foldName.select(); }, 60);
}

async function submitFoldSheet() {
  const name = els.foldName.value.trim();
  if (!name) { els.foldName.focus(); return; }
  els.foldOk.disabled = true;
  try {
    if (foldMode === "tag") {
      const d = await galleryOp({ action: "organize", folder: foldTarget,
                                  keyword: name }, null);
      els.foldSheet.hidden = true;
      if (d) {
        const n = d.moved || 0;
        if (n) {
          toast(`Filed ${n} image${n === 1 ? "" : "s"} into ${baseName(d.folder)}`);
        } else if (d.nometa && !d.checked) {
          toast("No image metadata to match", true);
        } else {
          toast(`Nothing here matches "${name}"`);
        }
      }
    } else if (foldMode === "rename") {
      const d = await galleryOp({ action: "rename", folder: foldTarget, name },
                                "Folder renamed");
      // keep the user where they were: re-root the open folder if it lived
      // inside the one that was just renamed
      const was = foldTarget;
      if (d && d.folder && was && (galFolder === was
          || galFolder.startsWith(was + "/"))) {
        await loadGallery(d.folder + galFolder.slice(was.length));
      }
    } else {
      const d = await galleryOp({ action: "create", parent: foldTarget, name });
      if (d && d.folder) await loadGallery(d.folder);
    }
    els.foldSheet.hidden = true;
  } catch { /* toast already shown; keep the sheet open to fix the name */ }
  finally { els.foldOk.disabled = false; }
}

function folderCtx(rel) {
  const n = galFolders.find((f) => joinFolder(galFolder, f.name) === rel);
  const count = n ? n.count : 0;
  openCtx([
    { label: "📂 Open", action: () => loadGallery(rel) },
    { label: "＋ New folder inside", action: () => newFolderIn(rel) },
    { label: "✎ Rename", action: () => openFoldSheet("rename", rel, baseName(rel)) },
    { label: "⇄ Move to…", action: () => openFolderPicker(rel, true) },
    { label: `🗑 Delete folder (${count} image${count === 1 ? "" : "s"})`,
      danger: true, action: () => deleteFolder(rel, count) },
  ], null);
}

async function deleteFolder(rel, count) {
  const extra = count
    ? ` It deletes the ${count} image${count === 1 ? "" : "s"} inside it.`
    : "";
  if (!confirm(`Delete the folder "${rel}"?${extra}`)) return;
  const parent = parentOf(rel);
  try {
    await galleryOp({ action: "delete", folder: rel }, "Folder deleted");
    await loadGallery(galFolder === rel ? parent : galFolder);
  } catch { /* toast shown */ }
}

function imageCtx(rel) {
  openCtx([
    { label: "🔍 Open", action: () => openLightbox(rel) },
    { label: "📁 Move to…", action: () => openFolderPicker([rel], false) },
    { label: "⤓ Download", action: () => downloadImage(rel) },
    { label: "🗑 Delete", danger: true, action: () => deleteImages([rel]) },
  ], "/thumb/" + rel);
}

function downloadImage(rel) {
  const a = document.createElement("a");
  a.href = "/outputs/" + rel;
  a.download = baseName(rel);
  a.click();
}

async function deleteImages(rels) {
  const n = rels.length;
  if (!n) return;
  if (!confirm(`Delete ${n} image${n === 1 ? "" : "s"}? This cannot be undone.`)) {
    return;
  }
  if (galSelecting) setGalSelect(false);
  try {
    const d = await api("/api/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ names: rels }),
    });
    if (d.partial) toast(`Deleted ${d.removed} of ${n}`, true);
    else toast(`Deleted ${d.removed} image${d.removed === 1 ? "" : "s"}`);
    closeLightbox();
    await reloadChat();
    await loadGallery();
  } catch (e) {
    if (e.message !== "locked") toast("Delete failed: " + e.message, true);
  }
}

/* ---- move-target picker ---- */

let pickMode = "images";     // "images" | "folder"
let pickRels = [];           // images to move
let pickFolder = "";         // folder being re-parented

function openFolderPicker(rels, isFolder) {
  pickMode = isFolder ? "folder" : "images";
  pickRels = Array.isArray(rels) ? rels.slice() : [rels];
  pickFolder = isFolder ? rels : "";
  els.fpTitle.textContent = isFolder
    ? "Move folder"
    : (pickRels.length === 1
      ? "Move image" : `Move ${pickRels.length} images`);
  renderPickList();
  els.foldPick.hidden = false;
}

function renderPickList() {
  const list = els.fpList;
  list.textContent = "";
  const self = pickMode === "folder" ? pickFolder : galFolder;

  const row = (rel, name, depth, opts) => {
    const b = el("button", "fp-row" + (opts.here ? " here" : ""));
    b.style.paddingLeft = 8 + depth * 16 + "px";
    b.innerHTML =
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';
    b.appendChild(el("span", "fp-name", name));
    if (opts.tag) b.appendChild(el("span", "fp-lock", opts.tag));
    b.addEventListener("click", () => confirmMove(rel, opts));
    list.appendChild(b);
  };

  // root first, then every folder in tree order
  row("", "Gallery (unfiled)", 0, { here: self === "", tag: pickMode === "images" ? "here" : "" });
  for (const f of galTree) {
    if (pickMode === "folder" && (f.rel === pickFolder
        || f.rel.startsWith(pickFolder + "/"))) continue;   // no self/child
    row(f.rel, f.name, f.depth + 1,
        { here: f.rel === self, tag: pickMode === "images" && f.rel === self ? "here" : "" });
  }
  list.appendChild(el("div", "fp-sep", "or"));
  const nb = el("button", "fp-row");
  nb.innerHTML =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M12 5v14M5 12h14"/></svg>';
  nb.appendChild(el("span", "fp-name", "New folder…"));
  nb.addEventListener("click", () => {
    els.foldPick.hidden = true;
    openFoldSheet("create", galFolder, "");
  });
  list.appendChild(nb);
}

async function confirmMove(dest, opts) {
  els.foldPick.hidden = true;
  if (opts.here) { toast("Already there"); return; }
  try {
    if (pickMode === "folder") {
      const d = await galleryOp(
        { action: "move", folder: pickFolder, parent: dest }, "Folder moved");
      if (d && d.folder) loadGallery(d.folder);
    } else {
      await galleryOp({ action: "move_images", rels: pickRels, folder: dest },
                     `Moved ${pickRels.length} image${pickRels.length === 1 ? "" : "s"}`);
    }
    if (galSelecting) setGalSelect(false);
  } catch { /* toast shown */ }
}

/* the chat view holds /outputs/ URLs — re-read it after files move */
async function reloadChat() {
  try {
    const h = await api("/api/history");
    renderHistory(h.timeline || []);
    images = timelineImages(h.timeline || []);
  } catch { /* the gallery still works without it */ }
}

/* -------------------------------------------------------------- lightbox
 *
 * lbList is the set of images the viewer pages through — a snapshot taken
 * when it opens. That keeps navigation scoped to the folder the user is
 * browsing, and keeps a chat card (which may reference an image outside any
 * folder view) from mutating the gallery list.
 */

let lbIndex = -1;
let lbList = [];
let lbToken = 0;                 // guards the async metadata fetch below

function openLightbox(rel, list) {
  const src = list || images;
  lbList = src.slice();
  lbIndex = lbList.findIndex((x) => x.rel === rel);
  if (lbIndex < 0) {
    lbList.unshift({ rel, name: baseName(rel), seed: null });
    lbIndex = 0;
  }
  toggleLbInfo(false);
  showLightbox();
  els.lb.hidden = false;
}

function closeLightbox() { els.lb.hidden = true; }

function showLightbox() {
  const im = lbList[lbIndex];
  if (!im) return;
  const rel = im.rel || im.name;
  els.lbName.textContent = baseName(rel);
  els.lbImg.src = "/outputs/" + rel;
  els.lbPrev.style.visibility = lbIndex > 0 ? "visible" : "hidden";
  els.lbNext.style.visibility = lbIndex < lbList.length - 1 ? "visible" : "hidden";

  els.lbMeta.textContent = "";
  els.lbPrompt.textContent = "";
  els.lbNeg.textContent = "";
  els.lbParams.textContent = "";
  [els.lbPromptWrap, els.lbNegWrap, els.lbParamsWrap].forEach((w) => { w.hidden = true; });

  const token = ++lbToken;      // paging fast must not stack up chips
  api("/api/image_info?name=" + encodeURIComponent(rel)).then((info) => {
    if (token !== lbToken || els.lb.hidden) return;
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
  if (ni >= 0 && ni < lbList.length) { lbIndex = ni; showLightbox(); }
}

// the (i) button slides the info popup over the image
function toggleLbInfo(open) {
  const show = open === undefined ? !els.lbSheet.classList.contains("open") : !!open;
  els.lbSheet.classList.toggle("open", show);
  els.lbInfo.classList.toggle("on", show);
}

/* settings — full page with Chatterbox-style sub-tabs */

function setPsTab(name) {
  for (const b of document.querySelectorAll("#ps-tabs button")) {
    b.classList.toggle("on", b.dataset.tab === name);
  }
  for (const s of document.querySelectorAll("#page-settings section[data-pane]")) {
    s.hidden = s.dataset.pane !== name;
  }
  document.querySelector(".ps-body").scrollTop = 0;
}

async function openSettings() {
  els.pageSettings.hidden = false;
  setPsTab("persona");
  loadPersonas();
  try {
    // refresh=1 → server asks SD to rescan its models dir, fresh list
    const s = await api("/api/status?refresh=1");
    sdCaps = s.capabilities || {};
    els.shSdok.textContent = s.sd_ok ? "· connected" : "· unreachable";
    els.shCur.textContent = s.current_model ? `· now: ${s.current_model}` : "";
    els.shArch.textContent = s.current_arch ? `· loaded: ${s.current_arch}` : "";
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
      els.shUsername.value = cfg.username || "";
      savedComps = cfg.sd_components || {};
      renderComps(cfg.current_arch || s.current_arch || "");
    } catch (e) {
      // settings are optional, but a failure here (a broken render, say) must
      // not vanish — it would leave the panel silently empty
      if (e && e.message !== "locked") console.warn("settings:", e);
    }
  } catch (e) {
    if (e.message !== "locked") toast("Status failed: " + e.message, true);
  }
}

/* ------------------------------------------- model components (Forge Neo) */

/** What the connected Forge Neo offers: samplers, schedule types, companion
 *  files, precision options and the per-architecture profiles. */
let sdCaps = {};
/** Per-architecture companion config, mirrors data/config.json sd_components */
let savedComps = {};
let compArch = "";

/** Architectures worth showing: every slot the server knows that either has a
 *  model, already has components saved, or is a modular family (the rest are
 *  legacy SD1.5/SDXL and need no companions). */
function compArchList() {
  const rows = sdCaps.architectures || [];
  const legacy = new Set(["sd", "xl"]);
  const out = [];
  for (const r of rows) {
    if (!legacy.has(r.arch) || r.checkpoint || savedComps[r.arch]) {
      out.push(r);
    }
  }
  if (!out.length) return [];
  const cur = out.findIndex((r) => r.arch === compArch);
  return out;
}

function renderComps(activeArch) {
  const box = els.shComps;
  box.textContent = "";
  const rows = compArchList();
  els.shCompActions.hidden = rows.length === 0;
  if (!rows.length) {
    box.appendChild(el("div", "hint",
      "No modular architectures reported by the server — this looks like a " +
      "legacy SD1.5/SDXL-only install."));
    return;
  }
  if (activeArch) compArch = activeArch;
  if (!rows.some((r) => r.arch === compArch)) compArch = rows[0].arch;
  const r = rows.find((x) => x.arch === compArch);
  const saved = savedComps[r.arch] || {};

  const card = el("div", "comp-card on");

  const sel = el("select", "comp-arch");
  for (const row of rows) {
    const o = el("option", null, row.label || row.arch);
    o.value = row.arch;
    if (row.arch === r.arch) o.selected = true;
    sel.appendChild(o);
  }
  sel.addEventListener("change", () => renderComps(sel.value));
  card.appendChild(sel);

  const head = el("div", "comp-head");
  const bits = [`CFG ${r.cfg}`, `${r.steps} steps`, `${r.sampler}/${r.scheduler}`];
  if (r.distilled) bits.unshift("low-guidance");
  if (r.video) bits.push("video");
  head.appendChild(el("span", "dim", bits.join(" · ")));
  card.appendChild(head);

  const fields = [
    ["Text encoder", "dl-te", "text_encoder", "server default",
     saved.text_encoder || ""],
    ["VAE", "dl-vae", "sd_vae", "Automatic", saved.sd_vae || ""],
    ["Precision", "dl-lb", "low_bits", "Automatic",
     saved.low_bits || r.low_bits || ""],
  ];
  for (const [label, list, field, ph, val] of fields) {
    const wrap = el("label", null, label);
    const inp = el("input");
    inp.type = "text";
    inp.setAttribute("list", list);   // .list is read-only on the element
    inp.placeholder = ph;
    inp.value = val;
    inp.dataset.arch = r.arch;
    inp.dataset.field = field;
    wrap.appendChild(inp);
    card.appendChild(wrap);
  }

  const cur = [];
  if ((r.modules || []).length) cur.push("attached: " + r.modules.join(", "));
  if (r.checkpoint) cur.push("checkpoint: " + r.checkpoint);
  if (cur.length) card.appendChild(el("div", "hint", cur.join(" · ")));
  box.appendChild(card);

  fillList("dl-te", sdCaps.text_encoders, true);
  fillList("dl-vae", ["Automatic"].concat((sdCaps.vaes || [])
    .filter((v) => v !== "Automatic")), true);
  fillList("dl-lb", sdCaps.low_bits, false);
}

function fillList(id, values, placeholder) {
  const dl = $(id);
  if (!dl) return;
  dl.textContent = "";
  for (const v of values || []) {
    if (!v) continue;
    dl.appendChild(el("option", null, v));
  }
  if (placeholder) {
    dl.appendChild(el("option", null, "— clear —"));
  }
}

/** Read the component form into {arch: {field: value}}, blank = cleared. */
function collectComps() {
  const out = {};
  for (const inp of els.shComps.querySelectorAll("input[data-field]")) {
    const arch = inp.dataset.arch, field = inp.dataset.field;
    const val = inp.value.trim();
    out[arch] = out[arch] || {};
    if (val === "— clear —" || val === "") delete out[arch][field];
    else out[arch][field] = val;
    if (!Object.keys(out[arch]).length) delete out[arch];
  }
  return out;
}

async function applyComps(arch, values) {
  const d = await api("/api/model/components", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ arch, ...values }),
  });
  toast("Components applied");
  return d;
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
    // the saved per-architecture components travel with the switch, so a
    // modular model never comes up without its text encoder / VAE
    const arch = archOfModel(title);
    const comps = savedComps[arch] || {};
    const d = await api("/api/model", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: title, arch, ...comps }),
    });
    els.shCur.textContent = `· now: ${d.current_model || title}`;
    els.shArch.textContent = d.arch ? `· loaded: ${d.arch}` : "";
    if ((d.modules || []).length) {
      toast("Loaded with " + d.modules.join(", "));
    } else {
      toast("Checkpoint loaded");
    }
    compArch = d.arch || arch;
    renderComps(compArch);
  } catch (e) {
    if (e.message !== "locked") toast("Load failed: " + e.message, true);
  } finally {
    els.shLoad.disabled = false;
    els.shLoad.textContent = "Load";
    refreshStatus();
  }
}

/** Architecture of a checkpoint title, from the status model_detail list. */
function archOfModel(title) {
  const hit = (sdModelDetail || []).find((m) => m.title === title);
  return (hit && hit.arch) || "";
}

/* ------------------------------------------------------------- characters */

let statusModels = [];        // checkpoint titles cache for the char form
let sdModelDetail = [];       // [{title, arch}] — which family each model is
let editCharId = null;        // null = creating new
let pendingAvatar = "";       // dataURL while editing
let pendingCover = "";        // character's first image, dataURL while editing
let pendingScCover = "";      // scenario's first image, dataURL while editing
let clearAvatar = false, clearCover = false, clearScCover = false;

async function refreshChars() {
  try {
    const [d, s] = await Promise.all([
      api("/api/characters"),
      api("/api/status"),
    ]);
    chars = d.characters || [];
    activeCharId = d.active || "";
    statusModels = s.models || [];
    sdModelDetail = s.model_detail || [];
    sdCaps = s.capabilities || sdCaps;
  } catch (e) {
    if (e.message !== "locked") toast("Characters failed: " + e.message, true);
  }
  renderCharList();
}

function charRows(filter) {
  const q = (filter || "").trim().toLowerCase();
  const rows = [];
  if (!q || "no character".includes(q) || "free chat".includes(q)) {
    rows.push({ plain: true });
  }
  for (const c of chars) {
    if (q && !(c.name || "").toLowerCase().includes(q)) continue;
    rows.push(c);
  }
  return rows;
}

/* list/grid toggle (Chatterbox) — persists in localStorage */
let charGrid = localStorage.getItem("charview") === "grid";

function updateGridBtn() {
  document.querySelector("#btn-gridtoggle .ic-grid").hidden = charGrid;
  document.querySelector("#btn-gridtoggle .ic-list").hidden = !charGrid;
}

function renderCharList() {
  const rows = charRows(els.charSearch.value);
  els.charlist.classList.toggle("asgrid", charGrid);
  els.charlist.textContent = "";
  els.charempty.hidden = rows.some((r) => !r.plain);
  for (const c of rows) {
    const row = el("button", "char-row" + (c.plain
      ? (activeCharId ? "" : " active")
      : (c.id === activeCharId ? " active" : "")));
    if (c.plain) {
      row.appendChild(el("span", "char-avatar ch-noavatar", "✦"));
      const tx = el("div", "char-text");
      tx.appendChild(el("b", null, "No character"));
      tx.appendChild(el("span", "dim", "plain assistant chat"));
      row.appendChild(tx);
      row.addEventListener("click", () => selectChar(""));
      els.charlist.appendChild(row);
      continue;
    }
    if (c.avatar) {
      const img = document.createElement("img");
      img.className = "char-avatar";
      img.src = c.avatar;
      img.alt = "";
      img.loading = "lazy";
      row.appendChild(img);
    } else {
      row.appendChild(el("span", "char-avatar ch-noavatar",
        (c.name || "?").slice(0, 1).toUpperCase()));
    }
    const tx = el("div", "char-text");
    tx.appendChild(el("b", null, c.name || c.id));
    if (c.chats > 0) {
      tx.appendChild(el("span", "dim",
        c.chats + (c.chats === 1 ? " chat" : " chats")));
    }
    if (c.id === activeCharId) tx.appendChild(el("span", "char-now", "now"));
    row.appendChild(tx);
    row.addEventListener("click", () => selectChar(c.id));
    row.dataset.charid = c.id;
    els.charlist.appendChild(row);
  }
}

function fillModelSelect() {
  const sel = els.chModel;
  sel.textContent = "";
  sel.appendChild(el("option", null, "(current checkpoint)"));
  for (const m of statusModels) {
    const o = el("option", null, m);
    o.value = m;
    sel.appendChild(o);
  }
}

function selectChar(id) {
  if (id === activeCharId) { switchView("chat"); return; }
  api("/api/character/select", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  }).then((d) => {
    activeCharId = id;
    els.msgs.textContent = "";
    renderHistory(d.timeline || []);
    applyAutoFlag(d);
    switchView("chat");
    refreshStatus();
  }).catch((e) => {
    if (e.message !== "locked") toast("Switch failed: " + e.message, true);
  });
}

function setPreview(img, clearBtn, src) {
  if (src) { img.src = src; img.hidden = false; }
  else { img.removeAttribute("src"); img.hidden = true; }
  if (clearBtn) clearBtn.hidden = !src;
}

function openCharForm(card) {
  editCharId = card ? card.id : null;
  pendingAvatar = "";
  pendingCover = "";
  clearAvatar = clearCover = clearScCover = false;
  scenarioCharId = card ? card.id : null;
  els.chTitle.textContent = card ? "Edit character" : "New character";
  els.chName.value = card ? card.name : "";
  els.chAppearance.value = card ? (card.appearance || "") : "";
  els.chPersona.value = card ? (card.persona || "") : "";
  els.chGreeting.value = card ? (card.greeting || "") : "";
  els.chSize.value = card && card.size && card.size.length === 2
    ? card.size[0] + "x" + card.size[1] : "";
  els.chTemp.value = card && card.temp != null ? card.temp : "";
  els.chMaxtok.value = card && card.max_tokens != null ? card.max_tokens : "";
  fillModelSelect();
  els.chModel.value = card ? (card.checkpoint || "") : "";
  if (els.chModel.value === "" && card && card.checkpoint) {
    // not in list — keep it as an option
    const o = el("option", null, card.checkpoint);
    o.value = card.checkpoint;
    els.chModel.appendChild(o);
    els.chModel.value = card.checkpoint;
  }
  if (card && card.avatar) {
    els.chAvatarImg.src = card.avatar;
    els.chAvatarImg.hidden = false;
  } else {
    els.chAvatarImg.hidden = true;
  }
  els.chAvatarClear.hidden = !(card && card.avatar);
  setPreview(els.chCoverImg, els.chCoverClear,
    (card && card.cover) || "");
  els.chDelete.hidden = !card;
  renderScenarioList();
  els.pageChform.hidden = false;
  setTimeout(() => els.chName.focus(), 60);
}

async function saveCharForm() {
  const name = els.chName.value.trim();
  if (!name) { toast("Name required", true); return; }
  const size = els.chSize.value;
  const temp = parseFloat(els.chTemp.value);
  const mtok = parseInt(els.chMaxtok.value, 10);
  const payload = {
    id: editCharId || "",
    name,
    appearance: els.chAppearance.value.trim(),
    persona: els.chPersona.value.trim(),
    greeting: els.chGreeting.value.trim(),
    checkpoint: els.chModel.value,
    size: size ? size.split("x").map(Number) : [],
  };
  if (!isNaN(temp)) payload.temp = Math.min(2, Math.max(0.1, temp));
  if (!isNaN(mtok)) payload.max_tokens = Math.min(8192, Math.max(16, mtok));
  if (pendingAvatar) payload.avatar = pendingAvatar;
  if (clearAvatar) payload.avatar_remove = true;
  if (pendingCover) payload.cover = pendingCover;
  if (clearCover) payload.cover_remove = true;
  const btn = els.cfSave;
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Saving…";
  try {
    const d = await api("/api/characters", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    toast(editCharId ? "Character saved" : "Character created");
    if (!editCharId && !activeCharId) {
      // first character may have adopted the current chat
      activeCharId = d.id;
    }
    pendingAvatar = "";
    pendingCover = "";
    clearAvatar = clearCover = false;
    els.pageChform.hidden = true;
    await refreshChars();
    refreshStatus();
  } catch (e) {
    if (e.message !== "locked") toast("Save failed: " + e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}

async function deleteCharById(id, name) {
  if (!id) return;
  if (!confirm("Delete " + (name || "this character") +
      " and their chat? Images stay in the gallery.")) return;
  try {
    await api("/api/character/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    toast("Character deleted");
    if (activeCharId === id) {
      const h = await api("/api/history");
      els.msgs.textContent = "";
      renderHistory(h.timeline || []);
    }
    await refreshChars();
    refreshStatus();
  } catch (e) {
    if (e.message !== "locked") toast("Delete failed: " + e.message, true);
  }
}

function pickAvatar(file) {
  if (!file || !file.type.startsWith("image/")) return;
  const reader = new FileReader();
  reader.onload = () => {
    const img = new Image();
    img.onload = () => {
      const S = 256;
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = S;
      const ctx = canvas.getContext("2d");
      const side = Math.min(img.width, img.height);
      ctx.drawImage(img, (img.width - side) / 2, (img.height - side) / 2,
        side, side, 0, 0, S, S);
      pendingAvatar = canvas.toDataURL("image/jpeg", 0.85);
      clearAvatar = false;
      els.chAvatarImg.src = pendingAvatar;
      els.chAvatarImg.hidden = false;
      els.chAvatarClear.hidden = false;
    };
    img.src = reader.result;
  };
  reader.readAsDataURL(file);
}

/* cover (first image) picker — keep native resolution when it fits, else
   scale the long side to 1216px so the save payload stays reasonable */
function pickCover(file, which) {
  if (!file || !file.type.startsWith("image/")) return;
  const reader = new FileReader();
  reader.onload = () => {
    const apply = (dataUrl) => {
      if (which === "sc") {
        pendingScCover = dataUrl; clearScCover = false;
        setPreview(els.scsCoverImg, els.scsCoverClear, pendingScCover);
      } else {
        pendingCover = dataUrl; clearCover = false;
        setPreview(els.chCoverImg, els.chCoverClear, pendingCover);
      }
    };
    const img = new Image();
    img.onload = () => {
      const long = Math.max(img.width, img.height);
      if (long <= 1216) { apply(reader.result); return; }
      const k = 1216 / long;
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(img.width * k);
      canvas.height = Math.round(img.height * k);
      canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
      apply(canvas.toDataURL("image/jpeg", 0.9));
    };
    img.onerror = () => apply(reader.result);
    img.src = reader.result;
  };
  reader.readAsDataURL(file);
}

/* ------------------------------- AI generate sheet (character form) ----- */

let aiMode = null;

const AI_MODES = {
  character: {
    title: "Generate character",
    hint: "Describe the character you want — the AI drafts the name, look "
          + "tags, personality and greeting.",
    placeholder: "e.g. a dry-humored knight who secretly paints flowers",
    prefill: () => "",
  },
  avatar: {
    title: "Generate avatar",
    hint: "Describe the portrait — the character's look tags are added "
          + "automatically. The result is square.",
    placeholder: "e.g. portrait, soft candlelight, slight smile",
    prefill: () => els.chAppearance.value.trim(),
  },
  "char-cover": {
    title: "Generate first image",
    hint: "Describe the opening scene — this image opens every new chat "
          + "with this character.",
    placeholder: "e.g. leaning on a rainy window at night, glancing back",
    prefill: () => els.chAppearance.value.trim(),
  },
  "scenario-cover": {
    title: "Generate first image",
    hint: "Describe the scene — this image shows when a chat with this "
          + "scenario starts.",
    placeholder: "e.g. a rooftop in heavy rain, city lights below",
    prefill: () => els.scsDesc.value.trim().slice(0, 600),
  },
};

function openAiSheet(mode) {
  const m = AI_MODES[mode];
  if (!m) return;
  aiMode = mode;
  els.aiTitle.textContent = m.title;
  els.aiHint.textContent = m.hint;
  els.aiText.value = m.prefill();
  els.aiText.placeholder = m.placeholder;
  els.aiGo.disabled = false;
  els.aiGo.textContent = "Generate";
  els.aiSheet.hidden = false;
  setTimeout(() => els.aiText.focus(), 60);
}

async function runAiGenerate() {
  const mode = aiMode;
  if (!mode) return;
  const text = els.aiText.value.trim();
  if (!text) { toast("Describe it first", true); return; }
  const btn = els.aiGo;
  btn.disabled = true;
  btn.textContent = "Generating…";
  try {
    if (mode === "character") {
      const d = await api("/api/char/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: text }),
      });
      els.chName.value = d.name || els.chName.value;
      els.chAppearance.value = d.appearance || "";
      els.chPersona.value = d.persona || "";
      els.chGreeting.value = d.greeting || "";
      els.aiSheet.hidden = true;
      toast("Character drafted — review and save");
      return;
    }
    const payload = {
      purpose: mode === "avatar" ? "avatar"
        : mode === "char-cover" ? "char_cover" : "scenario_cover",
      prompt: text,
      char_id: editCharId || "",
      appearance: els.chAppearance.value.trim(),
      size: els.chSize.value,
    };
    if (mode === "scenario-cover") payload.scenario_id = editScenarioId || "";
    const d = await api("/api/image/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (mode === "avatar") {
      pendingAvatar = d.avatar || "";
      setPreview(els.chAvatarImg, els.chAvatarClear, pendingAvatar);
    } else if (mode === "char-cover") {
      pendingCover = d.cover || "";
      setPreview(els.chCoverImg, els.chCoverClear, pendingCover);
    } else {
      pendingScCover = d.cover || "";
      setPreview(els.scsCoverImg, els.scsCoverClear, pendingScCover);
    }
    els.aiSheet.hidden = true;
    toast((pendingAvatar || pendingCover || pendingScCover)
      ? "Generated — save to keep it" : "Image is in the gallery");
  } catch (e) {
    if (e.message !== "locked") toast("Generate failed: " + e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "Generate";
  }
}

els.aiGo.addEventListener("click", runAiGenerate);
els.aiCancel.addEventListener("click", () => {
  els.aiSheet.hidden = true;
});

function charCtx(row) {
  const id = row.dataset.charid;
  const c = chars.find((x) => x.id === id);
  if (!c) return;
  openCtx([
    { label: "✎ Edit", action: () => openCharForm(c) },
    { label: "🎬 Start scenario", action: () => startScenarioMenu(c) },
    { label: "🗑 Delete", danger: true,
      action: () => deleteCharById(id, c.name) },
  ], c.avatar || null);
}

els.cfBack.addEventListener("click", () => { els.pageChform.hidden = true; });
$("btn-addchar").addEventListener("click", () => openCharForm(null));
$("btn-overflow").addEventListener("click", () => {
  openCtx([{ label: "⚙ Settings", action: openSettings }], null);
});
$("btn-gallery").addEventListener("click", openGallery);
$("pg-back").addEventListener("click", galleryBack);
$("pg-refresh").addEventListener("click", () => loadGallery());
els.pgNewFolder.addEventListener("click", () => newFolderIn(galFolder));
els.pgTag.addEventListener("click", () => openFoldSheet("tag", galFolder, ""));
els.gSearch.addEventListener("input", onGalSearchInput);
els.gSearch.addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); clearTimeout(galSearchTimer); setGalSearch(els.gSearch.value); }
  if (e.key === "Escape") {
    e.preventDefault();
    els.gSearch.value = "";
    setGalSearch("");
  }
});
els.pgSelect.addEventListener("click", () => setGalSelect(true));
els.pgAll.addEventListener("click", () => {
  const cells = [...els.grid.querySelectorAll(".gcell")];
  const all = cells.length && cells.every((c) => galSelected.has(c.dataset.rel));
  for (const c of cells) {
    if (all) { galSelected.delete(c.dataset.rel); c.classList.remove("sel"); }
    else { galSelected.add(c.dataset.rel); c.classList.add("sel"); }
  }
  galTitleSync();
});
els.pgMove.addEventListener("click", () => {
  if (!galSelected.size) { toast("Nothing selected", true); return; }
  openFolderPicker([...galSelected], false);
});
els.pgDel.addEventListener("click", () => {
  if (!galSelected.size) { toast("Nothing selected", true); return; }
  deleteImages([...galSelected]);
});

/* gallery folder name sheet */
let foldMode = "create", foldTarget = "";
els.foldCancel.addEventListener("click", () => { els.foldSheet.hidden = true; });
els.foldSheet.addEventListener("click", (e) => {
  if (e.target === els.foldSheet) els.foldSheet.hidden = true;
});
els.foldOk.addEventListener("click", submitFoldSheet);
els.foldName.addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); submitFoldSheet(); }
});
els.fpCancel.addEventListener("click", () => { els.foldPick.hidden = true; });
els.foldPick.addEventListener("click", (e) => {
  if (e.target === els.foldPick) els.foldPick.hidden = true;
});

/* long-press / right-click inside the gallery */
els.gfolders.addEventListener("contextmenu", (e) => {
  const t = e.target.closest(".gtile");
  if (!t || galSelecting) return;
  e.preventDefault();
  folderCtx(t.dataset.folder);
});
let gfLpTimer = null, gfLpStart = null;
els.gfolders.addEventListener("touchstart", (e) => {
  const t = e.target.closest(".gtile");
  if (!t || galSelecting) return;
  gfLpStart = { x: e.touches[0].clientX, y: e.touches[0].clientY, rel: t.dataset.folder };
  gfLpTimer = setTimeout(() => {
    gfLpTimer = null;
    if (navigator.vibrate) navigator.vibrate(25);
    folderCtx(gfLpStart.rel);
    gfLpStart = null;
  }, 550);
}, { passive: true });
els.gfolders.addEventListener("touchmove", (e) => {
  if (!gfLpTimer || !gfLpStart) return;
  const dx = e.touches[0].clientX - gfLpStart.x;
  const dy = e.touches[0].clientY - gfLpStart.y;
  if (dx * dx + dy * dy > 144) { clearTimeout(gfLpTimer); gfLpTimer = null; }
}, { passive: true });
els.gfolders.addEventListener("touchend", () => {
  if (gfLpTimer) { clearTimeout(gfLpTimer); gfLpTimer = null; }
}, { passive: true });

els.grid.addEventListener("contextmenu", (e) => {
  const t = e.target.closest(".gcell");
  if (!t || galSelecting) return;
  e.preventDefault();
  imageCtx(t.dataset.rel);
});
let ggLpTimer = null, ggLpStart = null;
els.grid.addEventListener("touchstart", (e) => {
  const t = e.target.closest(".gcell");
  if (!t || galSelecting) return;
  ggLpStart = { x: e.touches[0].clientX, y: e.touches[0].clientY, rel: t.dataset.rel };
  ggLpTimer = setTimeout(() => {
    ggLpTimer = null;
    if (navigator.vibrate) navigator.vibrate(25);
    imageCtx(ggLpStart.rel);
    ggLpStart = null;
  }, 550);
}, { passive: true });
els.grid.addEventListener("touchmove", (e) => {
  if (!ggLpTimer || !ggLpStart) return;
  const dx = e.touches[0].clientX - ggLpStart.x;
  const dy = e.touches[0].clientY - ggLpStart.y;
  if (dx * dx + dy * dy > 144) { clearTimeout(ggLpTimer); ggLpTimer = null; }
}, { passive: true });
els.grid.addEventListener("touchend", () => {
  if (ggLpTimer) { clearTimeout(ggLpTimer); ggLpTimer = null; }
}, { passive: true });
$("pg-refresh").addEventListener("click", loadGallery);
$("btn-gridtoggle").addEventListener("click", () => {
  charGrid = !charGrid;
  localStorage.setItem("charview", charGrid ? "grid" : "rows");
  updateGridBtn();
  renderCharList();
});
updateGridBtn();
$("btn-back").addEventListener("click", () => switchView("chars"));
$("btn-newchat").addEventListener("click", newChat);
els.btnAutoimg.addEventListener("click", toggleAutoImages);

/* --------------------------------------------------- auto image toggle
 *
 * Per chat, persisted server-side. Off means the image tools are withheld
 * from the model entirely, so the chat is plain text — regenerating an
 * existing image from the UI still works. The button reflects
 * aria-pressed; the server owns the value.
 */

function paintAutoImages(on) {
  const b = els.btnAutoimg;
  b.setAttribute("aria-pressed", on ? "true" : "false");
  b.title = on ? "Auto images on — tap for text only" : "Auto images off";
}

/* chat-mutating endpoints (new / select / delete / character switch) carry the
   active chat's flag, so the button follows the chat without a second fetch */
function applyAutoFlag(d) {
  if (d && typeof d.auto_images === "boolean") paintAutoImages(d.auto_images);
}

async function toggleAutoImages() {
  const want = els.btnAutoimg.getAttribute("aria-pressed") !== "true";
  const prev = els.btnAutoimg.getAttribute("aria-pressed");
  paintAutoImages(want);                        // optimistic
  try {
    const d = await api("/api/chat/auto_images", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ on: want }),
    });
    paintAutoImages(d.auto_images !== false);
    toast(d.auto_images !== false
      ? "Auto images on" : "Auto images off — text only");
  } catch (e) {
    paintAutoImages(prev === "true");           // server refused: revert
    if (e.message !== "locked") toast("Failed: " + e.message, true);
  }
}

els.cfSave.addEventListener("click", saveCharForm);
els.chDelete.addEventListener("click", () =>
  deleteCharById(editCharId, els.chName.value.trim()));
els.chAvatarBtn.addEventListener("click", () => els.chAvatarFile.click());
els.chAvatarFile.addEventListener("change", () => {
  if (els.chAvatarFile.files[0]) pickAvatar(els.chAvatarFile.files[0]);
  els.chAvatarFile.value = "";
});
els.chAvatarAi.addEventListener("click", () => openAiSheet("avatar"));
els.chAvatarClear.addEventListener("click", () => {
  pendingAvatar = "";
  clearAvatar = true;
  setPreview(els.chAvatarImg, els.chAvatarClear, "");
});
els.chCoverBtn.addEventListener("click", () => els.chCoverFile.click());
els.chCoverFile.addEventListener("change", () => {
  if (els.chCoverFile.files[0]) pickCover(els.chCoverFile.files[0], "ch");
  els.chCoverFile.value = "";
});
els.chCoverAi.addEventListener("click", () => openAiSheet("char-cover"));
els.chCoverClear.addEventListener("click", () => {
  pendingCover = "";
  clearCover = true;
  setPreview(els.chCoverImg, els.chCoverClear, "");
});
els.chAiFab.addEventListener("click", () => openAiSheet("character"));
els.charSearch.addEventListener("input", renderCharList);

/* long-press / right-click on character cards */
els.charlist.addEventListener("contextmenu", (e) => {
  const target = e.target.closest(".char-row");
  if (!target || !target.dataset.charid) return;
  e.preventDefault();
  charCtx(target);
});
let chLpTimer = null, chLpStart = null;
els.charlist.addEventListener("touchstart", (e) => {
  const target = e.target.closest(".char-row");
  if (!target || !target.dataset.charid) return;
  chLpStart = { x: e.touches[0].clientX, y: e.touches[0].clientY, target };
  chLpTimer = setTimeout(() => {
    chLpTimer = null;
    if (navigator.vibrate) navigator.vibrate(25);
    charCtx(chLpStart.target);
    chLpStart = null;
  }, 550);
}, { passive: true });
els.charlist.addEventListener("touchmove", (e) => {
  if (!chLpTimer || !chLpStart) return;
  const dx = e.touches[0].clientX - chLpStart.x;
  const dy = e.touches[0].clientY - chLpStart.y;
  if (dx * dx + dy * dy > 144) { clearTimeout(chLpTimer); chLpTimer = null; }
}, { passive: true });
els.charlist.addEventListener("touchend", () => {
  if (chLpTimer) { clearTimeout(chLpTimer); chLpTimer = null; }
}, { passive: true });

/* ---------------------------------------------------------------- status */

async function refreshStatus() {
  try {
    const s = await api("/api/status");
    activeCharId = s.character ? s.character.id : "";
    els.chatCharname.textContent = s.character ? s.character.name : "Free chat";
    els.btnHistory.hidden = !s.character;
    // per-chat setting — pick it up on chat switch, new chat and restart
    if (typeof s.auto_images === "boolean") paintAutoImages(s.auto_images);
    return s;
  } catch (e) {
    if (e.message === "locked") return;
    return null;
  }
}

/* ----------------------------------------------------------------- views */

function switchView(v) {
  els.viewChars.hidden = v !== "chars";
  els.viewChat.hidden = v !== "chat";
  els.composer.hidden = v !== "chat";
  if (v === "chars") refreshChars();
}

/* gallery — full-screen page opened from the Characters toolbar */
function openGallery() {
  setGalSelect(false);
  els.pageGallery.hidden = false;
  loadGallery();
}

/* ----------------------------------------------------------------- images */

// native-resolution images everywhere; generation details live in the
// lightbox info popup. `rel` may include folder segments.
function imgSrcFor(rel) {
  return "/outputs/" + rel;
}

/* accent color — custom UI theme (from the old Chatterbox appearance wheel) */

function hexToHsl(hex) {
  const n = parseInt(hex.slice(1), 16);
  const r = (n >> 16 & 255) / 255, g = (n >> 8 & 255) / 255, b = (n & 255) / 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  let h = 0, s = 0;
  const l = (max + min) / 2;
  if (max !== min) {
    const d = max - min;
    s = l > .5 ? d / (2 - max - min) : d / (max + min);
    if (max === r) h = (g - b) / d + (g < b ? 6 : 0);
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
  }
  return [h * 60, s * 100, l * 100];
}

function applyAccent(hex) {
  if (!/^#[0-9a-fA-F]{6}$/.test(hex)) return;
  const [h, s, l] = hexToHsl(hex);
  // partner color: hue +40°, softer saturation — keeps gradients tasteful
  const h2 = (h + 40) % 360;
  const s2 = Math.min(85, Math.max(45, s * 0.9 + 15));
  const l2 = Math.min(80, l + 8);
  const root = document.documentElement.style;
  root.setProperty("--accent", hex);
  root.setProperty("--accent2", `hsl(${h2.toFixed(0)}, ${s2.toFixed(0)}%, ${l2.toFixed(0)}%)`);
  localStorage.setItem("accent", hex);
  document.getElementById("accent-pick").value = hex;
  for (const sw of document.querySelectorAll(".swatch")) {
    sw.classList.toggle("on", sw.dataset.accent.toLowerCase() === hex.toLowerCase());
  }
}

(function initAccent() {
  const saved = localStorage.getItem("accent");
  if (saved) applyAccent(saved);
  for (const sw of document.querySelectorAll(".swatch")) {
    sw.addEventListener("click", () => applyAccent(sw.dataset.accent));
  }
  document.getElementById("accent-pick").addEventListener("input", (e) => {
    applyAccent(e.target.value);
  });
})();

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
    const d = await api("/api/chat/new", { method: "POST" });
    els.msgs.textContent = "";
    renderHistory(d.timeline || []);
    applyAutoFlag(d);
    toast("New chat");
  } catch (e) {
    if (e.message !== "locked") toast("Failed: " + e.message, true);
  }
}

/* --------------------------------------------------------- chat history */

function fmtChatDate(ts) {
  const d = new Date(ts * 1000);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay) {
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
  if (d.getFullYear() === now.getFullYear()) {
    return d.toLocaleDateString([], { month: "short", day: "numeric" });
  }
  return d.toLocaleDateString();
}

async function openHistory() {
  if (!activeCharId) {
    toast("History is per character", true);
    return;
  }
  setHsSelect(false);
  try {
    const d = await api("/api/chats?char=" + encodeURIComponent(activeCharId));
    els.hsList.textContent = "";
    if (!d.chats.length) {
      els.hsList.appendChild(el("div", "hint", "No chats yet."));
    }
    for (const c of d.chats) {
      const row = el("button", "hist-row" +
        (c.id === d.active ? " active" : ""));
      row.appendChild(el("span", "hs-check", "✓"));
      const tx = el("div", "char-text");
      tx.appendChild(el("b", null, c.title || "New chat"));
      tx.appendChild(el("span", "dim", fmtChatDate(c.updated) +
        " · " + c.messages + " messages"));
      row.appendChild(tx);
      row.addEventListener("click", () => {
        if (hsSelecting) {
          const id = c.id;
          if (hsSelected.has(id)) {
            hsSelected.delete(id);
            row.classList.remove("sel");
          } else {
            hsSelected.add(id);
            row.classList.add("sel");
          }
          hsTitleSync();
        } else selectChat(c.id);
      });
      row.dataset.chatid = c.id;
      els.hsList.appendChild(row);
    }
    els.pageHist.hidden = false;
  } catch (e) {
    if (e.message !== "locked") toast("History failed: " + e.message, true);
  }
}

/* ---- batch select/delete (Chatterbox-style) ---- */

let hsSelecting = false;
const hsSelected = new Set();

function hsTitleSync() {
  const n = hsSelected.size;
  els.hsTitle.textContent = hsSelecting
    ? (n ? `${n} selected` : "Select chats")
    : "Chat history";
  els.hsDel.classList.toggle("armed", n > 0);
}

function setHsSelect(on) {
  hsSelecting = on;
  hsSelected.clear();
  els.pageHist.classList.toggle("selecting", on);
  els.hsSelect.hidden = on;
  els.hsNew.hidden = on;
  els.hsAll.hidden = !on;
  els.hsDel.hidden = !on;
  for (const row of els.hsList.querySelectorAll(".hist-row.sel")) {
    row.classList.remove("sel");
  }
  hsTitleSync();
}

async function deleteChatsBatch(ids) {
  let ok = 0, last = null;
  for (const id of ids) {
    try {
      const d = await api("/api/chat/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ char: activeCharId, chat: id }),
      });
      ok++;
      last = d;
    } catch (e) {
      if (e.message === "locked") break;
    }
  }
  toast(ok === ids.length
    ? `Deleted ${ok} chat${ok === 1 ? "" : "s"}`
    : `Deleted ${ok} of ${ids.length}`);
  if (last) {
    els.msgs.textContent = "";
    renderHistory(last.timeline || []);
    applyAutoFlag(last);
    refreshStatus();
  }
  await openHistory();
}

function selectChat(chatId) {
  api("/api/chat/select", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ char: activeCharId, chat: chatId }),
  }).then((d) => {
    els.msgs.textContent = "";
    renderHistory(d.timeline || []);
    applyAutoFlag(d);
    els.pageHist.hidden = true;
    switchView("chat");
  }).catch((e) => {
    if (e.message !== "locked") toast("Open failed: " + e.message, true);
  });
}

async function deleteChatById(chatId) {
  try {
    const d = await api("/api/chat/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ char: activeCharId, chat: chatId }),
    });
    toast("Chat deleted");
    els.msgs.textContent = "";
    renderHistory(d.timeline || []);
    applyAutoFlag(d);
    await openHistory();
  } catch (e) {
    if (e.message !== "locked") toast("Delete failed: " + e.message, true);
  }
}

els.btnHistory.addEventListener("click", openHistory);
els.hsBack.addEventListener("click", () => {
  if (hsSelecting) { setHsSelect(false); return; }
  els.pageHist.hidden = true;
});
els.hsSelect.addEventListener("click", () => setHsSelect(true));
els.hsAll.addEventListener("click", () => {
  const rows = [...els.hsList.querySelectorAll(".hist-row")];
  const all = rows.length && rows.every((r) => hsSelected.has(r.dataset.chatid));
  for (const r of rows) {
    if (all) { hsSelected.delete(r.dataset.chatid); r.classList.remove("sel"); }
    else { hsSelected.add(r.dataset.chatid); r.classList.add("sel"); }
  }
  hsTitleSync();
});
els.hsDel.addEventListener("click", () => {
  const ids = [...hsSelected];
  if (!ids.length) { toast("Nothing selected", true); return; }
  openCtx([
    { label: `🗑 Delete ${ids.length} chat${ids.length === 1 ? "" : "s"}`,
      danger: true, action: () => deleteChatsBatch(ids) },
  ], null);
});
els.hsNew.addEventListener("click", async () => {
  els.pageHist.hidden = true;
  await newChat();
});
els.hsList.addEventListener("contextmenu", (e) => {
  if (hsSelecting) return;
  const target = e.target.closest(".hist-row");
  if (!target) return;
  e.preventDefault();
  openCtx([{ label: "🗑 Delete", danger: true,
    action: () => deleteChatById(target.dataset.chatid) }], null);
});
let hsLpTimer = null, hsLpStart = null;
els.hsList.addEventListener("touchstart", (e) => {
  if (hsSelecting) return;
  const target = e.target.closest(".hist-row");
  if (!target) return;
  hsLpStart = target;
  hsLpTimer = setTimeout(() => {
    hsLpTimer = null;
    if (navigator.vibrate) navigator.vibrate(25);
    openCtx([{ label: "🗑 Delete", danger: true,
      action: () => deleteChatById(target.dataset.chatid) }], null);
    hsLpStart = null;
  }, 550);
}, { passive: true });
els.hsList.addEventListener("touchmove", (e) => {
  if (!hsLpTimer || !hsLpStart) return;
  if (Math.abs(e.touches[0].clientY - (hsLpStart._y || 0)) > 12) {
    clearTimeout(hsLpTimer); hsLpTimer = null;
  }
}, { passive: true });
els.hsList.addEventListener("touchstart", (e) => {
  if (hsLpStart) hsLpStart._y = e.touches[0].clientY;
}, { passive: true });
els.hsList.addEventListener("touchend", () => {
  if (hsLpTimer) { clearTimeout(hsLpTimer); hsLpTimer = null; hsLpStart = null; }
}, { passive: true });

function copyText(text) {
  navigator.clipboard.writeText(text)
    .then(() => toast("Copied"))
    .catch(() => toast("Copy failed", true));
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
    const rel = el.dataset.file;          // may live in a gallery folder
    const items = [];
    if (rel) items.push({ label: "↻ Regenerate", action: () => openRegen(rel) });
    if (rel) items.push({ label: "✎ Edit in chat", action: () => {
      // a bare file name — edit_image resolves it anywhere in the tree
      switchView("chat");
      els.input.value = `Edit ${baseName(rel)} — `;
      autosize(); els.input.focus(); els.send.classList.add("ready");
    }});
    if (rel) items.push({ label: "📁 Show in gallery", action: () => {
      els.pageGallery.hidden = false;
      setGalSelect(false);
      loadGallery(rel.includes("/") ? parentOf(rel) : "");
    }});
    items.push({ label: "🗑 Delete from chat", danger: true, action: () => deleteChatEvent(el) });
    return { items, previewSrc: rel ? "/thumb/" + rel : null };
  }
  if (el.classList.contains("msg")) {
    const items = [];
    const editable = el.classList.contains("ai")
                     && el.dataset.idx != null;
    if (!el.classList.contains("error")) {
      items.push({ label: "⧉ Copy",
        action: () => copyText(el.dataset.raw || el.textContent) });
    }
    if (editable) {
      items.push({ label: "✎ Edit message",
        action: () => startMsgEdit(el) });
      items.push({ label: "↻ Regenerate reply",
        action: () => regenReply(el) });
    }
    items.push({ label: "🗑 Delete", danger: true, action: () => deleteChatEvent(el) });
    return { items, previewSrc: null };
  }
  return null;
}

/* ----------------------------------------------------- editing an AI reply
 *
 * Inline, so the conversation stays readable while you fix it. Save patches
 * the bubble AND the server's LLM context, so the corrected text is what the
 * model carries into the next turn.
 */

function startMsgEdit(node) {
  if (node.querySelector(".msg-edit")) return;
  const raw = node.dataset.raw || node.textContent || "";
  const box = el("div", "msg-edit");
  const ta = el("textarea", null);
  ta.value = raw;
  ta.rows = Math.min(14, Math.max(3, raw.split("\n").length + 1));
  const row = el("div", "msg-edit-row");
  const cancel = el("button", null, "Cancel");
  const save = el("button", "primary", "Save");
  row.appendChild(cancel);
  row.appendChild(save);
  box.appendChild(ta);
  box.appendChild(row);
  node.classList.add("editing");
  node.textContent = "";
  node.appendChild(box);

  const done = () => {
    box.remove();
    node.classList.remove("editing");
    renderMsgText(node, raw);
  };
  cancel.addEventListener("click", done);
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.stopPropagation(); done(); }
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      save.click();
    }
  });
  save.addEventListener("click", async () => {
    const text = ta.value.trim();
    if (!text || text === raw) { done(); return; }
    save.disabled = true;
    save.textContent = "Saving…";
    try {
      const d = await api("/api/edit_event", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ index: +node.dataset.idx, text }),
      });
      done();
      renderMsgText(node, d.text || text);
    } catch (e) {
      if (e.message === "locked") return;
      save.disabled = false;
      save.textContent = "Save";
      toast("Edit failed: " + e.message, true);
    }
  });
  setTimeout(() => { ta.focus(); ta.setSelectionRange(ta.value.length,
                                                        ta.value.length); }, 30);
}

function renderMsgText(node, text) {
  node.dataset.raw = text;
  node.innerHTML = mdToHtml(text);
}

/* ---------------------------------------------- regenerate an AI reply --
 *
 * Asks the model to answer again from just before the old reply. The server
 * answers against a working copy and only commits once a new reply exists,
 * so a stop or failure leaves the chat as it was. The whole chat is
 * re-rendered when the turn lands, so bubbles and indices agree again.
 */

async function regenReply(node) {
  if (busy) { toast("Busy — wait for the current job", true); return; }
  const idx = +node.dataset.idx;
  if (!Number.isFinite(idx)) return;
  if (!confirm("Regenerate this reply?\n\nEverything after it in the chat "
               + "will be replaced.")) return;
  setBusy(true);
  switchView("chat");
  pill("rewriting…", false);
  busyAbort = new AbortController();
  try {
    const resp = await fetch("/api/regen_reply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index: idx }),
      signal: busyAbort.signal,
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
      if (d.error === "no_api_key") { openSettings(); return; }
      toast(d.error || "Regeneration failed", true);
      return;
    }
    await consumeSSE(resp, (evt) => {
      switch (evt.type) {
        case "status":
          pill(esc(evt.text), false);
          break;
        case "progress":
          pillProgress(evt.progress || 0, "regenerating…");
          break;
        case "tool_start":
          pill(evt.name + "…", false);
          break;
        case "generation": {
          // an image made while re-answering — show it above the new reply.
          // No index: the server commits the rewind only after the turn
          // lands, so anything streamed now is provisional. reloadChat()
          // below re-draws the whole chat with the real ones.
          addGeneration(evt, null);
          break;
        }
        case "reply":
          addBubble("ai", evt.text);
          break;
        case "error":
          addBubble("error", evt.text);
          break;
        case "stopped":
          // nothing was committed, so reloadChat() restores the old answer —
          // a bubble here would just flash and vanish
          toast("Stopped — the previous reply was kept");
          break;
        case "done":
          break;
      }
      scrollDown(true);
    });
    // the server rewound and re-answered — re-read the chat wholesale so
    // bubbles, variant cards and indices all agree again
    await reloadChat();
  } catch (e) {
    if (busyStop || e.message === "locked") return;
    pillHide();
    toast("Connection lost — " + e.message, true);
  } finally {
    busyAbort = null;
    pillHide();
    setBusy(false);
    refreshStatus();
  }
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
    const d = await api("/api/delete_event", {
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
    // every index after the removed one just shifted down, and the DOM has no
    // way to know by how much — re-render from the server's copy instead of
    // yanking the node and leaving stale data-idx on its neighbours
    if (d.timeline) {
      renderHistory(d.timeline);
      images = timelineImages(d.timeline);
    } else {
      node.remove();
      await reloadChat();
    }
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

/* ------------------------------------------------------- regenerate sheet
 *
 * Full recipe editor: both prompts, resolution, steps, CFG, sampler, schedule
 * type, distilled CFG, clip skip, batch, seed, checkpoint and the companion
 * components of a modular model — pre-filled from the image's own history
 * (newest chat snapshot, else its PNG parameters chunk). Everything is
 * optional — a blank field keeps whatever the original used.
 */

let regenRel = null;                 // rel path — may include folders
let regenModels = [];                // checkpoints, for the datalist
let regenArch = "";                  // architecture of the image's model
let regenProfile = null;             // its CFG/step guidance, when known
const regenEl = $("regen");
const regenF = {
  mode: $("regen-mode"),
  prompt: $("regen-prompt"),
  negative: $("regen-negative"),
  width: $("regen-w"),
  height: $("regen-h"),
  steps: $("regen-steps"),
  cfg: $("regen-cfg"),
  sampler: $("regen-sampler"),
  sched: $("regen-sched"),
  dcfg: $("regen-dcfg"),
  clip: $("regen-clip"),
  batch: $("regen-batch"),
  seed: $("regen-seed"),
  model: $("regen-model"),
  te: $("regen-te"),
  vae: $("regen-vae"),
  lb: $("regen-lb"),
  denoise: $("regen-denoise"),
  text: $("regen-text"),
  hint: $("regen-hint"),
};

function regenSetMode() {
  const edit = regenF.mode.value === "img2img";
  $("regen-denoise-row").hidden = !edit;
  $("regen-text-row").hidden = !edit;
}

/** Show the distilled-CFG field and the component block only where they
 *  apply: a distilled DiT has a real guidance value, and only a modular
 *  architecture has companion files to attach. */
function regenSyncArch() {
  const p = regenProfile;
  const distilled = !!(p && p.distilled);
  $("regen-dcfg-wrap").hidden = !distilled;
  const modular = !!p && !["sd", "xl"].includes(regenArch);
  $("regen-comp").hidden = !modular;
  const note = $("regen-arch");
  if (p) {
    const bits = [`${regenArch || "model"}`];
    if (distilled) {
      bits.push("low-guidance: CFG 1–2, few steps");
    } else if (modular) {
      bits.push(`CFG ${p.cfg_range ? p.cfg_range.join("–") : "4–6"}`);
    } else {
      bits.push(`CFG ${p.cfg_range ? p.cfg_range.join("–") : "4–7"}`);
    }
    if (p.video) bits.push("video model");
    note.textContent = bits.join(" · ");
    note.hidden = false;
  } else {
    note.hidden = true;
  }
}

function regenFillLists() {
  const caps = sdCaps || {};
  fillList("rg-samplers", caps.samplers, false);
  fillList("rg-scheds", schedLabels(caps.schedulers), false);
  fillList("rg-tes", caps.text_encoders, true);
  fillList("rg-vaes", ["Automatic"].concat((caps.vaes || [])
    .filter((v) => v !== "Automatic")), true);
  fillList("rg-lbs", caps.low_bits, false);
}

/** API schedule names -> the labels the WebUI shows. */
function schedLabels(names) {
  const special = {
    flow_match: "FlowMatchEulerDiscrete", flux2: "Flux2",
    sgm_uniform: "SGM Uniform", linear_quadratic: "Linear Quadratic",
    kl_optimal: "KL Optimal", align_your_steps: "Align Your Steps",
    bong_tangent: "Bong Tangent",
  };
  return (names || []).map((n) => special[n]
    || n.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()));
}

async function openRegen(rel) {
  regenRel = rel;
  regenProfile = null;
  regenArch = "";
  regenF.mode.value = "txt2img";
  regenF.text.value = "";
  regenF.prompt.value = regenF.negative.value = "";
  regenF.sampler.value = "";
  regenF.sched.value = "";
  regenF.dcfg.value = "";
  regenF.model.value = "";
  regenF.te.value = regenF.vae.value = regenF.lb.value = "";
  regenF.denoise.value = "0.65";
  regenF.hint.textContent = "Reading the original recipe…";
  regenSetMode();
  regenSyncArch();
  regenEl.hidden = false;
  try {
    const d = await api("/api/regen_meta?name=" + encodeURIComponent(rel));
    const g = d.gen || {};
    const def = d.defaults || {};
    regenF.prompt.value = g.prompt || "";
    regenF.negative.value = g.negative_prompt || "";
    regenF.width.value = g.width || def.width || 1024;
    regenF.height.value = g.height || def.height || 1024;
    regenF.steps.value = g.steps != null ? g.steps
      : (def.steps != null ? def.steps : 25);
    regenF.cfg.value = g.cfg_scale != null ? g.cfg_scale
      : (def.cfg_scale != null ? def.cfg_scale : 7);
    regenF.sampler.value = g.sampler_name || def.sampler_name || "Euler a";
    regenF.sched.value = g.scheduler && g.scheduler !== "automatic"
      ? g.scheduler : "";
    regenF.dcfg.value = g.distilled_cfg_scale != null
      ? g.distilled_cfg_scale : "";
    regenF.clip.value = g.clip_skip != null ? g.clip_skip
      : (def.clip_skip != null ? def.clip_skip : "");
    regenF.batch.value = g.batch_size != null ? g.batch_size : 1;
    regenF.seed.value = g.seed != null && g.seed >= 0 ? g.seed : "";
    regenF.model.value = g.model && g.model !== "(unknown)" ? g.model : "";
    regenF.te.value = g.text_encoder || "";
    regenF.vae.value = g.sd_vae || "";
    regenF.lb.value = g.low_bits || "";
    if (g.denoising_strength != null) {
      regenF.denoise.value = g.denoising_strength;
    }
    regenModels = d.models || [];
    fillList("rg-models", regenModels, false);
    regenArch = d.arch || g.arch || archOfModel(regenF.model.value) || "";
    regenProfile = d.profile
      || (sdCaps.architectures || []).find((a) => a.arch === regenArch)
      || null;
    if (d.capabilities) sdCaps = d.capabilities;
    regenFillLists();
    regenSyncArch();
    const src = d.origin === "png"
      ? "Read from the image's own settings."
      : "Editing this image's recipe — the newest version of it.";
    regenF.hint.textContent = (g.notes || []).length
      ? src + " Note: " + g.notes.join("; ")
      : src;
  } catch (e) {
    regenF.hint.textContent = "Could not read the original recipe; fill it in.";
  }
  setTimeout(() => regenF.prompt.focus(), 60);
}

function regenOverrides() {
  const mode = regenF.mode.value;
  const o = {
    mode,
    prompt: regenF.prompt.value,
    negative_prompt: regenF.negative.value,
    width: regenF.width.value,
    height: regenF.height.value,
    steps: regenF.steps.value,
    cfg_scale: regenF.cfg.value,
    sampler_name: regenF.sampler.value.trim(),
    scheduler: regenF.sched.value.trim(),
    clip_skip: regenF.clip.value,
    batch_size: regenF.batch.value,
    model: regenF.model.value.trim(),
  };
  const dcfg = regenF.dcfg.value.trim();
  if (dcfg) o.distilled_cfg_scale = dcfg;
  const te = regenF.te.value.trim();
  if (te && te !== "— clear —") o.text_encoder = te;
  const vae = regenF.vae.value.trim();
  if (vae && vae !== "— clear —") o.sd_vae = vae;
  const lb = regenF.lb.value.trim();
  if (lb) o.low_bits = lb;
  const seed = regenF.seed.value.trim();
  if (seed) o.seed = seed;
  if (mode === "img2img") o.denoising_strength = regenF.denoise.value;
  return o;
}

function closeRegen() {
  regenEl.hidden = true;
  regenRel = null;
}

$("regen-cancel").addEventListener("click", closeRegen);
regenEl.addEventListener("click", (e) => { if (e.target === regenEl) closeRegen(); });
regenF.mode.addEventListener("change", regenSetMode);
$("regen-go").addEventListener("click", () => {
  const rel = regenRel;
  if (!rel) return;
  const instr = regenF.text.value.trim();
  const over = regenOverrides();
  closeRegen();
  runRegen(rel, instr, over);
});
for (const f of [regenF.prompt, regenF.negative, regenF.text]) {
  f.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      $("regen-go").click();
    }
  });
}

/* ------------------------------------- direct regeneration (no chat message) */

async function runRegen(name, instr, overrides) {
  if (busy) { toast("Busy — wait for the current job", true); return; }
  setBusy(true);
  switchView("chat");
  const editing = overrides && overrides.mode === "img2img";
  pill(editing ? "applying change…" : "regenerating…", false);
  busyAbort = new AbortController();
  try {
    const resp = await fetch("/api/regenerate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, instruction: instr, overrides }),
      signal: busyAbort.signal,
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
          } else {
            evtCounter = Math.max(evtCounter, (evt.idx ?? -1) + 1);
          }
          if (parentOf(evt.file) === galFolder
              && !images.some((x) => x.rel === evt.file)) {
            images.unshift({ rel: evt.file, name: baseName(evt.file),
                             seed: evt.seed });
          }
          if (!els.pageGallery.hidden) loadGallery();
          toast("Image regenerated");
          break;
        }
        case "regen_error":
          pillHide();
          toast(evt.error, true);
          break;
        case "regen_stopped":
          pillHide();
          toast("Stopped");
          break;
        case "done":
          pillHide();
          break;
      }
    });
  } catch (e) {
    if (busyStop || e.message === "locked") return;
    pillHide();
    toast("Connection lost — " + e.message, true);
  } finally {
    busyAbort = null;
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

$("ps-back").addEventListener("click", () => { els.pageSettings.hidden = true; });
for (const b of document.querySelectorAll("#ps-tabs button")) {
  b.addEventListener("click", () => setPsTab(b.dataset.tab));
}
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
$("sh-comp-save").addEventListener("click", async (e) => {
  const comps = collectComps();
  // replace the whole map: an emptied card means "no companions for that arch"
  await saveSettings({ sd_components: comps }, e.currentTarget,
    "Components saved");
  savedComps = comps;
  renderComps(compArch);
});
$("sh-comp-apply").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  const arch = compArch;
  const values = {};
  for (const inp of els.shComps.querySelectorAll("input[data-field]")) {
    if (inp.dataset.arch !== arch) continue;
    let v = inp.value.trim();
    if (v === "— clear —") v = "";
    values[inp.dataset.field] = v;
  }
  btn.disabled = true;
  btn.textContent = "Applying…";
  try {
    await applyComps(arch, values);
  } catch (err) {
    if (err.message !== "locked") toast("Apply failed: " + err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "Apply now";
  }
});
$("sh-save-username").addEventListener("click", () => {
  saveSettings({ username: els.shUsername.value.trim() },
    $("sh-save-username"), "Name saved");
});
$("sh-logout").addEventListener("click", async () => {
  try { await api("/api/logout", { method: "POST" }); } catch { /* ignore */ }
  showGate();
});

/* ------------------------------------------------------------- personas */

let personas = [];            // user persona entities
let activePersonaId = "";
let editPersonaId = null;     // null = creating new

async function loadPersonas() {
  try {
    const d = await api("/api/personas");
    personas = d.personas || [];
    activePersonaId = d.active || "";
    renderPersonaList();
  } catch (e) {
    if (e.message !== "locked") toast("Personas failed: " + e.message, true);
  }
}

function renderPersonaList() {
  els.psList.textContent = "";
  els.psCount.textContent = personas.length
    ? `· ${personas.length}` : "· none yet";
  if (!personas.length) {
    els.psList.appendChild(el("div", "hint",
      "No personas yet — add one to give the AI a fuller picture of you."));
  }
  for (const p of personas) {
    const row = el("button", "persona-row" +
      (p.id === activePersonaId ? " active" : ""));
    row.dataset.personaid = p.id;
    row.appendChild(el("span", "dot"));
    const tx = el("div", "char-text");
    tx.appendChild(el("b", null, p.name || p.id));
    if (p.description) {
      tx.appendChild(el("span", "dim", p.description));
    }
    row.appendChild(tx);
    row.addEventListener("click", () => selectPersona(p.id));
    els.psList.appendChild(row);
  }
}

async function selectPersona(id) {
  try {
    await api("/api/persona/select", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    activePersonaId = id;
    renderPersonaList();
    toast(id ? "Persona selected" : "Persona cleared");
  } catch (e) {
    if (e.message !== "locked") toast("Select failed: " + e.message, true);
  }
}

function openPersonaForm(p) {
  editPersonaId = p ? p.id : null;
  els.ppTitle.textContent = p ? "Edit persona" : "New persona";
  els.ppName.value = p ? (p.name || "") : "";
  els.ppDesc.value = p ? (p.description || "") : "";
  els.ppDelete.hidden = !p;
  els.pagePersona.hidden = false;
  setTimeout(() => els.ppName.focus(), 60);
}

async function savePersonaForm() {
  const name = els.ppName.value.trim();
  if (!name) { toast("Name required", true); return; }
  try {
    const d = await api("/api/personas", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: editPersonaId || "",
                             name,
                             description: els.ppDesc.value.trim() }),
    });
    personas = d.personas || [];
    if (activePersonaId === (d.id)) {
      // server already rebuilt the prompt; just reflect possible renames
      activePersonaId = d.id;
    }
    renderPersonaList();
    els.pagePersona.hidden = true;
    toast("Persona saved");
  } catch (e) {
    if (e.message !== "locked") toast("Save failed: " + e.message, true);
  }
}

async function deletePersonaById(pid) {
  try {
    const d = await api("/api/persona/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: pid }),
    });
    personas = d.personas || [];
    activePersonaId = d.active || "";
    renderPersonaList();
    els.pagePersona.hidden = true;
    toast("Persona deleted");
  } catch (e) {
    if (e.message !== "locked") toast("Delete failed: " + e.message, true);
  }
}

function personaCtx(row) {
  const p = personas.find((x) => x.id === row.dataset.personaid);
  if (!p) return;
  openCtx([
    { label: "✎ Edit", action: () => openPersonaForm(p) },
    { label: "🗑 Delete", danger: true,
      action: () => deletePersonaById(p.id) },
  ], null);
}

$("ps-add").addEventListener("click", () => openPersonaForm(null));
$("pp-back").addEventListener("click", () => { els.pagePersona.hidden = true; });
$("pp-save").addEventListener("click", savePersonaForm);
$("pp-delete").addEventListener("click", () =>
  deletePersonaById(editPersonaId));
els.psList.addEventListener("contextmenu", (e) => {
  const target = e.target.closest(".persona-row");
  if (!target) return;
  e.preventDefault();
  personaCtx(target);
});
let psLpTimer = null, psLpStart = null;
els.psList.addEventListener("touchstart", (e) => {
  const target = e.target.closest(".persona-row");
  if (!target) return;
  psLpStart = { x: e.touches[0].clientX, y: e.touches[0].clientY, target };
  psLpTimer = setTimeout(() => {
    psLpTimer = null;
    if (navigator.vibrate) navigator.vibrate(25);
    personaCtx(psLpStart.target);
    psLpStart = null;
  }, 550);
}, { passive: true });
els.psList.addEventListener("touchmove", (e) => {
  if (!psLpTimer || !psLpStart) return;
  const dx = e.touches[0].clientX - psLpStart.x;
  const dy = e.touches[0].clientY - psLpStart.y;
  if (dx * dx + dy * dy > 144) { clearTimeout(psLpTimer); psLpTimer = null; }
}, { passive: true });
els.psList.addEventListener("touchend", () => {
  if (psLpTimer) { clearTimeout(psLpTimer); psLpTimer = null; psLpStart = null; }
}, { passive: true });

/* ------------------------------------------------------------ scenarios */

let scenarioCharId = null;    // character the scenario form edits
let editScenarioId = null;    // null = creating new

function currentScenarioCard() {
  return chars.find((c) => c.id === scenarioCharId) || null;
}

function renderScenarioList() {
  els.scList.textContent = "";
  const card = currentScenarioCard();
  const scenarios = (card && card.scenarios) || [];
  els.scCount.textContent = scenarios.length
    ? `· ${scenarios.length}` : "· none";
  if (!scenarios.length) {
    els.scList.appendChild(el("div", "hint",
      "No scenarios yet — chat presets with their own setting and first message."));
  }
  for (const s of scenarios) {
    const row = el("button", "sc-row");
    row.dataset.scenarioid = s.id;
    const tx = el("div", "char-text");
    tx.appendChild(el("b", null, s.name || s.id));
    if (s.description) tx.appendChild(el("span", "dim", s.description));
    row.appendChild(tx);
    row.addEventListener("click", () => openScenarioForm(s));
    els.scList.appendChild(row);
  }
}

function openScenarioForm(s) {
  editScenarioId = s ? s.id : null;
  pendingScCover = "";
  clearScCover = false;
  els.scsTitle.textContent = s ? "Edit scenario" : "New scenario";
  els.scsName.value = s ? (s.name || "") : "";
  els.scsDesc.value = s ? (s.description || "") : "";
  els.scsFirst.value = s ? (s.first_message || "") : "";
  setPreview(els.scsCoverImg, els.scsCoverClear, (s && s.cover) || "");
  els.scsDelete.hidden = !s;
  els.pageScenario.hidden = false;
  setTimeout(() => els.scsName.focus(), 60);
}

async function saveScenarioForm() {
  const name = els.scsName.value.trim();
  if (!name) { toast("Name required", true); return; }
  const payload = {
    char_id: scenarioCharId,
    id: editScenarioId || "",
    name,
    description: els.scsDesc.value.trim(),
    first_message: els.scsFirst.value.trim(),
  };
  if (pendingScCover) payload.cover = pendingScCover;
  if (clearScCover) payload.cover_remove = true;
  try {
    const d = await api("/api/scenario", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const card = currentScenarioCard();
    if (card) card.scenarios = d.scenarios || [];
    renderScenarioList();
    pendingScCover = "";
    clearScCover = false;
    els.pageScenario.hidden = true;
    toast("Scenario saved");
  } catch (e) {
    if (e.message !== "locked") toast("Save failed: " + e.message, true);
  }
}

async function deleteScenarioById(sid) {
  try {
    const d = await api("/api/scenario/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ char_id: scenarioCharId, id: sid }),
    });
    const card = currentScenarioCard();
    if (card) card.scenarios = d.scenarios || [];
    renderScenarioList();
    els.pageScenario.hidden = true;
    toast("Scenario deleted");
  } catch (e) {
    if (e.message !== "locked") toast("Delete failed: " + e.message, true);
  }
}

async function startScenarioChat(cid, sid) {
  try {
    if (cid !== activeCharId) {
      await api("/api/character/select", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: cid }),
      });
    }
    const d = await api("/api/chat/scenario", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scenario_id: sid }),
    });
    activeCharId = cid;
    els.msgs.textContent = "";
    renderHistory(d.timeline || []);
    switchView("chat");
    refreshStatus();
    toast(sid ? "Scenario chat started" : "New chat");
  } catch (e) {
    if (e.message !== "locked") toast("Start failed: " + e.message, true);
  }
}

function startScenarioMenu(c) {
  const list = c.scenarios || [];
  if (!list.length) {
    toast("No scenarios — add one in the character editor", true);
    return;
  }
  openCtx(list.map((s) => ({
    label: "🎬 " + (s.name || s.id),
    action: () => startScenarioChat(c.id, s.id),
  })), null);
}

$("sc-add").addEventListener("click", () => {
  if (!scenarioCharId) { toast("Save the character first", true); return; }
  openScenarioForm(null);
});
$("scs-back").addEventListener("click", () => {
  els.pageScenario.hidden = true;
});
$("scs-save").addEventListener("click", saveScenarioForm);
$("scs-delete").addEventListener("click", () =>
  deleteScenarioById(editScenarioId));
els.scsCoverBtn.addEventListener("click", () => els.scsCoverFile.click());
els.scsCoverFile.addEventListener("change", () => {
  if (els.scsCoverFile.files[0]) pickCover(els.scsCoverFile.files[0], "sc");
  els.scsCoverFile.value = "";
});
els.scsCoverAi.addEventListener("click", () => openAiSheet("scenario-cover"));
els.scsCoverClear.addEventListener("click", () => {
  pendingScCover = "";
  clearScCover = true;
  setPreview(els.scsCoverImg, els.scsCoverClear, "");
});
els.scList.addEventListener("contextmenu", (e) => {
  const target = e.target.closest(".sc-row");
  if (!target) return;
  e.preventDefault();
  const card = currentScenarioCard();
  const s = ((card && card.scenarios) || [])
    .find((x) => x.id === target.dataset.scenarioid);
  if (!s) return;
  openCtx([
    { label: "✎ Edit", action: () => openScenarioForm(s) },
    { label: "🗑 Delete", danger: true,
      action: () => deleteScenarioById(s.id) },
  ], null);
});

els.lbClose.addEventListener("click", closeLightbox);
els.lbInfo.addEventListener("click", () => toggleLbInfo());
els.lbPrev.addEventListener("click", () => lbMove(-1));
els.lbNext.addEventListener("click", () => lbMove(1));
// tapping the picture dismisses the info popup (swipe still navigates)
els.lbStage.addEventListener("click", (e) => {
  if (e.target.closest(".lb-nav")) return;
  if (els.lbSheet.classList.contains("open")) toggleLbInfo(false);
});
$("lb-download").addEventListener("click", () => {
  const im = lbList[lbIndex];
  if (im) downloadImage(im.rel || im.name);
});
$("lb-delete").addEventListener("click", () => {
  const im = lbList[lbIndex];
  if (im) deleteImages([im.rel || im.name]);
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
    if (e.key === "Escape") {
      if (els.lbSheet.classList.contains("open")) toggleLbInfo(false);
      else closeLightbox();
    }
    if (e.key === "ArrowLeft") lbMove(-1);
    if (e.key === "ArrowRight") lbMove(1);
  } else if (e.key === "Escape" && !els.pageSettings.hidden) {
    els.pageSettings.hidden = true;
  } else if (e.key === "Escape" && !els.pagePersona.hidden) {
    els.pagePersona.hidden = true;
  } else if (e.key === "Escape" && !els.pageScenario.hidden) {
    els.pageScenario.hidden = true;
  } else if (e.key === "Escape" && !els.pageChform.hidden) {
    els.pageChform.hidden = true;
  } else if (e.key === "Escape" && !els.pageHist.hidden) {
    els.pageHist.hidden = true;
  } else if (e.key === "Escape" && !els.foldSheet.hidden) {
    els.foldSheet.hidden = true;
  } else if (e.key === "Escape" && !els.foldPick.hidden) {
    els.foldPick.hidden = true;
  } else if (e.key === "Escape" && !els.pageGallery.hidden) {
    galleryBack();
  } else if (e.key === "Escape" && !ctxEl.hidden) {
    closeCtx();
  } else if (e.key === "Escape" && !regenEl.hidden) {
    regenEl.hidden = true;
  } else if (e.key === "Escape" && !els.aiSheet.hidden) {
    els.aiSheet.hidden = true;
  }
});

/* ------------------------------------------------------------------ init */

let inited = false;

async function init() {
  if (inited) return;
  inited = true;
  switchView("chars");                    // the app opens on the character list
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

// pull image rel paths out of a restored timeline (newest first)
function timelineImages(timeline) {
  const seen = new Set();
  const out = [];
  for (const evt of timeline) {
    if (evt.type !== "generation") continue;
    for (const f of evt.files || []) {
      const rel = relFromUrl(f);
      if (!seen.has(rel)) {
        seen.add(rel);
        out.push({ rel, name: baseName(rel), seed: evt.seed });
      }
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
