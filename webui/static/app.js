/* Anima — frontend logic (PWA) */
"use strict";

/* ------------------------------------------------- version-skew guard (FIRST)
   index.html and app.js are versioned independently, so a tab can end up
   holding an index.html that asks for a script the server has already moved
   on from — after a deploy, mid-refresh, or when the service worker falls back
   to a cached shell. The old HTML then lacks elements this script wires up,
   and a top-level `null.addEventListener` throws: every later line is dead,
   including the service-worker reload further down, so the app never recovers
   on its own. It looked like "the character list won't load".

   So: notice the skew before anything else can throw, and reload once onto
   matching assets. `on()` below keeps a missing element from being fatal even
   if this guard is itself out of date. */
(function guardSkew() {
  // one id added by each version since the wiring went null-safe; if the newest
  // one is missing, this DOM predates this script
  const probe = ["llm-chain", "sh-llm-pick", "pp-appearance"];
  if (probe.some((id) => document.getElementById(id))) return;
  const tries = Number(sessionStorage.getItem("skew-reload") || 0);
  if (tries >= 2) return;                  // give up rather than reload forever
  sessionStorage.setItem("skew-reload", String(tries + 1));
  // cache-bust the shell so the reload cannot be answered from the same cache
  location.replace(location.pathname + "?r=" + Date.now());
})();

const $ = (id) => document.getElementById(id);

/* ------------------------------------------------ stale-build guard
   The guard above catches old DOM + new JS. It cannot catch the other
   direction, which is what actually happens in practice: a proxy in front of
   this server rewrites `no-cache` into a multi-hour `max-age`, so index.html,
   app.js and style.css all sit in the browser cache together, consistently
   agreeing — on a build several versions old. Nothing is mismatched, so
   nothing reloads, and the page just renders the old UI.

   So ask the origin which build index.html points at, and compare that with
   the script tag that is actually running. If they differ, this page is
   stale no matter how internally consistent it is. */
(function guardStaleBuild() {
  const self_src = document.querySelector('script[src*="app.js"]');
  const mine = self_src && /app\.js\?v=(\d+)/.exec(self_src.getAttribute("src") || "");
  if (!mine) return;
  if (Number(sessionStorage.getItem("stale-reload") || 0) >= 2) return;
  fetch("index.html", { cache: "no-store" })
    .then((r) => (r.ok ? r.text() : ""))
    .then((html) => {
      const m = /app\.js\?v=(\d+)/.exec(html || "");
      if (!m || m[1] === mine[1]) return;
      sessionStorage.setItem("stale-reload",
        String(Number(sessionStorage.getItem("stale-reload") || 0) + 1));
      location.reload();
    })
    .catch(() => { /* offline: the service worker cache is the best we have */ });
})();

/** addEventListener that tolerates a missing element. Returns the element (or
    null) so callers can still chain when they need to. */
function on(id, event, fn, opts) {
  const el = typeof id === "string" ? $(id) : id;
  if (el) el.addEventListener(event, fn, opts);
  return el;
}

/* Register the service worker FIRST too: the skew guard above and the takeover
   reload are the app's only self-repair, so nothing above may throw before
   them. (Moved out of the bottom of the file, where a single bad listener
   above could take them down with it.) */
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.addEventListener("message", (e) => {
    if (e.data && e.data.type === "sw-takeover" &&
        !sessionStorage.getItem("sw-reloaded")) {
      sessionStorage.setItem("sw-reloaded", "1");
      location.reload();
    }
  });
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => { /* optional */ });
  });
}

const els = {
  gate: $("gate"), gateUser: $("gate-user"),
  gatePw: $("gate-pw"), gateBtn: $("gate-btn"),
  gateErr: $("gate-err"), app: $("app"),
  viewChars: $("view-chars"), viewChat: $("view-chat"),
  pageGallery: $("page-gallery"),
  charSearch: $("char-search"), charlist: $("charlist"),
  charTagBar: $("char-tags"),
  charempty: $("charempty"),
  chatScroll: $("chat-scroll"), chatCharname: $("chat-charname"),
  btnHistory: $("btn-history"), pageHist: $("page-hist"),
  btnChmenu: $("btn-chmenu"),
  pageChform: $("page-chform"), chTitle: $("ch-title"),
  cfBack: $("cf-back"), cfSave: $("cf-save"),
  hsBack: $("hs-back"), hsList: $("hs-list"),
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
  lbScroll: $("lb-scroll"),
  pageSettings: $("page-settings"), shModels: $("sh-models"), shLoad: $("sh-load"),
  psList: $("ps-list"), psCount: $("ps-count"),
  pagePersona: $("page-persona"), ppTitle: $("pp-title"),
  ppName: $("pp-name"), ppDesc: $("pp-desc"), ppDelete: $("pp-delete"),
  ppAppearance: $("pp-appearance"),
  scList: $("sc-list"), scCount: $("sc-count"),
  pageScenario: $("page-scenario"), scsTitle: $("scs-title"),
  scsName: $("scs-name"), scsDesc: $("scs-desc"), scsFirst: $("scs-first"),
  scsDelete: $("scs-delete"),
  shCur: $("sh-cur"), shSd: $("sh-sd"), shLlm: $("sh-llm"), shKey: $("sh-key"),
  shKeymask: $("sh-keymask"), shSdok: $("sh-sdok"),
  llmChain: $("llm-chain"), shLlmState: $("sh-llm-state"),
  shLlmPick: $("sh-llm-pick"), shLlmRefresh: $("sh-llm-refresh"),
  shLlmAdd: $("sh-llm-add"), shLlmAddGo: $("sh-llm-addgo"),
  shModelhint: $("sh-modelhint"),
  shArch: $("sh-arch"), shComps: $("sh-comps"),
  shCompActions: $("sh-comp-actions"),
  shUsername: $("sh-username"),
  shSys: $("sh-sysprompt"), shSysState: $("sh-sysstate"),
  shGuide: $("sh-guide"), shGuideState: $("sh-guidestate"),
  chName: $("ch-name"),
  chAvatarImg: $("ch-avatar-img"), chAvatarBtn: $("ch-avatar-btn"),
  chAvatarFile: $("ch-avatar-file"),
  chAvatarClear: $("ch-avatar-clear"),
  chCoverImg: $("ch-cover-img"), chCoverBtn: $("ch-cover-btn"),
  chCoverFile: $("ch-cover-file"),
  chCoverClear: $("ch-cover-clear"), chAiFab: $("ch-aifab"),
  aiSheet: $("aisheet"), aiTitle: $("ai-title"), aiHint: $("ai-hint"),
  aiText: $("ai-text"), aiGo: $("ai-go"), aiCancel: $("ai-cancel"),
  aiLog: $("ai-log"),
  scsCoverImg: $("scs-cover-img"), scsCoverBtn: $("scs-cover-btn"),
  scsCoverFile: $("scs-cover-file"),
  scsCoverClear: $("scs-cover-clear"),
  pageGalpick: $("page-galpick"), gpBack: $("gp-back"), gpTitle: $("gp-title"),
  gpUpload: $("gp-upload"), gpCrumbs: $("gp-crumbs"),
  gpFolders: $("gp-folders"), gpGrid: $("gp-grid"),
  gpEmpty: $("gp-empty"), gpEmptyText: $("gp-empty-text"),
  chAppearance: $("ch-appearance"), chPersona: $("ch-persona"),
  chGreeting: $("ch-greeting"), chModel: $("ch-model"), chSize: $("ch-size"),
  chTags: $("ch-tags"),
  chLoraList: $("ch-lora-list"), chLoraCount: $("ch-loracount"),
  shLoraList: $("sh-lora-list"), shLoraQ: $("sh-lora-q"),
  shLoraArch: $("sh-lora-arch"), shLoraRefresh: $("sh-lora-refresh"),
  shLoraNote: $("sh-lora-note"), shLoraUse: $("sh-lora-use"),
  shLoraCount: $("sh-loracount"),
  shDlBox: $("sh-dl-box"), shDlQ: $("sh-dl-q"), shDlSort: $("sh-dl-sort"),
  shDlGo: $("sh-dl-go"), shDlList: $("sh-dl-list"), shDlMore: $("sh-dl-more"),
  shDlState: $("sh-dl-state"), shDlPeriod: $("sh-dl-period"),
  shDlTag: $("sh-dl-tag"), shDlArch: $("sh-dl-arch"), shDlSfw: $("sh-dl-sfw"),
  loraModal: $("lora-modal"), loraModalCard: $("lora-modal-card"),
  chTemp: $("ch-temp"), chMaxtok: $("ch-maxtok"),
  chShared: $("ch-shared"),
  chDelete: $("ch-delete"),
  toast: $("toast"),
};

let images = [];          // gallery list
let busy = false;
let statusTimer = null;   // progress polling while generating
let toastTimer = null;
let authed = false;
let me = null;            // {id, name, role, admin} from /api/auth
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
  forgetUser();
  setTimeout(() => els.gatePw.focus(), 50);
}

/** Drop everything scoped to whoever was signed in.
 *
 *  Not tidiness. `init()` is a one-shot (`if (inited) return`), so without
 *  this, logging out and back in as somebody else re-showed the previous
 *  user's rendered timeline — the DOM was hidden, never emptied. Same class
 *  of bug as the cross-user reads Phase 1 closed, one layer up.
 *
 *  `me` goes null so the Admin tab disappears on logout rather than lingering
 *  until the next person logs in. `galFolder` must reset too: the gallery
 *  folder is a path in *their* outputs tree, and opening it as another user
 *  would 404. */
function forgetUser() {
  me = null;
  inited = false;
  chars = [];
  images = [];
  personas = [];
  activeCharId = "";
  editCharId = null;
  activePersonaId = "";
  chLoraSel = [];
  chLoraDirty = false;
  galFolder = "";
  galFolders = [];
  galTree = [];
  galSearchQuery = "";
  galSearchInfo = null;
  lbList = [];
  lbIndex = -1;
  busyStop = true;          // a turn still streaming belongs to the old user
}

function showApp() {
  authed = true;
  els.gate.hidden = true;
  els.app.hidden = false;
  init();
  initAdmin();          // the Admin tab exists only for admins
}

async function tryLogin() {
  const pw = els.gatePw.value;
  if (!pw) return;
  const name = (els.gateUser.value || "").trim();
  els.gateBtn.disabled = true;
  els.gateErr.hidden = true;
  try {
    const d = await api("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, password: pw }),
    });
    // The server answers with the user, so the Admin tab follows a switch of
    // accounts without a reload. Clear the old user's state first, then adopt
    // the new identity — forgetUser nulls `me` on purpose.
    forgetUser();
    me = d.user || null;
    showApp();
  } catch (e) {
    els.gateErr.hidden = false;
    // api() throws "locked" on a 401 and re-shows the gate itself; anything
    // else is the server's own message ("wrong password", "unknown user").
    const m = e && e.message;
    els.gateErr.textContent =
      !m || m === "locked" || /^(HTTP|failed)/i.test(m)
        ? "Wrong username or password" : m;
    els.gatePw.select();
  } finally {
    els.gateBtn.disabled = false;
  }
}

els.gateBtn.addEventListener("click", tryLogin);
for (const f of [els.gateUser, els.gatePw]) {
  f.addEventListener("keydown", (e) => {
    if (e.key === "Enter") tryLogin();
  });
}
// Username first: a returning user should not have to reach for the tab.
els.gateUser.addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); els.gatePw.focus(); }
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
      // the server retires the rating once the reply lands — the chat it was
      // given for is gone
      chatRating = "";
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
    { label: "Open", action: () => loadGallery(rel) },
    { label: "New folder inside", action: () => newFolderIn(rel) },
    { label: "Rename", action: () => openFoldSheet("rename", rel, baseName(rel)) },
    { label: "Move to…", action: () => openFolderPicker(rel, true) },
    { label: `Delete folder (${count} image${count === 1 ? "" : "s"})`,
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
    { label: "Open", action: () => openLightbox(rel) },
    { label: "Move to…", action: () => openFolderPicker([rel], false) },
    { label: "Download", action: () => downloadImage(rel) },
    { label: "Delete", danger: true, action: () => deleteImages([rel]) },
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

/* the chat view holds /outputs/ URLs — re-read it after files move, and
   after a delete that may have landed on a different chat */
async function reloadChat() {
  try {
    const h = await api("/api/history");
    renderHistory(h.timeline || []);
    images = timelineImages(h.timeline || []);
    applyChatFlags(h);
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

// lightbox browsing mode — "slideshow" (one image + arrows) or "scroll"
// (whole list stacked as a vertical feed). Persisted like the accent color.
function lbMode() { return localStorage.getItem("lbmode") === "scroll" ? "scroll" : "slideshow"; }
function lbIsScroll() { return els.lb.classList.contains("scroll"); }

function openLightbox(rel, list) {
  const src = list || images;
  lbList = src.slice();
  lbIndex = lbList.findIndex((x) => x.rel === rel);
  if (lbIndex < 0) {
    lbList.unshift({ rel, name: baseName(rel), seed: null });
    lbIndex = 0;
  }
  toggleLbInfo(false);
  els.lb.classList.toggle("scroll", lbMode() === "scroll");
  if (lbIsScroll()) lbBuildScroll(); else lbClearScroll();
  els.lb.hidden = false;          // unhide first — the scroll position below
  showLightbox();                 // needs the stage's real height
}

function closeLightbox() {
  els.lb.hidden = true;
  lbToken++;                      // pending metadata fetches are moot now
  if (lbIsScroll()) lbClearScroll();  // drop the feed's decoded images
}

// build the scroll feed: one full-height slide per image. `loading="lazy"`
// keeps far-away images from loading until they approach the viewport.
function lbBuildScroll() {
  const wrap = els.lbScroll;
  wrap.textContent = "";
  for (const im of lbList) {
    const img = document.createElement("img");
    img.loading = "lazy";
    img.decoding = "async";
    img.alt = "";
    img.src = "/outputs/" + (im.rel || im.name);
    wrap.appendChild(el("div", "lb-slide", "")).appendChild(img);
  }
}

function lbClearScroll() { els.lbScroll.textContent = ""; }

// position the feed on slide `idx` and sync the header/info to it
function lbJump(idx) {
  lbIndex = idx;
  const st = els.lbStage;
  st.scrollTop = idx * Math.max(1, st.clientHeight);
  const im = lbList[idx];
  els.lbName.textContent = baseName(im.rel || im.name);
  if (els.lbSheet.classList.contains("open")) { lbResetInfo(); lbFetchInfo(im); }
}

function showLightbox() {
  const im = lbList[lbIndex];
  if (!im) return;
  els.lbName.textContent = baseName(im.rel || im.name);
  lbResetInfo();
  if (lbIsScroll()) { lbJump(lbIndex); return; }
  els.lbImg.src = "/outputs/" + (im.rel || im.name);
  els.lbPrev.style.visibility = lbIndex > 0 ? "visible" : "hidden";
  els.lbNext.style.visibility = lbIndex < lbList.length - 1 ? "visible" : "hidden";
  lbFetchInfo(im);
}

function lbResetInfo() {
  els.lbMeta.textContent = "";
  els.lbPrompt.textContent = "";
  els.lbNeg.textContent = "";
  els.lbParams.textContent = "";
  [els.lbPromptWrap, els.lbNegWrap, els.lbParamsWrap].forEach((w) => { w.hidden = true; });
}

function lbFetchInfo(im) {
  const rel = im.rel || im.name;
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

/* Guide entry = the prompt, the negative, then the parameters blob — the shape
   the entries in model_guide.txt take. No "### <checkpoint>" header: the model
   already rides along inside the parameters as "Model: ...", so a header would
   only repeat it. */
function lbEntryText() {
  const out = [];
  const prompt = els.lbPrompt.textContent.trim();
  if (prompt) out.push(prompt);
  const neg = els.lbNeg.textContent.trim();
  if (neg) out.push("Negative prompt: " + neg);
  const params = els.lbParams.textContent.trim();
  if (params) out.push(params);
  return out.join("\n");
}

function lbMove(d) {
  const ni = lbIndex + d;
  if (ni < 0 || ni >= lbList.length) return;
  if (lbIsScroll()) { lbJump(ni); return; }
  lbIndex = ni;
  showLightbox();
}

// scroll-mode index tracking: the centered slide is the current image.
// Scroll-snap makes slide height == stage height, so the math is exact.
function lbOnScroll() {
  if (els.lb.hidden || !lbIsScroll()) return;
  const st = els.lbStage;
  const idx = Math.max(0, Math.min(lbList.length - 1,
    Math.round(st.scrollTop / Math.max(1, st.clientHeight))));
  if (idx === lbIndex) return;
  const im = lbList[idx];
  els.lbName.textContent = baseName(im.rel || im.name);
  if (els.lbSheet.classList.contains("open")) { lbResetInfo(); lbFetchInfo(im); }
  lbIndex = idx;
}

// the (i) button slides the info popup over the image; in scroll mode the
// popup loads metadata on demand (scrolling itself fires no requests)
function toggleLbInfo(open) {
  const show = open === undefined ? !els.lbSheet.classList.contains("open") : !!open;
  els.lbSheet.classList.toggle("open", show);
  els.lbInfo.classList.toggle("on", show);
  if (show && lbIsScroll() && lbList[lbIndex]) { lbResetInfo(); lbFetchInfo(lbList[lbIndex]); }
}

/* ---------------------------------------------- themed dropdown */
/* Native <select>/<datalist> popups draw with OS styling (a white list on
   desktop, the system sheet on Android). These wrappers keep the host
   element (hidden) as the single source of truth for .value and replace
   only the popup with an anchored, themed panel. */

const dd = { backdrop: null, panel: null, anchor: null, onKey: null, onView: null, raf: 0 };

function ddClose() {
  if (!dd.backdrop) return;
  dd.backdrop.remove();
  document.removeEventListener("keydown", dd.onKey, true);
  window.removeEventListener("scroll", dd.onView, true);
  window.removeEventListener("resize", dd.onView);
  const vv = window.visualViewport;
  if (vv) {
    vv.removeEventListener("resize", dd.onView);
    vv.removeEventListener("scroll", dd.onView);
  }
  if (dd.raf) cancelAnimationFrame(dd.raf);
  dd.backdrop = dd.panel = dd.anchor = dd.onKey = dd.onView = null;
  dd.raf = 0;
}

/** Open the themed panel under `anchor`. items: [{value, label, group?, dim?}]
    — a group string renders as a non-interactive section header. */
function ddOpen(anchor, items, current, onPick) {
  ddClose();
  const backdrop = document.createElement("div");
  backdrop.className = "dd";
  const panel = el("div", "dd-panel");
  const list = el("div", "dd-list");
  dd.onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); ddClose(); } };

  const searchable = items.length > 8;
  let query = "";
  const render = () => {
    list.textContent = "";
    const q = query.trim().toLowerCase();
    let lastGroup = null, shown = 0;
    for (const it of items) {
      if (q && !it.label.toLowerCase().includes(q)) continue;
      if (it.group) {
        if (it.group !== lastGroup) {
          lastGroup = it.group;
          list.appendChild(el("div", "dd-group", it.group));
        }
      } else lastGroup = null;
      const b = el("button",
        "dd-item" + (it.value === current ? " on" : "") + (it.dim ? " dim" : ""),
        it.label);
      b.type = "button";
      b.title = it.label;
      b.addEventListener("click", () => { ddClose(); onPick(it.value); });
      list.appendChild(b);
      shown++;
    }
    if (!shown) list.appendChild(el("div", "dd-none", "No match"));
  };
  render();

  let search = null;
  if (searchable) {
    search = document.createElement("input");
    search.className = "dd-search";
    search.placeholder = "Filter…";
    search.autocomplete = "off";
    search.spellcheck = false;
    search.setAttribute("inputmode", "search");
    search.addEventListener("input", () => { query = search.value; render(); });
    panel.appendChild(search);
  }
  panel.appendChild(list);
  backdrop.appendChild(panel);
  backdrop.addEventListener("pointerdown",
    (e) => { if (e.target === backdrop) ddClose(); });
  document.body.appendChild(backdrop);

  // position under the anchor; flip above when there is no room below.
  //
  // The panel FOLLOWS the anchor instead of closing when it moves. On a phone
  // the anchor shifts for reasons the user never asked for — momentum left
  // over from the tap that opened the menu, the soft keyboard, the collapsing
  // URL bar, scroll chaining from the list — and the old 2px tolerance closed
  // the menu on every one of them, so the options and the filter box were
  // unreachable. Worst of all the tap itself was the trigger: the button is
  // pressed with scale(.97) while click() runs, so the anchor was measured
  // ~1.5% of its width off and the next scroll event dismissed the menu the
  // instant it appeared. Only a field that has actually left the visible area
  // closes it now.
  let seen = "";                        // anchor + visible area; skip no-op frames
  const place = () => {
    const r = anchor.getBoundingClientRect();
    const vv = window.visualViewport;
    // the keyboard shrinks the visual viewport, not the window: measure
    // against what the user can actually see so the filter stays reachable
    const vTop = vv ? vv.offsetTop : 0;
    const vLeft = vv ? vv.offsetLeft : 0;
    const vH = vv ? vv.height : window.innerHeight;
    const vW = vv ? vv.width : document.documentElement.clientWidth;
    // scrolling the list moves neither the field nor the visible area — skip
    // the reflow rather than re-measure it on every frame of a drag
    const sig = [r.left, r.top, r.width, vTop, vLeft, vH, vW].map(Math.round).join();
    if (sig === seen) return;
    seen = sig;
    if (r.bottom < vTop + 4 || r.top > vTop + vH - 4
        || r.right < vLeft + 4 || r.left > vLeft + vW - 4) { ddClose(); return; }
    const w = Math.min(Math.max(r.width, 210), vW - 16);
    panel.style.minWidth = w + "px";
    panel.style.maxHeight = Math.round(vH * 0.58) + "px";
    panel.style.left = Math.round(
      Math.min(Math.max(8, r.left), Math.max(8, vLeft + vW - w - 8))) + "px";
    // park at the top edge first so offsetHeight is measured at the real width
    panel.style.top = Math.round(
      Math.min(Math.max(vTop + 8, r.top), Math.max(vTop + 8, vTop + vH - 8))) + "px";
    const ph = panel.offsetHeight;
    let top = r.bottom + 6;
    if (top + ph > vTop + vH - 8) top = r.top - ph - 6;      // flip above
    panel.style.top = Math.round(
      Math.max(vTop + 8, Math.min(top, vTop + vH - 8 - ph))) + "px";
  };

  const onView = () => {                                    // one frame per burst
    if (!dd.backdrop || dd.raf) return;
    dd.raf = requestAnimationFrame(() => { dd.raf = 0; place(); });
  };

  dd.backdrop = backdrop;
  dd.panel = panel;
  dd.anchor = anchor;
  dd.onView = onView;
  document.addEventListener("keydown", dd.onKey, true);
  window.addEventListener("scroll", onView, true);
  window.addEventListener("resize", onView);
  const vv = window.visualViewport;
  if (vv) {
    vv.addEventListener("resize", onView);
    vv.addEventListener("scroll", onView);
  }
  place();                                    // no unpositioned first frame
  requestAnimationFrame(() => {
    if (dd.backdrop !== backdrop) return;      // closed again already
    panel.classList.add("in");
    // scroll the selected row into view WITHOUT touching ancestor scrollers
    // (scrollIntoView would scroll the page behind and re-place the panel)
    const cur = list.querySelector(".dd-item.on");
    if (cur) {
      list.scrollTop = cur.offsetTop - (list.clientHeight - cur.offsetHeight) / 2;
    }
    if (search && matchMedia("(pointer: fine)").matches) {
      search.focus({ preventScroll: true });
    }
  });
}

function ddOptions(sel) {
  const out = [];
  for (const o of sel.options) {
    out.push({
      value: o.value,
      label: o.textContent,
      group: o.parentNode.tagName === "OPTGROUP" ? o.parentNode.label : "",
    });
  }
  return out;
}

/** Replace a <select>'s popup with the themed panel. The select stays in the
    DOM (hidden) — .value and change events keep working as before. Call
    sel._ddSync() after setting sel.value programmatically. */
function ddWrapSelect(sel) {
  sel.classList.add("dd-host");
  const btn = el("button", "dd-btn");
  btn.type = "button";
  btn.setAttribute("aria-haspopup", "listbox");
  sel.parentNode.insertBefore(btn, sel);
  const sync = () => {
    const o = sel.options[sel.selectedIndex];
    btn.textContent = o ? o.textContent : "";
  };
  btn.addEventListener("click", () => {
    ddOpen(btn, ddOptions(sel), sel.value, (v) => {
      sel.value = v;
      sync();
      sel.dispatchEvent(new Event("change", { bubbles: true }));
    });
  });
  sel._ddSync = sync;
  sync();
}

/** Replace a free-text+<datalist> field with a real dropdown. The input
    stays (hidden) as the source of truth; getItems() supplies the choices
    at open time, labelFor() maps a value to its display text. */
function ddWrapInput(inp, getItems, labelFor) {
  inp.classList.add("dd-host");
  const btn = el("button", "dd-btn");
  btn.type = "button";
  btn.setAttribute("aria-haspopup", "listbox");
  inp.parentNode.insertBefore(btn, inp);
  const sync = () => {
    const v = inp.value;
    btn.textContent = v ? (labelFor ? labelFor(v) : v)
      : (inp.placeholder || "");
    btn.classList.toggle("dd-empty", !v);
  };
  btn.addEventListener("click", () => {
    const items = (getItems() || []).slice();
    if (!items.some((i) => i.value === "") && inp.placeholder
        && !items.some((i) => i.label === inp.placeholder)) {
      items.unshift({ value: "", label: inp.placeholder, dim: true });
    }
    if (inp.value && !items.some((i) => i.value === inp.value)) {
      items.push({ value: inp.value,
        label: labelFor ? labelFor(inp.value) : inp.value });
    }
    ddOpen(btn, items, inp.value, (v) => {
      inp.value = v;
      sync();
      inp.dispatchEvent(new Event("change", { bubbles: true }));
    });
  });
  inp._ddSync = sync;
  sync();
}

/* dropdown option sources — read live at open time from the server
   capability lists (sdCaps is refreshed by /api/status) */
const ddProviders = {
  samplers: () => (sdCaps.samplers || []).map((v) => ({ value: v, label: v })),
  scheds: () => (sdCaps.schedulers || []).map((n) => (
    { value: n, label: schedLabel(n) })),
  models: () => regenModels.map((v) => ({ value: v, label: v })),
  te: () => (sdCaps.text_encoders || []).map((v) => ({ value: v, label: v })),
  vae: () => ["Automatic"]
    .concat((sdCaps.vaes || []).filter((v) => v !== "Automatic"))
    .map((v) => ({ value: v, label: v })),
  lb: () => (sdCaps.low_bits || []).map((v) => ({ value: v, label: v })),
};

function ddInit() {
  ddWrapSelect(els.chModel);
  ddWrapSelect(els.chSize);
  ddWrapSelect(els.shModels);
  ddWrapSelect(regenF.mode);
  ddWrapInput(regenF.sampler, ddProviders.samplers);
  ddWrapInput(regenF.sched, ddProviders.scheds, schedLabel);
  ddWrapInput(regenF.model, ddProviders.models);
  ddWrapInput(regenF.te, ddProviders.te);
  ddWrapInput(regenF.vae, ddProviders.vae);
  ddWrapInput(regenF.lb, ddProviders.lb);
}

/* settings — full page with Chatterbox-style sub-tabs */

/** Second-level tabs inside Settings -> Generation. Separate from setPsTab
 *  because they address [data-gpane] divs within one pane, not whole
 *  sections — and they restore their own scroll position independently. */
function setGenTab(name) {
  for (const b of document.querySelectorAll("#ps-gen-tabs button")) {
    b.classList.toggle("on", b.dataset.gtab === name);
  }
  for (const s of document.querySelectorAll("[data-gpane]")) {
    s.hidden = s.dataset.gpane !== name;
  }
  // Each pane fetches its own content on first open: the model picker needs
  // its catalogue and the LoRA list is the pane. The image pane needs
  // nothing — paintImageTab is repainted by the status poll.
  if (name === "text") loadLlmModels(false);
  if (name === "lora") loadLoras();
}

function setPsTab(name) {
  for (const b of document.querySelectorAll("#ps-tabs button")) {
    b.classList.toggle("on", b.dataset.tab === name);
  }
  for (const s of document.querySelectorAll("#page-settings section[data-pane]")) {
    s.hidden = s.dataset.pane !== name;
  }
  document.querySelector(".ps-body").scrollTop = 0;
  if (name === "admin") loadAdmin();
  // Re-enter Generation on the sub-tab the user was last on. Snapping back
  // to Text every time reads as if their selection had been lost.
  if (name === "gen") {
    const on = document.querySelector("#ps-gen-tabs button.on");
    setGenTab(on ? on.dataset.gtab : "text");
  }
}

/* ---------------------------------------------------------------- admin
 *
 * Account management and the audit trail. The tab is unhidden at runtime
 * only when /api/auth says this user is an admin — hiding it in markup
 * would leak that the page exists, and it is not a security boundary
 * anyway: every call here is 403 without the role.
 */

function initAdmin() {
  const tab = document.getElementById("ps-admin-tab");
  if (tab) tab.hidden = !(me && me.admin);
}

function ago(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 90) return "just now";
  if (s < 3600) return Math.round(s / 60) + "m ago";
  if (s < 86400) return Math.round(s / 3600) + "h ago";
  return Math.round(s / 86400) + "d ago";
}

async function loadAdmin() {
  const box = $("ad-users"), audit = $("ad-audit");
  if (box) box.innerHTML = '<div class="hint">loading…</div>';
  try {
    const d = await api("/api/admin/users");
    renderAdminUsers(d.users || [], d.me);
  } catch (e) {
    if (box) box.innerHTML = "";
    if (box) box.appendChild(el("div", "hint", e.message || "could not load"));
  }
  loadAudit();
}

function renderAdminUsers(users, meId) {
  const box = $("ad-users");
  box.innerHTML = "";
  for (const u of users) {
    const row = el("div", "ad-user" + (u.disabled ? " off" : ""));
    const top = el("div", "ad-top");
    top.appendChild(el("b", "", null,
                       [u.name, el("span", "", u.id)]));
    if (u.admin) top.appendChild(el("span", "ad-badge admin", "admin"));
    if (u.disabled) top.appendChild(el("span", "ad-badge off", "disabled"));
    row.appendChild(top);

    const meta = el("div", "ad-meta");
    const bits = [
      u.has_key ? ("key " + (u.key_masked || "set")) : "no OpenRouter key",
      u.sd_url || "no image server",
      u.chats + " chat" + (u.chats === 1 ? "" : "s"),
      u.images + " image" + (u.images === 1 ? "" : "s"),
      "added " + ago(u.created),
    ];
    for (const b of bits) {
      const span = el("span", "", b);
      // The two things that stop an account working, called out rather than
      // left for the admin to infer from a grey row.
      if (!u.has_key) span.className = "warn";
      meta.appendChild(span);
    }
    row.appendChild(meta);

    const acts = el("div", "ad-acts");
    const mk = (label, cls, fn) => {
      const b = el("button", cls || "", label);
      b.addEventListener("click", fn);
      return b;
    };
    acts.appendChild(mk("Reset password", "", () => adminPasswd(u)));
    acts.appendChild(mk(u.disabled ? "Enable" : "Disable", "",
                        () => adminToggle(u)));
    if (u.id !== meId) {
      acts.appendChild(mk("Delete", "del", () => adminDelete(u)));
    } else {
      const b = el("button", "", "You");
      b.disabled = true;
      acts.appendChild(b);
    }
    row.appendChild(acts);
    box.appendChild(row);
  }
}

async function adminCall(body, what) {
  try {
    const d = await api("/api/admin/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    renderAdminUsers(d.users || [], d.me);
    loadAudit();
    toast(what + " ok");
  } catch (e) {
    toast(e.message || what + " failed", true);
    // A refusal often means the server state moved (e.g. it just removed the
    // last admin), so re-read rather than leaving a stale list on screen.
    loadAdmin();
  }
}

function adminPasswd(u) {
  const pw = prompt(`New password for ${u.name}:`);
  if (!pw) return;
  adminCall({ action: "passwd", id: u.id, password: pw },
            "password change");
}

function adminToggle(u) {
  const act = u.disabled ? "enable" : "disable";
  if (act === "disable" && !confirm(
      `Disable ${u.name}?\n\nThey are signed out immediately and cannot ` +
      `sign back in until re-enabled. Their data is kept.`)) return;
  adminCall({ action: act, id: u.id }, act);
}

function adminDelete(u) {
  if (!confirm(
      `Delete ${u.name}?\n\nThis removes their account, chats, personas ` +
      `and every image they generated. Character cards they own stay, since ` +
      `others may share them. This cannot be undone.`)) return;
  adminCall({ action: "delete", id: u.id }, "delete");
}

function wireAdmin() {
  const mk = $("ad-create");
  if (mk) mk.addEventListener("click", adminCreate);
  const nm = $("ad-name"), pw = $("ad-pass");
  for (const f of [nm, pw, $("ad-key"), $("ad-url")]) {
    if (!f) continue;
    if (!f) continue;
    f.addEventListener("keydown", (e) => {
      if (e.key === "Enter") adminCreate();
    });
  }
  const only = $("ad-audit-adminonly");
  if (only) only.addEventListener("change", loadAudit);
}

function adminCreate() {
  const name = ($("ad-name").value || "").trim();
  const pw = $("ad-pass").value || "";
  if (!name || !pw) { toast("name and password required", true); return; }
  // Both optional: blank means "leave the template's default", which is a
  // working account with no key that the user fills in themselves.
  const body = { action: "create", name, password: pw,
                 role: $("ad-admin").checked ? "admin" : "user" };
  const key = ($("ad-key").value || "").trim();
  const url = ($("ad-url").value || "").trim();
  if (key) body.api_key = key;
  if (url) body.sd_url = url;
  adminCall(body, "create").then(() => {
    for (const id of ["ad-name", "ad-pass", "ad-key", "ad-url"]) {
      const f = $(id); if (f) f.value = "";
    }
    $("ad-admin").checked = false;
  });
}

async function loadAudit() {
  const box = $("ad-audit"), count = $("ad-audit-count");
  const only = $("ad-audit-adminonly");
  if (!box) return;
  try {
    const q = only && only.checked ? "&admin_only=1" : "";
    const d = await api("/api/audit?limit=60" + q);
    const evs = d.events || [];
    if (count) count.textContent = evs.length ? "· " + evs.length : "";
    box.innerHTML = "";
    if (!evs.length) {
      box.appendChild(el("div", "hint", "nothing recorded yet"));
      return;
    }
    for (const e of evs) box.appendChild(el("div", "ad-ev" + (e.admin ? " priv" : ""),
      null, [
        el("div", "ad-when", ago(e.ts) + " · " + (e.who || e.actor)),
        el("div", "ad-what", e.action + (e.target ? " → " + e.target : "")),
        e.detail ? el("div", "ad-det", e.detail) : null,
      ]));
  } catch (e) {
    box.innerHTML = "";
    box.appendChild(el("div", "hint", e.message || "could not load"));
  }
}

/* ------------------------------------- LLM chain (OpenRouter model picker) */

/** OpenRouter's catalogue: [{id, name, ctx, tools, in, out}], tools-capable
 *  first (the agent drives images through function calling). */
let llmModels = [];
let llmModelsAsked = false;

async function loadLlmModels(force) {
  if (!force && llmModelsAsked && llmModels.length) return;
  llmModelsAsked = true;
  els.shLlmState.textContent = llmModels.length ? "· refreshing…" : "· loading…";
  try {
    const d = await api("/api/llm/models" + (force ? "?refresh=1" : ""));
    llmModels = d.models || [];
    els.shLlmState.textContent = llmModels.length
      ? `· ${llmModels.length} models` : "· list unavailable";
    renderLlmChain();          // names/prices only resolve once we have the list
    if (d.error) toast(d.error, true);
  } catch (e) {
    if (e.message === "locked") return;
    els.shLlmState.textContent = llmModels.length
      ? `· ${llmModels.length} models (cached)` : "· list unavailable";
    toast("Could not load the model list: " + e.message, true);
  }
}

function llmModelById(id) {
  return llmModels.find((m) => m.id === id) || null;
}

/** "USD per million tokens", which is how OpenRouter itself quotes. */
function llmPrice(m) {
  const one = (n) => `$${(n >= 0.1 ? n.toFixed(2) : n.toFixed(3))}/M`;
  const a = m.in ? one(m.in) : "free";
  return m.out ? `${a} in · ${one(m.out)} out` : a;
}

function llmCtx(n) {
  if (!n) return "";
  return n >= 1e6 ? `${+(n / 1e6).toFixed(1)}M ctx`
    : `${Math.round(n / 1000)}k ctx`;
}

/** One dropdown row: name · id · price · context · tool-calling warning. */
function llmLabel(m) {
  const bits = [m.name];
  if (m.id !== m.name) bits.push(m.id);
  if (m.in || m.out) bits.push(llmPrice(m));
  if (m.ctx) bits.push(llmCtx(m.ctx));
  if (!m.tools) bits.push("no tools");
  return bits.join(" · ");
}

function llmChain() {
  return els.shLlm.value.split(",").map((s) => s.trim()).filter(Boolean);
}

function setLlmChain(list) {
  els.shLlm.value = list.join(", ");
  renderLlmChain();
}

function addLlmModel(id) {
  const chain = llmChain();
  if (!id || chain.includes(id)) return;
  chain.push(id);
  setLlmChain(chain);
}

function renderLlmChain() {
  const box = els.llmChain;
  const chain = llmChain();
  box.textContent = "";
  if (!chain.length) {
    box.appendChild(el("div", "hint", "No models yet — the chain needs at "
      + "least one, and it has to be a model id like vendor/model."));
    return;
  }
  chain.forEach((id, i) => {
    const m = llmModelById(id);
    const row = el("div", "llm-row");
    const main = el("div", "llm-row-main");
    // the chain is stored as ids, so an id the catalogue does not know is
    // legal (a private or brand-new model) — just not annotated
    main.appendChild(el("b", null, m ? m.name : id));
    main.appendChild(el("span", "mono dim llm-id", m ? m.id
      : (llmModels.length ? "not in the OpenRouter list" : "checking…")));
    if (m) {
      main.appendChild(el("span", "dim", llmPrice(m) + (m.ctx ? " · " + llmCtx(m.ctx) : "")));
      if (!m.tools) main.appendChild(el("span", "dim llm-warn", "· no tools"));
    }
    row.appendChild(main);
    const btns = el("div", "llm-row-btns");
    const mk = (glyph, title, fn) => {
      const b = el("button", "iconbtn", glyph);
      b.type = "button";
      b.title = title;
      b.setAttribute("aria-label", title);
      if (!fn) b.disabled = true;
      else b.addEventListener("click", fn);
      btns.appendChild(b);
      return b;
    };
    mk("↑", "move up", i > 0 ? (() => {
      const c = llmChain();
      [c[i - 1], c[i]] = [c[i], c[i - 1]];
      setLlmChain(c);
    }) : null);
    mk("↓", "move down", i < chain.length - 1 ? (() => {
      const c = llmChain();
      [c[i + 1], c[i]] = [c[i], c[i + 1]];
      setLlmChain(c);
    }) : null);
    mk("✕", "remove from the chain", () => {
      setLlmChain(llmChain().filter((x) => x !== id));
    });
    row.appendChild(btns);
    box.appendChild(row);
  });
}

function openLlmPicker() {
  if (!llmModels.length) {
    loadLlmModels(false);
    toast("Fetching the OpenRouter list…");
    return;
  }
  const chain = llmChain();
  const items = llmModels.map((m) => ({
    value: m.id,
    label: llmLabel(m) + (chain.includes(m.id) ? "  ✓" : ""),
    dim: chain.includes(m.id),
  }));
  ddOpen(els.shLlmPick, items, "", (id) => addLlmModel(id));
}

/** The image-server URL the server is actually using — kept so a refused save
 *  can put the working address back in the box. */
let lastSdUrl = "";

async function openSettings() {
  els.pageSettings.hidden = false;
  setPsTab("persona");
  loadPersonas();
  try {
    // refresh=1 → server asks SD to rescan its models dir, fresh list
    const s = await api("/api/status?refresh=1");
    els.shLlm.value = (s.llm || []).join(", ");
    renderLlmChain();
    paintImageTab(s);
    onSdSettled(paintImageTab);
    // masked key display (GET /api/settings)
    try {
      const cfg = await api("/api/settings");
      els.shKeymask.textContent = cfg.key_masked ? `· ${cfg.key_masked}` : "· not set";
      els.shSd.value = cfg.sd_url || "";
      lastSdUrl = els.shSd.value;
      if (!els.shLlm.value) {
        els.shLlm.value = (cfg.llm || []).join(", ");
        renderLlmChain();
      }
      els.shSys.value = cfg.system_prompt || "";
      els.shSysState.textContent = cfg.system_prompt_custom ? "· custom" : "· default";
      els.shGuide.value = cfg.model_guide || "";
      els.shGuideState.textContent = cfg.model_guide_custom ? "· custom" : "· model_guide.txt";
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

/** Paint Settings → Image from a /api/status snapshot. Split out of
 *  openSettings because the server now resolves the image server off-thread:
 *  the first status after a URL change says "connecting", and this has to be
 *  repaintable when the real answer lands. */
function paintImageTab(s) {
  sdCaps = s.capabilities || {};
  els.shSdok.textContent = s.sd_ok ? "· connected"
    : (s.sd_pending ? "· connecting…" : "· unreachable");
  els.shSdok.title = s.sd_error || "";
  els.shCur.textContent = s.current_model ? `· now: ${s.current_model}` : "";
  els.shArch.textContent = s.current_arch ? `· loaded: ${s.current_arch}` : "";
  els.shModelhint.textContent = s.sd_ok
    ? "Loading a checkpoint takes 10-30 s."
    : (s.sd_error || (s.sd_pending ? "Contacting the image server…"
      : "No image server answering."));
  els.shModels.textContent = "";
  if (s.models.length) {
    // group by detected architecture — the bytes decide the family (a
    // Nova "AM" file is an Anima model even though the name hides it), and
    // seeing the groups makes a wrong guess obvious
    const detail = s.model_detail || [];
    const groups = new Map();
    const seen = new Set();
    for (const m of detail) {
      if (!groups.has(m.arch)) groups.set(m.arch, []);
      groups.get(m.arch).push(m.title);
      seen.add(m.title);
    }
    const other = s.models.filter((t) => !seen.has(t));
    const label = (a) => (((sdCaps.architectures || [])
      .find((r) => r.arch === a) || {}).label) || a;
    const addOpt = (parent, t) => {
      const o = el("option", null, t);
      o.value = t;
      if (t === s.current_model
          || (s.current_model && t.startsWith(s.current_model))) {
        o.selected = true;
      }
      parent.appendChild(o);
    };
    if (other.length) {
      const g = document.createElement("optgroup");
      g.label = "Other";
      for (const t of other) addOpt(g, t);
      els.shModels.appendChild(g);
    }
    for (const [arch, titles] of groups) {
      const g = document.createElement("optgroup");
      g.label = label(arch);
      for (const t of titles) addOpt(g, t);
      els.shModels.appendChild(g);
    }
    els.shLoad.disabled = false;
  } else {
    els.shModels.appendChild(el("option", null,
      s.sd_pending ? "(connecting…)"
        : (s.sd_ok ? "(no models)" : "(server unreachable)")));
    els.shLoad.disabled = true;
  }
  if (els.shModels._ddSync) els.shModels._ddSync();
  // the companion card is built from the same capability lists, and those land
  // with the checkpoint list — without this it kept claiming "no modular
  // architectures" until the Settings page was reopened
  renderComps(s.current_arch || compArch);
}

/** Re-run `fn(status)` once the server's background image-server probe settles.
 *
 * The server answers /api/status from a cache and resolves the WebUI in a
 * worker thread, so a status can legitimately arrive while the answer is
 * still "connecting" — right after a server switch, or on a cold app start.
 * Anything that renders checkpoint lists registers here instead of showing an
 * empty picker and hoping the user comes back later.
 */
let sdWaiters = [];
let sdPumpTimer = null;

function onSdSettled(fn) {
  sdWaiters.push(fn);
  pumpSdSettled();
}

function pumpSdSettled() {
  if (sdPumpTimer) return;
  sdPumpTimer = setTimeout(async () => {
    sdPumpTimer = null;
    const s = await refreshStatus();
    if (!s) return;                           // locked or offline — drop it
    if (s.sd_pending) { pumpSdSettled(); return; }
    const waiters = sdWaiters;
    sdWaiters = [];
    for (const fn of waiters) {
      try { fn(s); } catch (e) { console.warn(e); }
    }
  }, 1200);
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

// ------------------------------------------------------------------- LoRAs

// A collection, not a document. Fifty of these is the expected size, so every
// row is independent, searchable and collapsible — the model_guide textarea
// shape does not survive the count, and its 64k cap would become a wall.
let loraRows = [];
let loraArchNow = "";
let chLoraSel = [];      // names ticked on the card being edited, in order
let chLoraDirty = false;

const ARCH_LABEL = {
  xl: "SDXL", sd: "SD 1.5", sd3: "SD3", flux: "Flux", qwen: "Qwen",
  anima: "Anima", krea: "Krea", wan: "Wan", zimage: "Z-Image",
};

function loraArchLabel(a) { return ARCH_LABEL[a] || a || "unknown"; }

async function loadLoras() {
  try {
    const d = await api("/api/loras");
    loraRows = d.loras || [];
    loraArchNow = d.current_arch || "";
    renderLoraArchFilter();
    renderLoras();
    renderChLoras();
  } catch (e) {
    if (els.shLoraList) els.shLoraList.textContent = "";
    if (els.shLoraList) els.shLoraList.appendChild(el("div", "hint", String(e)));
  }
}

function renderLoraArchFilter() {
  const sel = els.shLoraArch;
  if (!sel) return;
  const have = new Set(loraRows.map((r) => r.arch).filter(Boolean));
  const want = JSON.stringify([...have].sort()) + "|" + sel.value;
  if (sel.dataset.sig === want) return;
  sel.dataset.sig = want;
  const prev = sel.value;
  sel.textContent = "";
  const all = el("option", null, "All architectures");
  all.value = "";
  sel.appendChild(all);
  for (const a of [...have].sort()) {
    const o = el("option", null, loraArchLabel(a));
    o.value = a;
    sel.appendChild(o);
  }
  sel.value = have.has(prev) ? prev : "";
}

function loraFiltered() {
  const q = (els.shLoraQ ? els.shLoraQ.value : "").trim().toLowerCase();
  const arch = els.shLoraArch ? els.shLoraArch.value : "";
  const note = els.shLoraNote ? els.shLoraNote.value : "";
  const use = els.shLoraUse ? els.shLoraUse.value : "";
  const worn = wornLoraNames();
  return loraRows.filter((r) => {
    if (arch && r.arch !== arch) return false;
    // "has notes" means something the user (or the CivitaiAI seed) actually
    // wrote — an untouched record has an empty trigger and empty notes, and
    // filtering on `described` alone would count those as annotated.
    const annotated = !!(r.trigger || r.notes || r.example);
    if (note === "yes" && !annotated) return false;
    if (note === "no" && annotated) return false;
    const isWorn = worn.has(r.name);
    if (use === "yes" && !isWorn) return false;
    if (use === "no" && isWorn) return false;
    if (!q) return true;
    return [r.name, r.trigger, r.notes, r.alias]
      .some((v) => String(v || "").toLowerCase().includes(q));
  });
}

// Which LoRAs any character card currently references. Read straight off the
// card list rather than a cached module mirror, so it cannot go stale the way
// a "currently selected card" flag does.
function wornLoraNames() {
  const out = new Set();
  for (const c of (chars || [])) {
    for (const n of (c.loras || [])) out.add(n);
  }
  return out;
}

function loraStatus(r) {
  const bits = [];
  bits.push(r.installed ? loraArchLabel(r.arch) : "not installed");
  if (r.installed && r.file_arch && r.arch && r.file_arch !== r.arch) {
    bits.push("file says " + loraArchLabel(r.file_arch));
  }
  if (r.rank) bits.push("rank " + r.rank);
  if (!r.described) bits.push("no notes yet");
  return bits.join(" · ");
}

// What Forge has to be given, and what actually sits in models/Lora. Both, in
// full: the reference string is what goes in a card, and a LoRA whose own
// trainer wrote an ss_output_name resolves under BOTH, so showing one without
// the other leaves you guessing which one to copy.
function loraFileName(r) {
  const base = String(r.name || "");
  if (!base) return "";
  return /\.(safetensors|ckpt|pt)$/i.test(base) ? base : base + ".safetensors";
}

function loraRow(r) {
  const row = el("div", "lora-row" + (r.mismatch ? " bad" : ""));
  const head = el("div", "lora-head");

  const caret = el("button", "lora-caret", "▸");
  caret.setAttribute("aria-label", "Expand");
  head.appendChild(caret);

  const names = el("div", "lora-names");
  names.appendChild(el("span", "lora-name", loraFileName(r)));
  if (r.alias && r.alias !== r.name) {
    names.appendChild(el("span", "dim lora-alias", "goes by “" + r.alias + "”"));
  }
  if (!r.installed) {
    names.appendChild(el("span", "dim",
      "no such file in the server's models/Lora folder — write notes now, "
      + "attach it later"));
  }
  head.appendChild(names);

  const del = el("button", "lora-del", "✕");
  del.setAttribute("aria-label", "Forget this LoRA");
  del.title = r.installed
    ? "Delete the notes for this file (the file stays)"
    : "Forget this — no file is left on the server";
  head.appendChild(del);

  const body = el("div", "lora-body");
  body.hidden = true;

  const f = (label, key, ph, area) => {
    const wrap = el("label", null, label);
    const inp = area ? el("textarea", "mono") : el("input", "mono");
    if (!area) inp.type = "text";
    inp.value = r[key] || "";
    inp.placeholder = ph || "";
    inp.spellcheck = false;
    wrap.appendChild(inp);
    body.appendChild(wrap);
    return inp;
  };
  const weight = f("Weight", "weight", "0.9");
  weight.setAttribute("inputmode", "decimal");
  weight.value = r.weight ?? 1;
  const trigger = f("Trigger word", "trigger", "mitia");
  const arch = f("Architecture", "arch", "xl — blank = guess from the file");
  const notes = f("Notes for the AI", "notes",
    "light blue messy hair, purple eyes, gold atom brooch", true);
  const example = f("Example prompt", "example",
    "masterpiece, best quality, mitia, 1boy, solo", true);

  const save = el("button", "primary", "Save");
  const gather = () => ({
    weight: Math.min(1.5, Math.max(0, parseFloat(weight.value) || 0)),
    trigger: trigger.value.trim(),
    arch: arch.value.trim().toLowerCase(),
    notes: notes.value.trim(),
    example: example.value.trim(),
    enabled: true,
  });
  const statusRow = el("div", "lora-statusrow");
  statusRow.appendChild(el("span", "dim lora-status", loraStatus(r)));
  statusRow.appendChild(save);
  body.appendChild(statusRow);

  save.addEventListener("click", async () => {
    save.disabled = true;
    try {
      const d = await api("/api/loras", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "save", name: r.name, record: gather() }),
      });
      loraRows = d.loras || loraRows;
      loraArchNow = d.current_arch || loraArchNow;
      renderLoraArchFilter(); renderLoras(); renderChLoras();
      toast("Saved");
    } catch (e) { toast(String(e), true); }
    save.disabled = false;
  });

  del.addEventListener("click", async () => {
    if (!confirm("Forget “" + r.name + "”?\n\nIts notes and weight are "
      + "deleted, and any card wearing it stops wearing it. The file on disk "
      + "is left alone.")) return;
    try {
      const d = await api("/api/loras", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "delete", name: r.name }),
      });
      loraRows = d.loras || [];
      chLoraSel = chLoraSel.filter((n) => n !== r.name);
      renderLoraArchFilter(); renderLoras(); renderChLoras();
      toast("Forgot " + r.name);
    } catch (e) { toast(String(e), true); }
  });

  caret.addEventListener("click", () => {
    body.hidden = !body.hidden;
    caret.textContent = body.hidden ? "▸" : "▾";
  });

  row.appendChild(head);
  row.appendChild(body);
  return row;
}

function renderLoras() {
  const box = els.shLoraList;
  if (!box) return;
  box.textContent = "";
  const rows = loraFiltered();
  const inst = loraRows.filter((r) => r.installed).length;
  if (els.shLoraCount) {
    els.shLoraCount.textContent = loraRows.length
      ? "— " + inst + " installed, " + loraRows.length + " known"
      : "";
  }
  if (!loraRows.length) {
    box.appendChild(el("div", "hint",
      "No LoRAs on the server. Drop .safetensors files into its models/Lora "
      + "folder and press ↻ — they are picked up from there, nothing to "
      + "import."));
    return;
  }
  if (!rows.length) {
    box.appendChild(el("div", "hint", "Nothing matches that search."));
    return;
  }
  for (const r of rows) box.appendChild(loraRow(r));
}

// -------------------------------------------------------- CivitAI browsing
//
// Kept deliberately separate from the list above: those LoRAs are on disk,
// these are somewhere else entirely. Collapsed by default so the installed
// set stays the thing you look at.

let dlItems = [];
let dlCursor = "";
let dlBusy = false;
let dlQuery = "";

async function searchLoras(reset) {
  if (dlBusy) return;
  dlBusy = true;
  els.shDlGo.disabled = true;
  els.shDlState.textContent = "searching…";
  const q = els.shDlQ.value.trim();
  const tag = els.shDlTag.value.trim();
  if (reset) { dlQuery = q + " " + tag; dlItems = []; dlCursor = ""; }
  try {
    const p = new URLSearchParams({
      q, sort: els.shDlSort.value, period: els.shDlPeriod.value, tag,
      cursor: reset ? "" : dlCursor, limit: "24",
    });
    const d = await api("/api/loras/search?" + p);
    dlItems = reset ? d.items : dlItems.concat(d.items || []);
    dlCursor = d.next_cursor || "";
    renderDlArchFilter();
    renderDl();
    if (d.cached) {
      els.shDlState.textContent = "— cached" + (d.age_s ? ", " + d.age_s + "s old" : "");
    } else {
      els.shDlState.textContent = "";
    }
  } catch (e) {
    els.shDlState.textContent = "";
    if (reset) {
      dlItems = [];
      els.shDlList.textContent = "";
      els.shDlList.appendChild(el("div", "hint", "Search failed: " + e));
    } else {
      toast("Could not load more", true);
    }
  }
  dlBusy = false;
  els.shDlGo.disabled = false;
  els.shDlMore.disabled = false;
}

function renderDlArchFilter() {
  const sel = els.shDlArch;
  if (!sel) return;
  const have = new Set(dlItems.map((r) => r.arch).filter(Boolean));
  const want = [...have].sort().join(",") + "|" + sel.value;
  if (sel.dataset.sig === want) return;
  sel.dataset.sig = want;
  const prev = sel.value;
  sel.textContent = "";
  const all = el("option", null, "Any architecture");
  all.value = "";
  sel.appendChild(all);
  for (const a of [...have].sort()) {
    const o = el("option", null, loraArchLabel(a));
    o.value = a;
    sel.appendChild(o);
  }
  sel.value = have.has(prev) ? prev : "";
}

// Search-result filters that Civitai has no parameter for. Its `baseModel=`
// is silently ignored (asked for Illustrious, got SD 1.5 back), so filtering
// architecture here — off the baseModels it actually returned — is the only
// version of this filter that tells the truth.
function dlFiltered() {
  const arch = els.shDlArch ? els.shDlArch.value : "";
  const sfw = els.shDlSfw && els.shDlSfw.checked;
  return dlItems.filter((it) => {
    if (arch && it.arch !== arch) return false;
    if (sfw && it.nsfw) return false;
    return true;
  });
}

function renderDl() {
  const box = els.shDlList;
  box.textContent = "";
  const rows = dlFiltered();
  if (!dlItems.length) {
    box.appendChild(el("div", "hint",
      "Nothing found. Try a character name, a style, or a concept."));
    els.shDlMore.hidden = true;
    return;
  }
  if (!rows.length) {
    box.appendChild(el("div", "hint",
      "Nothing matches those filters — " + dlItems.length + " result(s) hidden."));
    els.shDlMore.hidden = !dlCursor;
    return;
  }
  for (const it of rows) box.appendChild(dlRow(it));
  els.shDlMore.hidden = !dlCursor;
}

function dlRow(it) {
  const row = el("div", "dl-row" + (it.installed ? " have" : ""));
  if (it.images && it.images.length) {
    const img = el("img", "dl-thumb");
    img.src = it.images[0].url;
    img.alt = "";
    img.loading = "lazy";
    row.appendChild(img);
  }
  const main = el("div", "dl-main");
  main.appendChild(el("span", "dl-name", it.file || it.name));

  const bits = [];
  if (it.civitai_base) bits.push(it.civitai_base);
  bits.push(it.size_mb + " MB");
  if (it.downloads) bits.push(it.downloads.toLocaleString() + " downloads");
  if (it.version_name && it.version_name !== "Main") {
    bits.push(it.version_name);
  }
  main.appendChild(el("span", "dim", bits.join(" · ")));
  if (it.trained && it.trained.length) {
    main.appendChild(el("span", "dl-trig", "trigger: " + it.trained[0]));
  }
  if (it.nsfw) main.appendChild(el("span", "warn", "contains NSFW"));
  row.appendChild(main);

  const btn = el("button", null, it.installed ? "Installed" : "Details");
  btn.addEventListener("click", () => openLoraModal(it));
  row.appendChild(btn);
  return row;
}

// ------------------------------------------------------------ detail popup

let modalBusy = false;

function closeLoraModal() {
  els.loraModal.hidden = true;
  els.loraModalCard.textContent = "";
}

function openLoraModal(it) {
  const card = els.loraModalCard;
  card.textContent = "";

  const head = el("div", "lm-head");
  const h = el("div", "lm-title");
  h.appendChild(el("div", "lm-name", it.model_name || it.file || it.name));
  const sub = [it.file, it.civitai_base, it.size_mb + " MB"].filter(Boolean);
  h.appendChild(el("div", "dim", sub.join(" · ")));
  head.appendChild(h);
  const x = el("button", "lm-x", "✕");
  x.setAttribute("aria-label", "Close");
  x.addEventListener("click", closeLoraModal);
  head.appendChild(x);
  card.appendChild(head);

  const body = el("div", "lm-body");

  const stat = [];
  if (it.downloads) stat.push(it.downloads.toLocaleString() + " downloads");
  if (it.rating) stat.push(it.rating.toLocaleString() + " likes");
  if (it.creator) stat.push("by " + it.creator);
  if (it.version_name && it.version_name !== "Main") {
    stat.push("version " + it.version_name);
  }
  if (stat.length) body.appendChild(el("div", "lm-stats", stat.join(" · ")));

  // The whole point of the popup: a filename says nothing about what a LoRA
  // actually does, and Civitai has already told us in three separate fields.
  if (it.trained && it.trained.length) {
    const tw = el("div", "lm-sec");
    tw.appendChild(el("div", "lm-lab", "Trigger words"));
    const chips = el("div", "lm-chips");
    for (const t of it.trained) chips.appendChild(el("span", "lm-chip", t));
    tw.appendChild(chips);
    body.appendChild(tw);
  }
  if (it.desc) {
    const d = el("div", "lm-sec");
    d.appendChild(el("div", "lm-lab", "About"));
    d.appendChild(el("div", "lm-desc", it.desc));
    body.appendChild(d);
  }
  if (it.images && it.images.length) {
    const g = el("div", "lm-sec");
    g.appendChild(el("div", "lm-lab",
      "Preview" + (it.images.length >= 10 ? " (first 10)" : "")));
    const grid = el("div", "lm-grid");
    for (const im of it.images) {
      const fig = el("figure", "lm-shot");
      const img = el("img");
      img.src = im.url;
      img.alt = "";
      img.loading = "lazy";
      fig.appendChild(img);
      if (im.nsfw) {
        // Blurred rather than hidden. The app has no NSFW policy, but a
        // settings panel is not the place for surprise nudity over someone's
        // shoulder — one tap reveals, and the badge says which it was.
        fig.classList.add("blurred");
        fig.appendChild(el("span", "lm-nsfw", "NSFW"));
        fig.addEventListener("click", () => fig.classList.remove("blurred"));
      }
      grid.appendChild(fig);
    }
    g.appendChild(grid);
    body.appendChild(g);
  }
  card.appendChild(body);

  const foot = el("div", "lm-foot");
  const note = el("div", "dim");
  if (it.installed) {
    note.textContent = "Already installed — attach it from a character card.";
  } else if (it.arch && loraArchNow && it.arch !== loraArchNow) {
    note.classList.add("warn");
    note.textContent = "Built for " + loraArchLabel(it.arch) +
      "; this box loads " + loraArchLabel(loraArchNow) +
      ". It will download, but it will not change the image.";
  } else if (!it.arch) {
    note.textContent = "Base model unknown — you can set it after installing.";
  }
  foot.appendChild(note);

  const go = el("button", "primary", it.installed ? "Installed" : "Download");
  if (it.installed) {
    go.disabled = true;
  } else {
    go.addEventListener("click", async () => {
      if (modalBusy) return;
      modalBusy = true;
      const label = go.textContent;
      go.disabled = true;
      // a single streamed response has no progress events, so this creeps
      // toward 90% and waits rather than faking a number that never lands
      let p = 0;
      const tick = setInterval(() => {
        p = Math.min(90, p + Math.random() * 10);
        go.textContent = Math.round(p) + "%";
      }, 400);
      try {
        const d = await api("/api/loras/search", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ version_id: it.version_id }),
        });
        clearInterval(tick);
        go.textContent = "Installed";
        loraRows = d.loras || loraRows;
        loraArchNow = d.current_arch || loraArchNow;
        it.installed = true;
        renderLoraArchFilter(); renderLoras(); renderChLoras(); renderDl();
        toast((d.installed || {}).name + " installed" +
              (d.seeded ? " — notes filled in from CivitAI" : ""));
        if (d.warning) toast(d.warning, true);
        setTimeout(closeLoraModal, 900);
      } catch (e) {
        clearInterval(tick);
        go.disabled = false;
        go.textContent = label;
        toast("Download failed: " + e, true);
      }
      modalBusy = false;
    });
  }
  foot.appendChild(go);
  card.appendChild(foot);

  els.loraModal.hidden = false;
  card.scrollTop = 0;
}

function renderChLoras() {
  const box = els.chLoraList;
  if (!box) return;
  box.textContent = "";
  if (!loraRows.length) {
    box.appendChild(el("div", "hint",
      "No LoRAs known yet — describe some in Settings → LoRAs."));
  }
  for (const r of loraRows) {
    const on = chLoraSel.includes(r.name);
    const wrap = el("label", "ch-lora" + (on ? " on" : ""));
    const cb = el("input");
    cb.type = "checkbox";
    cb.checked = on;
    cb.disabled = !on && chLoraSel.length >= 3;
    cb.addEventListener("change", () => {
      if (cb.checked) {
        if (chLoraSel.length >= 3) {
          cb.checked = false;
          toast("Three is the limit per character", true);
          return;
        }
        chLoraSel.push(r.name);
      } else {
        chLoraSel = chLoraSel.filter((n) => n !== r.name);
      }
      chLoraDirty = true;
      renderChLoras();
    });
    wrap.appendChild(cb);
    const txt = el("span", "ch-lora-txt");
    txt.appendChild(el("span", "ch-lora-file", loraFileName(r)));
    const bits = [r.installed ? loraArchLabel(r.arch) : "not installed"];
    if (r.weight != null && r.weight !== 1) bits.push("w " + r.weight);
    if (r.trigger) bits.push("“" + r.trigger + "”");
    txt.appendChild(el("span", "dim", bits.join(" · ")));
    if (r.mismatch) txt.appendChild(el("span", "warn", r.mismatch + " — won’t load"));
    wrap.appendChild(txt);
    box.appendChild(wrap);
  }
  if (els.chLoraCount) {
    els.chLoraCount.textContent = chLoraSel.length
      ? "— " + chLoraSel.length + " of 3 selected"
      : "— loaded on every image of this character";
  }
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
  ddWrapSelect(sel);

  const head = el("div", "comp-head");
  const bits = [`CFG ${r.cfg}`, `${r.steps} steps`, `${r.sampler}/${r.scheduler}`];
  if (r.distilled) bits.unshift("low-guidance");
  if (r.video) bits.push("video");
  head.appendChild(el("span", "dim", bits.join(" · ")));
  card.appendChild(head);

  const fields = [
    ["Text encoder", "text_encoder", "server default",
     saved.text_encoder || ""],
    ["VAE", "sd_vae", "Automatic", saved.sd_vae || ""],
    ["Precision", "low_bits", "Automatic",
     saved.low_bits || r.low_bits || ""],
  ];
  for (const [label, field, ph, val] of fields) {
    const wrap = el("label", null, label);
    const inp = el("input");
    inp.type = "text";
    inp.placeholder = ph;
    inp.value = val;
    inp.dataset.arch = r.arch;
    inp.dataset.field = field;
    wrap.appendChild(inp);
    ddWrapInput(inp, field === "text_encoder" ? ddProviders.te
      : field === "sd_vae" ? ddProviders.vae : ddProviders.lb);
    card.appendChild(wrap);
  }

  const cur = [];
  if ((r.modules || []).length) cur.push("attached: " + r.modules.join(", "));
  if (r.checkpoint) cur.push("checkpoint: " + r.checkpoint);
  if (cur.length) card.appendChild(el("div", "hint", cur.join(" · ")));
  box.appendChild(card);
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

async function refreshChars(watched) {
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
    // the checkpoint list arrives empty while the server is still resolving
    // the image server — repaint when it does
    if (s.sd_pending && !watched) onSdSettled(() => refreshChars(true));
  } catch (e) {
    if (e.message !== "locked") toast("Characters failed: " + e.message, true);
  }
  renderCharList();
  renderTagBar();
}

/* list/card toggle — lives in Settings > Appearance, not the list header.
   Persists in localStorage. */
let charGrid = localStorage.getItem("charview") === "grid";

function updateGridBtn() {
  for (const b of document.querySelectorAll("#charseg button")) {
    const on = (b.dataset.charview === "grid") === charGrid;
    b.classList.toggle("on", on);
    b.setAttribute("aria-pressed", on ? "true" : "false");
  }
}

function setCharView(mode) {
  const grid = mode === "grid";
  if (grid === charGrid) return;
  charGrid = grid;
  localStorage.setItem("charview", grid ? "grid" : "rows");
  updateGridBtn();
  renderCharList();
}

/* the tag strip above the list: every distinct tag on any card, tap to filter */
let charTagFilter = "";

function allCharTags() {
  const seen = [];
  for (const c of chars || []) {
    for (const t of (c.tags || [])) {
      if (t && !seen.includes(t)) seen.push(t);
    }
  }
  return seen;
}

function renderTagBar() {
  const bar = els.charTagBar;
  if (!bar) return;
  const tags = allCharTags();
  bar.textContent = "";
  bar.hidden = tags.length === 0;
  if (!tags.length) return;
  // deterministic colours, so a tag keeps the same colour everywhere
  const hue = (s) => {
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360;
    return h;
  };
  for (const t of tags) {
    const b = el("button", "tagchip" + (charTagFilter === t ? " on" : ""), t);
    b.style.setProperty("--h", hue(t));
    b.setAttribute("aria-pressed", charTagFilter === t ? "true" : "false");
    b.addEventListener("click", () => {
      charTagFilter = charTagFilter === t ? "" : t;
      renderTagBar();
      renderCharList();
    });
    bar.appendChild(b);
  }
}

/* cards used to be just a name and a chat count, which is not enough to tell
   two of them apart when scrolling. The greeting and the tags are already on
   the card, so they get shown — that is the whole difference between this and
   the old row. */
function charRows(filter) {
  const q = (filter || "").trim().toLowerCase();
  const rows = [];
  for (const c of chars) {
    if (q) {
      const hay = [c.name, c.greeting, (c.tags || []).join(" ")]
        .map((v) => String(v || "").toLowerCase());
      if (!hay.some((h) => h.includes(q))) continue;
    }
    if (charTagFilter && !(c.tags || []).includes(charTagFilter)) continue;
    rows.push(c);
  }
  return rows;
}

function charChips(c) {
  const wrap = el("span", "char-tags");
  for (const t of (c.tags || []).slice(0, 4)) {
    wrap.appendChild(el("span", "tagchip sm", t));
  }
  return wrap;
}

// roleplay text is full of *asterisks* and newlines; one clean line reads far
// better in a row than the raw greeting did
function charBlurb(c) {
  const g = String(c.greeting || "").trim();
  if (!g) return "";
  return g.replace(/\s+/g, " ").replace(/^\*+|\*+$/g, "").trim();
}

function charCount(c) {
  const n = Number(c.chats) || 0;
  return n + (n === 1 ? " Conversation" : " Conversations");
}

function renderCharList() {
  const rows = charRows(els.charSearch.value);
  els.charlist.classList.toggle("asgrid", charGrid);
  els.charlist.textContent = "";
  els.charempty.hidden = rows.length > 0;
  for (const c of rows) {
    // A div, not a <button>. A card is a multi-element card — cover image,
    // chips, name, greeting, count — and <button> cannot lay that out: Chrome
    // wraps button content in an anonymous centred box, which is what turned
    // grid mode into a wall of letterboxed pictures with no text under them.
    // The long-press and context-menu handlers only ever use
    // .closest(".char-row") and dataset.charid, so this is transparent to
    // them; role/tabindex/keydown keep it reachable by keyboard.
    const row = el("div", "char-row"
      + (c.id === activeCharId ? " active" : ""));
    row.setAttribute("role", "button");
    row.setAttribute("tabindex", "0");
    const pic = c.cover || c.avatar;
    if (pic) {
      const img = document.createElement("img");
      img.className = "char-avatar";
      img.src = pic;
      img.alt = "";
      img.loading = "lazy";
      row.appendChild(img);
    } else {
      row.appendChild(el("span", "char-avatar ch-noavatar",
        (c.name || "?").slice(0, 1).toUpperCase()));
    }
    const tx = el("div", "char-text");
    const top = el("div", "char-top");
    top.appendChild(favStar(c));
    top.appendChild(el("b", "char-name", c.name || c.id));
    // tags share the name's line rather than taking one of their own — a row
    // is four lines tall either way, and spending a line on chips made short
    // cards feel emptier than they are
    if ((c.tags || []).length) top.appendChild(charChips(c));
    tx.appendChild(top);
    // only when there is one. Reserving the line for everyone was the old
    // behaviour: it kept every row the same height, but left a visible dead
    // gap under any character with no greeting written yet. The clamp is what
    // actually stops a row growing, so the reservation is not needed.
    const blurb = charBlurb(c);
    if (blurb) tx.appendChild(el("span", "char-blurb", blurb));
    tx.appendChild(el("span", "dim", charCount(c)));
    row.appendChild(tx);
    row.addEventListener("click", () => selectChar(c.id));
    row.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        selectChar(c.id);
      }
    });
    row.dataset.charid = c.id;
    els.charlist.appendChild(row);
  }
}

/* the pin, a span rather than a button — a row is a button and nesting one
   inside another is invalid markup. stopPropagation keeps a tap on it from
   also opening the chat. */
function favStar(c) {
  const s = el("span", "char-fav" + (c.fav ? " on" : ""), c.fav ? "★" : "☆");
  s.title = c.fav ? "Unfavourite" : "Favourite";
  s.setAttribute("role", "button");
  s.setAttribute("aria-label", s.title);
  s.setAttribute("aria-pressed", c.fav ? "true" : "false");
  const go = (e) => { e.preventDefault(); e.stopPropagation(); toggleFav(c.id); };
  s.addEventListener("click", go);
  s.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") go(e);
  });
  return s;
}

async function toggleFav(id) {
  try {
    await api("/api/character/favourite", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    await refreshChars();
  } catch (e) {
    if (e.message !== "locked") toast("Favourite failed: " + e.message, true);
  }
}

function fillModelSelect() {
  const sel = els.chModel;
  sel.textContent = "";
  const cur = el("option", null, "(current checkpoint)");
  cur.value = "";                      // else the value would be its text
  sel.appendChild(cur);
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
    applyChatFlags(d);
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
  chLoraSel = card && Array.isArray(card.loras) ? card.loras.slice() : [];
  chLoraDirty = false;
  if (!loraRows.length) loadLoras();   // first open on a fresh session
  else renderChLoras();
  aiReset();               // a different card is a different conversation
  pendingAvatar = "";
  pendingCover = "";
  clearAvatar = clearCover = clearScCover = false;
  scenarioCharId = card ? card.id : null;
  els.chTitle.textContent = card ? "Edit character" : "New character";
  els.chName.value = card ? card.name : "";
  els.chAppearance.value = card ? (card.appearance || "") : "";
  els.chTags.value = card ? (card.tags || []).join(", ") : "";
  els.chPersona.value = card ? (card.persona || "") : "";
  els.chGreeting.value = card ? (card.greeting || "") : "";
  els.chSize.value = card && card.size && card.size.length === 2
    ? card.size[0] + "x" + card.size[1] : "";
  els.chTemp.value = card && card.temp != null ? card.temp : "";
  els.chMaxtok.value = card && card.max_tokens != null ? card.max_tokens : "";
  // A card written before sharing existed has no flag and was never private,
  // so default the box to ticked — same rule the server reads.
  els.chShared.checked = card ? (card.shared !== false) : true;
  fillModelSelect();
  els.chModel.value = card ? (card.checkpoint || "") : "";
  if (els.chModel.value === "" && card && card.checkpoint) {
    // not in list — keep it as an option
    const o = el("option", null, card.checkpoint);
    o.value = card.checkpoint;
    els.chModel.appendChild(o);
    els.chModel.value = card.checkpoint;
  }
  if (els.chModel._ddSync) els.chModel._ddSync();
  if (els.chSize._ddSync) els.chSize._ddSync();
  if (card && card.avatar) {
    els.chAvatarImg.src = card.avatar;
    els.chAvatarImg.hidden = false;
  } else {
    els.chAvatarImg.hidden = true;
  }
  els.chAvatarClear.hidden = !(card && card.avatar);
  setPreview(els.chCoverImg, els.chCoverClear,
    (card && card.cover) || "");
  els.chDelete.hidden = !card || !!card.system;
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
    tags: els.chTags.value.split(",").map((s) => s.trim()).filter(Boolean),
    loras: chLoraSel.slice(0, 3),
    shared: !!(els.chShared && els.chShared.checked),
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
  reader.onload = () => avatarFromDataUrl(reader.result);
  reader.readAsDataURL(file);
}

/* center-crop to a square — same path for uploads and gallery picks.
   512, not 256: card view stretches the avatar to the full width of a grid
   cell, which is ~340px on a phone and ~460px on a desktop, so a 256 source
   was being upscaled nearly 2x and went visibly soft. */
function avatarFromDataUrl(dataUrl) {
  const img = new Image();
  img.onload = () => {
    const S = 512;
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
  img.src = dataUrl;
}

/* cover (first image) picker — keep native resolution when it fits, else
   scale the long side to 1216px so the save payload stays reasonable */
function pickCover(file, which) {
  if (!file || !file.type.startsWith("image/")) return;
  const reader = new FileReader();
  reader.onload = () => coverFromDataUrl(reader.result, which);
  reader.readAsDataURL(file);
}

function coverFromDataUrl(dataUrl, which) {
  const apply = (url) => {
    if (which === "sc") {
      pendingScCover = url; clearScCover = false;
      setPreview(els.scsCoverImg, els.scsCoverClear, pendingScCover);
    } else {
      pendingCover = url; clearCover = false;
      setPreview(els.chCoverImg, els.chCoverClear, pendingCover);
    }
  };
  const img = new Image();
  img.onload = () => {
    const long = Math.max(img.width, img.height);
    if (long <= 1216) { apply(dataUrl); return; }
    const k = 1216 / long;
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(img.width * k);
    canvas.height = Math.round(img.height * k);
    canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
    apply(canvas.toDataURL("image/jpeg", 0.9));
  };
  img.onerror = () => apply(dataUrl);
  img.src = dataUrl;
}

/* ---------------- gallery picker (character form "Choose image…") ------- */

let gpTarget = null;            // "avatar" | "cover" | "sccover"
let gpFolder = "";
let gpImages = [], gpFolderList = [];
let gpSeq = 0;

function openGalPick(target) {
  gpTarget = target;
  els.gpTitle.textContent =
    target === "avatar" ? "Choose avatar" : "Choose first image";
  els.pageGalpick.hidden = false;
  gpNavigate("");
}

async function gpNavigate(folder) {
  const seq = ++gpSeq;
  gpFolder = folder;
  els.gpCrumbs.textContent = "";
  els.gpFolders.textContent = "";
  els.gpGrid.textContent = "";
  els.gpEmpty.hidden = true;
  try {
    const d = await api("/api/gallery?tree=1&folder=" + encodeURIComponent(folder));
    if (seq !== gpSeq || els.pageGalpick.hidden) return;
    gpImages = d.images || [];
    gpFolderList = d.folders || [];
    gpRender();
  } catch (e) {
    if (seq !== gpSeq) return;
    if (e.message !== "locked") toast("Gallery failed: " + e.message, true);
  }
}

function gpRender() {
  const bar = els.gpCrumbs;
  bar.textContent = "";
  const segs = gpFolder ? gpFolder.split("/") : [];
  const root = el("button", null, "Gallery");
  root.classList.toggle("here", !segs.length);
  root.addEventListener("click", () => gpNavigate(""));
  bar.appendChild(root);
  let acc = "";
  segs.forEach((s) => {
    acc = joinFolder(acc, s);
    const b = el("button", null, s);
    const to = acc;
    b.addEventListener("click", () => gpNavigate(to));
    bar.appendChild(el("span", "sep", "/"));
    bar.appendChild(b);
  });
  els.gpFolders.textContent = "";
  for (const f of gpFolderList) {
    const rel = joinFolder(gpFolder, f.name);
    const t = el("button", "gtile");
    t.innerHTML =
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';
    const txt = el("div", "gt-text");
    txt.appendChild(el("b", null, f.name));
    txt.appendChild(el("span", null, f.count === 1 ? "1 image" : f.count + " images"));
    t.appendChild(txt);
    t.addEventListener("click", () => gpNavigate(rel));
    els.gpFolders.appendChild(t);
  }
  els.gpGrid.textContent = "";
  gpImages.forEach((im, i) => {
    const cell = el("div", "gcell");
    const img = document.createElement("img");
    img.src = "/thumb/" + im.rel;
    if (i >= 24) img.loading = "lazy";
    img.decoding = "async";
    img.alt = im.name;
    img.title = im.name;
    cell.addEventListener("click", () => usePickedImage(im.rel));
    cell.appendChild(img);
    els.gpGrid.appendChild(cell);
  });
  const bare = !gpImages.length && !gpFolderList.length;
  els.gpEmpty.hidden = !bare;
  els.gpEmptyText.textContent = gpFolder
    ? "This folder is empty."
    : "No images yet — ask the agent to draw something, or tap ⤒ to upload.";
}

async function galleryImageDataUrl(rel) {
  const r = await fetch("/outputs/" + rel);
  if (!r.ok) throw new Error("image load failed");
  const blob = await r.blob();
  return await new Promise((ok, fail) => {
    const fr = new FileReader();
    fr.onload = () => ok(fr.result);
    fr.onerror = () => fail(new Error("read failed"));
    fr.readAsDataURL(blob);
  });
}

async function usePickedImage(rel) {
  try {
    const dataUrl = await galleryImageDataUrl(rel);
    if (els.pageGalpick.hidden) return;
    if (gpTarget === "avatar") avatarFromDataUrl(dataUrl);
    else coverFromDataUrl(dataUrl, gpTarget === "sccover" ? "sc" : "ch");
    els.pageGalpick.hidden = true;
    toast("Image set — save to keep it");
  } catch (e) {
    if (e.message !== "locked") toast("Failed to load image: " + e.message, true);
  }
}

/* ------------------------------- AI generate sheet (character form) ----- */

const AI_MODES = {
  character: {
    title: "Character assistant",
    hint: "Describe them to start, then keep talking — ask for one field to "
          + "change, or say revert to undo the last edit.",
    placeholder: "e.g. Sasuke from Naruto… then: make the first message shorter",
    prefill: () => "",
  },
};

// The assistant is a conversation, not a one-shot filler: the user describes
// a character, the model drafts the card, and they keep coming back to adjust
// one field at a time. State lives here, not in the sheet, so closing the
// sheet to hand-edit the form and reopening it keeps the thread — which is
// what lets the model see those hand edits as the live card state.
const AI_FIELDS = ["name", "appearance", "persona", "greeting"];
let aiHistory = [];      // [{role, content}] sent to the model
let aiStack = [];        // snapshots of the 4 fields, for revert
let aiBusy = false;

function charFields() {
  return {
    name: els.chName.value,
    appearance: els.chAppearance.value,
    persona: els.chPersona.value,
    greeting: els.chGreeting.value,
  };
}

function applyCharFields(f) {
  els.chName.value = f.name ?? "";
  els.chAppearance.value = f.appearance ?? "";
  els.chPersona.value = f.persona ?? "";
  els.chGreeting.value = f.greeting ?? "";
}

const FIELD_LABEL = {
  name: "name", appearance: "look tags",
  persona: "personality", greeting: "first message",
};

function aiLog(cls, text) {
  const d = el("div", cls, text);
  els.aiLog.appendChild(d);
  els.aiLog.scrollTop = els.aiLog.scrollHeight;
  return d;
}

function aiReset() {
  aiHistory = [];
  aiStack = [];
  els.aiLog.textContent = "";
}

function openAiSheet(mode) {
  const m = AI_MODES[mode];
  if (!m) return;
  els.aiTitle.textContent = m.title;
  els.aiHint.textContent = m.hint;
  if (!els.aiText.placeholder) els.aiText.placeholder = m.placeholder;
  els.aiGo.disabled = false;
  els.aiGo.textContent = "Send";
  if (!els.aiLog.childElementCount) {
    aiLog("ai-note", "Describe a character, or paste a name. Afterwards you "
      + "can ask for one field to change, or say revert.");
  }
  els.aiSheet.hidden = false;
  setTimeout(() => els.aiText.focus(), 60);
}

async function runAiGenerate() {
  const text = els.aiText.value.trim();
  if (!text) { toast("Say what you want first", true); return; }
  if (aiBusy) return;
  const before = charFields();          // includes the user's hand edits
  aiBusy = true;
  const btn = els.aiGo;
  btn.disabled = true;
  btn.textContent = "Thinking…";
  els.aiText.value = "";
  aiLog("ai-you", text);
  try {
    const d = await api("/api/char/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt: text,
        history: aiHistory,
        current: before,
      }),
    });
    if (d.revert) {
      const prev = aiStack.pop();
      if (prev) {
        applyCharFields(prev);
        aiLog("ai-note", "Reverted — the fields are back as they were.");
      } else {
        aiLog("ai-note warn", "Nothing to revert to yet.");
      }
    }
    const changed = AI_FIELDS.filter((k) => d[k]);
    if (changed.length) {
      // Snapshot what the form held BEFORE this edit — hand edits included,
      // so revert restores the user's own version and not the model's.
      aiStack.push(before);
      if (aiStack.length > 25) aiStack.shift();
      const next = { ...before };
      for (const k of changed) next[k] = d[k];
      applyCharFields(next);
      aiLog("ai-note", "Changed: " + changed.map((k) => FIELD_LABEL[k])
        .join(", ") + ".");
    }
    if (d.dropped_tags && d.dropped_tags.length) {
      aiLog("ai-note warn", "Left out of the look tags (they'd be forced "
        + "into every image): " + d.dropped_tags.join(", "));
    }
    const said = d.reply || (changed.length ? "Done." : "");
    if (said) aiLog("ai-bot", said);
    aiHistory.push({ role: "user", content: text });
    if (said) aiHistory.push({ role: "assistant", content: said });
    if (changed.length && aiStack.length === 1) {
      aiLog("ai-note", "Say revert to undo that.");
    }
  } catch (e) {
    if (e.message !== "locked") aiLog("ai-note warn", "Failed: " + e.message);
    els.aiText.value = text;            // don't swallow what they typed
  } finally {
    aiBusy = false;
    btn.disabled = false;
    btn.textContent = "Send";
  }
}

els.aiGo.addEventListener("click", runAiGenerate);
els.aiText.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    runAiGenerate();
  }
});
els.aiCancel.addEventListener("click", () => {
  els.aiSheet.hidden = true;
});

function charCtx(row) {
  const id = row.dataset.charid;
  const c = chars.find((x) => x.id === id);
  if (!c) return;
  openCtx([
    { label: c.fav ? "Unfavourite" : "Favourite",
      action: () => toggleFav(id) },
    { label: "Edit", action: () => openCharForm(c) },
    { label: "Start scenario", action: () => startScenarioMenu(c) },
    ...(c.system ? [] : [{ label: "Delete", danger: true,
      action: () => deleteCharById(id, c.name) }]),
  ], c.avatar || null);
}

els.cfBack.addEventListener("click", () => { els.pageChform.hidden = true; });
$("btn-addchar").addEventListener("click", () => openCharForm(null));
$("btn-settings").addEventListener("click", openSettings);
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
for (const b of document.querySelectorAll("#charseg button")) {
  b.addEventListener("click", () => setCharView(b.dataset.charview));
}
updateGridBtn();

// Build stamp: taken from this page's own script tag rather than a constant,
// so it can never disagree with the code actually executing — which is the
// only way to tell "my change isn't landing" from "the browser is running
// something else". Paired with the stale-build guard above.
(function stampBuild() {
  const tag = document.querySelector('script[src*="app.js"]');
  const v = tag && /app\.js\?v=(\d+)/.exec(tag.getAttribute("src") || "");
  const out = $("sh-build");
  if (out) out.textContent = v ? "v" + v[1] : "unversioned";
})();
$("btn-back").addEventListener("click", () => switchView("chars"));
$("btn-newchat").addEventListener("click", newChat);
els.btnChmenu.addEventListener("click", openChatMenu);

/* ------------------------------------------------------- chat options menu
 *
 * Everything chat-wide that isn't worth a permanent header button lives
 * here. Both settings are per chat and owned by the server; this file only
 * mirrors them so the menu can show the current state.
 *
 * Auto images: off means the image tools are withheld from the model
 * entirely, so the chat is plain text — regenerating an existing image from
 * the UI still works.
 *
 * Like/dislike: one verdict on the conversation as it stands. The server
 * clears it when the next reply lands (and archives it), so tapping is
 * always about what is on screen right now. Captured only — nothing reads
 * it yet.
 */

let autoImages = true;
let chatRating = "";        // "" | "like" | "dislike"

/* chat-mutating endpoints (new / select / delete / character switch) carry
   the active chat's flags, so the menu follows the chat without a refetch */
function applyChatFlags(d) {
  if (!d) return;
  if (typeof d.auto_images === "boolean") autoImages = d.auto_images;
  // present-but-empty clears the rating: deleting the open chat lands on
  // another one, which is unrated
  if ("feedback" in d) chatRating = (d.feedback && d.feedback.rating) || "";
}

function openChatMenu() {
  const rated = chatRating;
  openCtx([
    { label: rated === "like" ? "Liked" : "Like", on: rated === "like",
      action: () => rateChat("like") },
    { label: rated === "dislike" ? "Disliked" : "Dislike",
      on: rated === "dislike", action: () => rateChat("dislike") },
    { label: autoImages ? "Auto images on" : "Auto images off",
      on: autoImages, action: () => toggleAutoImages() },
  ]);
}

async function rateChat(rating) {
  try {
    const d = await api("/api/chat/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rating }),
    });
    chatRating = (d.feedback && d.feedback.rating) || "";
    toast(chatRating ? (chatRating === "like" ? "Liked" : "Disliked")
                     : "Rating cleared");
  } catch (e) {
    if (e.message !== "locked") toast("Failed: " + e.message, true);
  }
}

async function toggleAutoImages() {
  const want = !autoImages;
  const prev = autoImages;
  autoImages = want;                               // optimistic
  try {
    const d = await api("/api/chat/auto_images", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ on: want }),
    });
    autoImages = d.auto_images !== false;
    toast(autoImages ? "Auto images on" : "Auto images off — text only");
  } catch (e) {
    autoImages = prev;                             // server refused: revert
    if (e.message !== "locked") toast("Failed: " + e.message, true);
  }
}

els.cfSave.addEventListener("click", saveCharForm);
els.chDelete.addEventListener("click", () =>
  deleteCharById(editCharId, els.chName.value.trim()));
els.chAvatarBtn.addEventListener("click", () => openGalPick("avatar"));
els.chAvatarFile.addEventListener("change", () => {
  if (els.chAvatarFile.files[0]) pickAvatar(els.chAvatarFile.files[0]);
  els.chAvatarFile.value = "";
});
els.chAvatarClear.addEventListener("click", () => {
  pendingAvatar = "";
  clearAvatar = true;
  setPreview(els.chAvatarImg, els.chAvatarClear, "");
});
els.chCoverBtn.addEventListener("click", () => openGalPick("cover"));
els.chCoverFile.addEventListener("change", () => {
  if (els.chCoverFile.files[0]) pickCover(els.chCoverFile.files[0], "ch");
  els.chCoverFile.value = "";
});
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
    // per-chat setting — pick it up on chat switch, new chat and restart
    applyChatFlags(s);
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

// lightbox mode — segmented control in Settings → Visuals, saved like accent
(function initLbMode() {
  const seg = document.getElementById("lbmode-seg");
  const paint = () => {
    const m = lbMode();
    for (const b of seg.querySelectorAll("button")) {
      b.classList.toggle("on", b.dataset.lbmode === m);
    }
  };
  paint();
  seg.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-lbmode]");
    if (!b) return;
    localStorage.setItem("lbmode", b.dataset.lbmode);
    paint();
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
    applyChatFlags(d);
    toast("New chat");
  } catch (e) {
    if (e.message !== "locked") toast("Failed: " + e.message, true);
  }
}

/* --------------------------------------------------------- chat history
 *
 * One list of every chat of every character, newest first — it lives on the
 * Characters page, not in a chat, precisely because it crosses characters.
 * A row is the character's name, the user's first message in that chat (no
 * generated title) and when it was last touched.
 */

function fmtChatDate(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" }) +
    " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

async function openHistory() {
  setHsSelect(false);
  try {
    const d = await api("/api/chats");
    els.hsList.textContent = "";
    if (!d.chats.length) {
      els.hsList.appendChild(el("div", "hint", "No chats yet."));
    }
    for (const c of d.chats) {
      const active = c.id === d.active && c.char === d.active_char;
      const row = el("button", "hist-row" + (active ? " active" : ""));
      row.appendChild(el("span", "hs-check", "✓"));
      const tx = el("div", "char-text");
      tx.appendChild(el("b", null, c.char_name));
      tx.appendChild(el("span", "hs-preview", c.preview || "No messages yet"));
      tx.appendChild(el("span", "dim", fmtChatDate(c.updated) +
        " | (" + c.messages + " message" + (c.messages === 1 ? "" : "s") + ")"));
      row.appendChild(tx);
      row.addEventListener("click", () => {
        if (hsSelecting) {
          const key = hsKey(row);
          if (hsSelected.has(key)) {
            hsSelected.delete(key);
            row.classList.remove("sel");
          } else {
            hsSelected.add(key);
            row.classList.add("sel");
          }
          hsTitleSync();
        } else selectChat(c.id, c.char);
      });
      row.dataset.chatid = c.id;
      row.dataset.char = c.char;
      els.hsList.appendChild(row);
    }
    els.pageHist.hidden = false;
  } catch (e) {
    if (e.message !== "locked") toast("History failed: " + e.message, true);
  }
}

/* ---- batch select/delete (Chatterbox-style) ---- */

let hsSelecting = false;
const hsSelected = new Set();      // "<char>:<chat id>" — ids repeat per char

function hsKey(row) { return row.dataset.char + ":" + row.dataset.chatid; }

function hsTitleSync() {
  const n = hsSelected.size;
  els.hsTitle.textContent = hsSelecting
    ? (n ? `${n} selected` : "Select chats")
    : "Conversation History";
  els.hsDel.classList.toggle("armed", n > 0);
}

function setHsSelect(on) {
  hsSelecting = on;
  hsSelected.clear();
  els.pageHist.classList.toggle("selecting", on);
  els.hsSelect.hidden = on;
  els.hsAll.hidden = !on;
  els.hsDel.hidden = !on;
  for (const row of els.hsList.querySelectorAll(".hist-row.sel")) {
    row.classList.remove("sel");
  }
  hsTitleSync();
}

async function deleteChatsBatch(keys) {
  /* keys are "<char>:<chat id>" — a batch spans characters, so each delete
     names its own owner. The server falls back to the character's newest
     remaining chat when the one deleted was the open one, which can move
     the app off the character it was on: re-read both after the batch. */
  let ok = 0;
  for (const key of keys) {
    const i = key.indexOf(":");
    try {
      await api("/api/chat/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ char: key.slice(0, i), chat: key.slice(i + 1) }),
      });
      ok++;
    } catch (e) {
      if (e.message === "locked") break;
    }
  }
  toast(ok === keys.length
    ? `Deleted ${ok} chat${ok === 1 ? "" : "s"}`
    : `Deleted ${ok} of ${keys.length}`);
  await refreshStatus();
  await reloadChat();
  await openHistory();
}

function selectChat(chatId, charId) {
  api("/api/chat/select", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ char: charId, chat: chatId }),
  }).then((d) => {
    els.msgs.textContent = "";
    renderHistory(d.timeline || []);
    applyChatFlags(d);
    els.pageHist.hidden = true;
    switchView("chat");
    refreshStatus();     // the chat may belong to another character
  }).catch((e) => {
    if (e.message !== "locked") toast("Open failed: " + e.message, true);
  });
}

async function deleteChatById(row) {
  const char = row.dataset.char, chat = row.dataset.chatid;
  try {
    await api("/api/chat/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ char, chat }),
    });
    toast("Chat deleted");
    await refreshStatus();
    await reloadChat();
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
  const all = rows.length && rows.every((r) => hsSelected.has(hsKey(r)));
  for (const r of rows) {
    if (all) { hsSelected.delete(hsKey(r)); r.classList.remove("sel"); }
    else { hsSelected.add(hsKey(r)); r.classList.add("sel"); }
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
els.hsList.addEventListener("contextmenu", (e) => {
  if (hsSelecting) return;
  const target = e.target.closest(".hist-row");
  if (!target) return;
  e.preventDefault();
  openCtx([{ label: "Delete", danger: true,
    action: () => deleteChatById(target) }], null);
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
    openCtx([{ label: "Delete", danger: true,
      action: () => deleteChatById(target) }], null);
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

/* Copy via a throwaway textarea when the async clipboard is unusable.
   Reached over plain http on a LAN address — how the phone gets to this —
   the page is not a secure context, so navigator.clipboard is undefined and
   calling it throws synchronously, before .catch() is ever attached. That
   silent throw is why the buttons appeared to do nothing at all. */
function legacyCopy(text) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.cssText = "position:fixed;top:0;left:0;opacity:0";
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
  document.body.removeChild(ta);
  return ok;
}

function copyText(text) {
  const done = (ok) => toast(ok ? "Copied" : "Copy failed", !ok);
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(() => done(true), () => done(legacyCopy(text)));
  } else {
    done(legacyCopy(text));
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
    const b = el("button", (it.danger ? "danger" : "")
      + (it.on ? " on" : ""), it.label);
    if (it.on) b.setAttribute("aria-pressed", "true");
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
    if (rel) items.push({ label: "Regenerate", action: () => openRegen(rel) });
    if (rel) items.push({ label: "Edit in chat", action: () => {
      // a bare file name — edit_image resolves it anywhere in the tree
      switchView("chat");
      els.input.value = `Edit ${baseName(rel)} — `;
      autosize(); els.input.focus(); els.send.classList.add("ready");
    }});
    if (rel) items.push({ label: "Show in gallery", action: () => {
      els.pageGallery.hidden = false;
      setGalSelect(false);
      loadGallery(rel.includes("/") ? parentOf(rel) : "");
    }});
    items.push({ label: "Delete from chat", danger: true, action: () => deleteChatEvent(el) });
    return { items, previewSrc: rel ? "/thumb/" + rel : null };
  }
  if (el.classList.contains("msg")) {
    const items = [];
    const editable = el.classList.contains("ai")
                     && el.dataset.idx != null;
    if (!el.classList.contains("error")) {
      items.push({ label: "Copy",
        action: () => copyText(el.dataset.raw || el.textContent) });
    }
    if (editable) {
      items.push({ label: "Edit message",
        action: () => startMsgEdit(el) });
      items.push({ label: "Regenerate reply",
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
let regenModels = [];                // checkpoints, for the dropdown
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

/** Refresh the themed-dropdown buttons after recipe fields were filled
    programmatically — the hidden inputs don't fire events on assignment. */
function regenSyncDD() {
  for (const k of ["mode", "sampler", "sched", "model", "te", "vae", "lb"]) {
    const f = regenF[k];
    if (f && f._ddSync) f._ddSync();
  }
}

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

/** API schedule name -> the label the WebUI shows. Idempotent: labels
    round-trip ("Karras" -> "Karras"). */
function schedLabel(n) {
  const special = {
    flow_match: "FlowMatchEulerDiscrete", flux2: "Flux2",
    sgm_uniform: "SGM Uniform", linear_quadratic: "Linear Quadratic",
    kl_optimal: "KL Optimal", align_your_steps: "Align Your Steps",
    bong_tangent: "Bong Tangent",
  };
  return special[n]
    || n.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
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
  regenSyncDD();
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
    regenArch = d.arch || g.arch || archOfModel(regenF.model.value) || "";
    regenProfile = d.profile
      || (sdCaps.architectures || []).find((a) => a.arch === regenArch)
      || null;
    if (d.capabilities) sdCaps = d.capabilities;
    regenSyncDD();
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
$("regen-seed-rand").addEventListener("click", () => {
  // The sheet opens pre-filled with the image's own seed, so regenerating
  // as-is reproduces it. Roll Forge's 32-bit seed space and show it rather
  // than sending -1, so the value stays visible and re-runnable.
  const buf = new Uint32Array(1);
  crypto.getRandomValues(buf);
  regenF.seed.value = buf[0];
});
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
          // a batch regen saves more than one file under a single event
          const files = (evt.files && evt.files.length) ? evt.files : [evt.file];
          const rec = variantIndex.get(evt.src);
          // the server appended an event, so anything streamed after this
          // has to index past it
          evtCounter = Math.max(evtCounter, (evt.idx ?? -1) + 1);
          if (rec) {
            for (const f of files) if (!rec.files.includes(f)) rec.files.push(f);
            if (evt.idx != null) rec.evtIdxs.push(evt.idx);
            // the variant we just made is itself regeneratable, so it has to
            // be findable as a src. Without this, regenerating a regenerated
            // image looks up a path no record points at, falls through to the
            // branch below, and the new picture never appears — the server
            // has already written it and pushed the event, so it only shows
            // up after a reload, when the whole timeline is walked in order.
            for (const f of files) variantIndex.set(f, rec);
            rec.pos = rec.files.length - 1;      // show the new variant
            renderCarousel(rec);
            rec.card.scrollIntoView({ behavior: "smooth", block: "center" });
          } else {
            // nothing to extend (its card is gone but the file wasn't) —
            // draw the new image on its own rather than dropping it, which
            // is what re-rendering the timeline would have given us anyway
            addGeneration({ files: files.map((f) => "/outputs/" + f) },
                          evt.idx);
          }
          // the lightbox list is newest-first, and unshift puts each one in
          // front — so walking the batch in order leaves the last file on top
          for (const f of files) {
            if (parentOf(f) === galFolder && !images.some((x) => x.rel === f)) {
              images.unshift({ rel: f, name: baseName(f), seed: evt.seed });
            }
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
for (const b of document.querySelectorAll("#ps-gen-tabs button")) {
  b.addEventListener("click", () => {
    setGenTab(b.dataset.gtab);
    document.querySelector(".ps-body").scrollTop = 0;
  });
}

// --- LoRAs ---
// Nothing here creates a record. A LoRA has to exist as a file before its
// name is knowable — the string a card stores is the filename Forge resolves,
// and that cannot be guessed or derived from a Civitai page. Notes are
// therefore written against a file the server has already seen; a record with
// no file can still appear here, but only because the file was deleted out
// from under a card that still points at it.
els.shLoraQ.addEventListener("input", renderLoras);
els.shLoraArch.addEventListener("change", renderLoras);
els.shLoraNote.addEventListener("change", renderLoras);
els.shLoraUse.addEventListener("change", renderLoras);
els.shLoraRefresh.addEventListener("click", async () => {
  els.shLoraRefresh.disabled = true;
  try {
    const d = await api("/api/loras", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "refresh" }),
    });
    loraRows = d.loras || [];
    loraArchNow = d.current_arch || loraArchNow;
    renderLoraArchFilter(); renderLoras(); renderChLoras();
    toast(loraRows.filter((r) => r.installed).length + " LoRAs on the server");
  } catch (e) { toast(String(e), true); }
  els.shLoraRefresh.disabled = false;
});

// --- CivitAI browse ---
els.shDlGo.addEventListener("click", () => searchLoras(true));
els.shDlMore.addEventListener("click", () => searchLoras(false));
els.shDlQ.addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); searchLoras(true); }
});
els.shDlTag.addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); searchLoras(true); }
});
// sort, period and tag change what Civitai is asked for, so they re-query
els.shDlSort.addEventListener("change", () => searchLoras(true));
els.shDlPeriod.addEventListener("change", () => searchLoras(true));
els.shDlTag.addEventListener("change", () => searchLoras(true));
// architecture and SFW are post-filters over what came back — instant, and no
// reason to spend a Civitai call on them
els.shDlArch.addEventListener("change", renderDl);
// the SFW toggle borrows the buttons' pressed styling, which is a class on
// the label — :has() would do it in CSS but not on every browser this PWA
// still has installed in a tab
els.shDlSfw.addEventListener("change", () => {
  els.shDlSfw.parentElement.classList.toggle("on", els.shDlSfw.checked);
  renderDl();
});
els.shDlBox.addEventListener("toggle", () => {
  if (els.shDlBox.open && !dlItems.length) searchLoras(true);
});
// tap the backdrop to dismiss, Escape too — the card itself is the target of
// clicks, so without this the overlay would swallow every tap on the sheet
els.loraModal.addEventListener("click", (e) => {
  if (e.target === els.loraModal) closeLoraModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !els.loraModal.hidden) closeLoraModal();
});
$("sh-save-key").addEventListener("click", () => {
  const v = els.shKey.value.trim();
  if (v) saveSettings({ openrouter_key: v }, $("sh-save-key"), "API key saved")
    .then(() => { els.shKey.value = ""; });
});
$("sh-save-llm").addEventListener("click", () => {
  const v = els.shLlm.value.trim();
  if (!v) {
    toast("Add at least one model to the chain", true);
    return;
  }
  saveSettings({ llm: v }, $("sh-save-llm"), "LLM chain saved");
});
on("sh-llm-pick", "click", openLlmPicker);
on("sh-llm-refresh", "click", () => loadLlmModels(true));
on("sh-llm-addgo", "click", () => {
  const v = els.shLlmAdd.value.trim();
  if (!v) return;
  addLlmModel(v);
  els.shLlmAdd.value = "";
});
on("sh-llm-add", "keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); els.shLlmAddGo.click(); }
});
$("sh-save-sd").addEventListener("click", async () => {
  const v = els.shSd.value.trim();
  if (!v) return;
  const btn = $("sh-save-sd"), orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Checking…";
  try {
    // the server probes the address before saving it, so this either switches
    // to a live image server or fails with the old one still running
    const d = await api("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sd_url: v }),
    });
    lastSdUrl = v;
    toast(d.sd_model ? `Connected — running ${d.sd_model}`
      : "Image server connected");
  } catch (e) {
    if (e.message !== "locked") {
      toast(e.message, true);
      els.shSd.value = lastSdUrl || els.shSd.value;   // put the good one back
    }
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
    refreshStatus().then((s) => {
      if (s) { paintImageTab(s); onSdSettled(paintImageTab); }
    });
  }
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
$("sh-save-guide").addEventListener("click", () => {
  const v = els.shGuide.value.trim();
  if (!v) {
    toast("Clear the box or use Reset to go back to model_guide.txt", true);
    return;
  }
  saveSettings({ model_guide: v }, $("sh-save-guide"), "Model guide saved")
    .then((d) => {
      if (d) els.shGuideState.textContent =
        d.model_guide_custom ? "· custom" : "· model_guide.txt";
    });
});
$("sh-reset-guide").addEventListener("click", async () => {
  if (!confirm("Go back to the model_guide.txt file? Your saved edits "
             + "here will be discarded.")) return;
  const btn = $("sh-reset-guide"), orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Resetting…";
  try {
    const d = await api("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_guide_reset: true }),
    });
    els.shGuide.value = d.model_guide || "";
    els.shGuideState.textContent = "· model_guide.txt";
    toast("Model guide reset to model_guide.txt");
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
  els.ppAppearance.value = p ? (p.appearance || "") : "";
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
                             description: els.ppDesc.value.trim(),
                             appearance: els.ppAppearance.value.trim() }),
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
    { label: "Edit", action: () => openPersonaForm(p) },
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
    label: (s.name || s.id),
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
els.scsCoverBtn.addEventListener("click", () => openGalPick("sccover"));
els.scsCoverFile.addEventListener("change", () => {
  if (els.scsCoverFile.files[0]) pickCover(els.scsCoverFile.files[0], "sc");
  els.scsCoverFile.value = "";
});
els.scsCoverClear.addEventListener("click", () => {
  pendingScCover = "";
  clearScCover = true;
  setPreview(els.scsCoverImg, els.scsCoverClear, "");
});

/* gallery picker page controls */
els.gpBack.addEventListener("click", () => { els.pageGalpick.hidden = true; });
els.gpUpload.addEventListener("click", () => {
  // close the picker first so the form (and its new preview) is visible
  // when the file dialog returns; the click stays inside this user gesture
  const target = gpTarget;
  els.pageGalpick.hidden = true;
  if (target === "avatar") els.chAvatarFile.click();
  else if (target === "sccover") els.scsCoverFile.click();
  else els.chCoverFile.click();
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
    { label: "Edit", action: () => openScenarioForm(s) },
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
$("lb-copy-entry").addEventListener("click", () => {
  const entry = lbEntryText();
  if (!entry) return toast("No generation settings for this image", true);
  copyText(entry);
});
for (const b of document.querySelectorAll(".copybtn")) {
  if (!b.dataset.copy) continue;      // lb-copy-entry builds its own text
  b.addEventListener("click", () => copyText($(b.dataset.copy).textContent));
}

// swipe between gallery images (slideshow mode only — in scroll mode the
// vertical flick owns the gesture and horizontal swipes mean nothing)
let touchX = null;
els.lbStage.addEventListener("touchstart", (e) => { touchX = e.touches[0].clientX; }, { passive: true });
els.lbStage.addEventListener("touchend", (e) => {
  if (touchX == null) return;
  const dx = e.changedTouches[0].clientX - touchX;
  if (!lbIsScroll() && Math.abs(dx) > 50) lbMove(dx < 0 ? 1 : -1);
  touchX = null;
}, { passive: true });

// scroll-mode tracking: which slide is centered right now
els.lbStage.addEventListener("scroll", lbOnScroll, { passive: true });
// rotation/keyboard snap: keep the current slide aligned after a resize
window.addEventListener("resize", () => {
  if (els.lb.hidden || !lbIsScroll() || !lbList[lbIndex]) return;
  const st = els.lbStage;
  st.scrollTop = lbIndex * Math.max(1, st.clientHeight);
});

document.addEventListener("keydown", (e) => {
  if (!els.lb.hidden) {
    if (e.key === "Escape") {
      if (els.lbSheet.classList.contains("open")) toggleLbInfo(false);
      else closeLightbox();
    }
    if (e.key === "ArrowLeft" || (lbIsScroll() && e.key === "ArrowUp")) lbMove(-1);
    if (e.key === "ArrowRight" || (lbIsScroll() && e.key === "ArrowDown")) lbMove(1);
  } else if (e.key === "Escape" && !els.pageSettings.hidden) {
    els.pageSettings.hidden = true;
  } else if (e.key === "Escape" && !els.pagePersona.hidden) {
    els.pagePersona.hidden = true;
  } else if (e.key === "Escape" && !els.pageScenario.hidden) {
    els.pageScenario.hidden = true;
  } else if (e.key === "Escape" && !els.pageGalpick.hidden) {
    els.pageGalpick.hidden = true;
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

/* ------------------------------------------------ Android back button ---
 *
 * In standalone PWA mode the hardware back button used to close the whole
 * app: the page never pushes history entries, so there was nothing to pop.
 * We park one guard entry on the history stack and spend each back press on
 * the topmost overlay — the same chain the in-app back arrows use. On the
 * home screen nothing is left to close, so the press arms an exit
 * confirmation; the guard is then NOT re-pushed, so the next press really
 * exits. Tapping anywhere (or letting the toast lapse) re-parks the guard.
 */

const BACK_GUARD = { sdBack: 1 };

function backCloseOverlay() {          // true = a UI layer consumed the press
  if (!ctxEl.hidden) { closeCtx(); return true; }
  if (!els.lb.hidden) {
    if (els.lbSheet.classList.contains("open")) toggleLbInfo(false);
    else closeLightbox();
    return true;
  }
  if (!regenEl.hidden) { regenEl.hidden = true; return true; }
  if (!els.aiSheet.hidden) { els.aiSheet.hidden = true; return true; }
  if (!els.foldSheet.hidden) { els.foldSheet.hidden = true; return true; }
  if (!els.foldPick.hidden) { els.foldPick.hidden = true; return true; }
  if (!els.pageSettings.hidden) { els.pageSettings.hidden = true; return true; }
  if (!els.pagePersona.hidden) { els.pagePersona.hidden = true; return true; }
  if (!els.pageScenario.hidden) { els.pageScenario.hidden = true; return true; }
  if (!els.pageGalpick.hidden) { els.pageGalpick.hidden = true; return true; }
  if (!els.pageChform.hidden) { els.pageChform.hidden = true; return true; }
  if (!els.pageHist.hidden) {
    if (hsSelecting) setHsSelect(false);
    else els.pageHist.hidden = true;
    return true;
  }
  if (!els.pageGallery.hidden) { galleryBack(); return true; }
  return false;
}

let backExitArmed = false;
let backExitTimer = 0;

function backRearm() {
  // park a fresh guard entry so the next press pops instead of exiting
  history.pushState(BACK_GUARD, "");
}

function backDisarm() {
  backExitArmed = false;
  clearTimeout(backExitTimer);
  backRearm();
}

window.addEventListener("popstate", () => {
  if (backExitArmed) {                 // confirmed exit — only reachable when
    history.back();                    // deeper history exists (rare); the
    return;                            // usual path exits at browser level
  }
  clearTimeout(backExitTimer);
  if (backCloseOverlay()) { backRearm(); return; }
  if (!els.viewChat.hidden) {          // back out of a chat to the list
    switchView("chars");
    backRearm();
    return;
  }
  // home screen: ask before leaving — do not re-arm, so the next press exits
  backExitArmed = true;
  toast("Click back again to exit the app");
  backExitTimer = setTimeout(backDisarm, 2600);   // matches the toast
});

// any tap while the exit confirmation shows cancels it (and re-parks the
// guard, so a later back press asks again instead of exiting silently)
document.addEventListener("pointerdown", () => {
  if (backExitArmed) backDisarm();
}, { capture: true });

if (!history.state || !history.state.sdBack) backRearm();

/* ------------------------------------------------------------------ init */

let inited = false;

async function init() {
  if (inited) return;
  inited = true;
  switchView("chars");                    // the app opens on the character list
  autosize();
  refreshStatus();
  // Installed LoRAs are rediscovered by the server on every call, so there is
  // nothing to initialise — but the card sheet's picker reads this list, and
  // waiting for a visit to Settings > LoRAs left it claiming "none known" on a
  // box that had a LoRA sitting right there.
  loadLoras();
  try {
    const h = await api("/api/history");
    applyChatFlags(h);
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

ddInit();                            // themed dropdowns over native popups

(async function bootstrap() {
  try {
    const a = await api("/api/auth");
    me = a.user || null;
    wireAdmin();
    if (a.authed) showApp();
    else showGate();
  } catch {
    showGate();
  }
})();

/* --------------------------------------------------------------- PWA SW */

/* registration and the takeover-reload live at the top of this file, next to
   the version-skew guard: they are the app's self-repair and must not sit
   below code that can throw. */
