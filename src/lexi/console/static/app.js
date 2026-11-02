"use strict";
/* LEXI console. Vanilla JS, no build. The server owns every number: this file formats what arrives on /api/stream and sends back the four operator
   actions (resolve a ticket, command the twin, read a report, reset the counters). Nothing on screen is typed in. */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const inr = (n) => (n == null ? "—" : "₹" + Math.round(n).toLocaleString("en-IN"));
const pct = (x, d = 1) => (x == null ? "—" : (x * 100).toFixed(d) + "%");
const lam = (x) => (x == null ? "—" : x < 0.01 ? x.toExponential(2) : x.toFixed(3));
const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const clamp = (x, a = 0, b = 1) => Math.max(a, Math.min(b, x));
const post = (path, body) => fetch(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body ?? {}) }).then((r) => r.json());
const INTENT = { CREDIT_ENTRY: "Credit", DEBIT_ENTRY: "Debit", PROMISE_TO_PAY: "Promise to pay", RESCHEDULE_DUE: "Reschedule due" };
const SCENARIOS = [
  ["S0", "Reference traffic", "the profile the certificates were earned on"],
  ["S1", "Monsoon", "−10 dB babble, 5% packet loss"],
  ["S2", "Codec swap", "wideband to AMR-NB"],
  ["S3", "New speakers", "voices unseen in training"],
  ["S4", "New parties", "names outside the registry"],
  ["S5", "Minimal pairs", "confusable numbers only"],
  ["S6", "Null", "long run, no shift"],
];
const KEPT = 40;

const S = {
  catalog: { cells: [] }, registry: null, sentinel: {}, traces: {}, cellstats: null, tally: null, health: {}, head: null, mode: "", providers: "sim",
  scorer: "", clock: { now: 0, speed: 1 }, clockAt: Date.now(), leaseExpiry: 0, ttl: 10, undoS: 5, accounts: [], tickets: new Map(), calls: new Map(), order: [],
  gold: new Map(), names: {}, sel: null, follow: true, holdUntil: 0, scn: "S0", twin: null, plots: {}, open: null, form: null, ticket: null, report: null,
  screen: "s1",
};

/* ---------------------------------------------------------------- rendering schedule */
const dirty = new Set();
let raf = 0;
const mark = (...keys) => { keys.forEach((k) => dirty.add(k)); raf ||= requestAnimationFrame(flush); };
function flush() {
  raf = 0;
  for (const k of dirty) {
    if (k === "bar" || k === "open" || k === S.screen) RENDER[k]?.();  // other screens draw when opened
  }
  dirty.clear();
}
function show(id) {
  S.screen = id;
  $$(".screen").forEach((s) => s.classList.toggle("on", s.id === id));
  $$("#rail button[data-s]").forEach((b) => b.classList.toggle("on", b.dataset.s === id));
  RENDER[id]?.();
}

/* ---------------------------------------------------------------- clock: the server's call time, advanced locally */
const now = () => S.clock.now + ((Date.now() - S.clockAt) / 1000) * S.clock.speed;
const sync = (ts) => { S.clock.now = ts; S.clockAt = Date.now(); };

/* ---------------------------------------------------------------- stream */
const call = (id) => {
  let c = S.calls.get(id);
  if (!c) {
    c = { id, items: [], entry: null, view: null, live: false, done: false, sum: null, t0: Date.now() };
    S.calls.set(id, c);
    if (S.calls.size > 400) for (const [k, v] of S.calls) { if (!v.live) { S.calls.delete(k); break; } }
  }
  return c;
};
const nameOf = (c, id) => {
  if (!id || id === "OUT_OF_REGISTRY") return "unknown party";
  return c?.view?.parties.find((p) => p.id === id)?.display ?? S.names[`${c?.view?.account}/${id}`] ?? id;
};

const H = {
  state(d) {
    Object.assign(S, { catalog: d.catalog, registry: d.registry, sentinel: d.sentinel, traces: d.traces ?? {}, cellstats: d.cellstats, tally: d.tally, health: d.health,
      head: d.audit_head, mode: d.mode, providers: d.providers, scorer: d.scorer_version, clock: d.clock, clockAt: Date.now(), accounts: d.accounts, undoS: d.undo_window_s, ttl: d.lease_ttl_s });
    S.leaseExpiry = d.registry?.lease_expiry ?? 0;
    $("#speed").value = d.clock.speed;
    S.tickets = new Map(d.tickets.map((t) => [t.ticket_id, t]));
    mark("bar", "s1", "s2", "s3", "s4", "s5", "s7");
  },
  audit(r) {
    sync(r.ts);
    S.head = { seq: r.seq, hash: r.hash };
    $("#head").classList.remove("tick"); void $("#head").offsetWidth; $("#head").classList.add("tick");
    if (r.type === "DECISION") {
      const c = call(r.call_id);
      if (r.mode !== "LIVE") return;
      if (!c.live) {
        c.live = true;
        S.order.push(r.call_id);
        if (S.order.length > KEPT) S.calls.delete(S.order.shift());
        if (S.follow && Date.now() >= S.holdUntil) S.sel = r.call_id;
      }
      c.items.push({ k: "turn", r });
      c.latest = r;
      mark("s1", "bar");
    } else if (r.type === "LEDGER_EVENT") {
      const c = call(r.session_id);
      const e = (c.entry ??= { reached: new Set(), t0: r.ts });
      Object.assign(e, r);
      e.reached.add(r.event);
      mark("s1", "bar");
    }
  },
  view(v) { const c = call(v.session_id); c.view = v; for (const p of v.parties) S.names[`${v.account}/${p.id}`] = p.display; mark("s1"); },
  control(m) { if (m.text) { call(m.session_id).items.push({ k: "sys", ...m }); mark("s1"); } },
  gold(g) { S.gold.set(g.call_id, g); if (S.gold.size > 600) S.gold.delete(S.gold.keys().next().value); },
  call(sum) {
    const c = call(sum.call_id);
    c.sum = sum; c.done = true;
    if (sum.baseline?.wrong && !sum.wrong && c.live) { S.sel = sum.call_id; S.holdUntil = Date.now() + 9000; }
    mark("s1");
  },
  outcome() {},
  ticket(t) { S.tickets.set(t.ticket_id, t); mark("s4", "bar"); },  // the Queue badge counts them; a toast per ticket would bury the state changes
  ticket_closed(d) { S.tickets.delete(d.ticket_id); if (S.ticket === d.ticket_id) S.ticket = null; mark("s4", "bar"); },
  registry(r) {
    const first = S.registry?.version;
    S.registry = r; S.leaseExpiry = r.lease_expiry;
    if (first !== undefined && first !== r.version) toast(`Registry v${r.version} published`, "ok");
    mark("s2", "open", "bar", "s5");
  },
  sentinel(d) {
    const t = Date.now() / 1000;
    for (const [cell, st] of Object.entries(d.states)) {
      const tr = (S.traces[cell] ??= []);
      tr.push([t, st.srm1, st.srm2, st.state]);
      if (tr.length > 600) tr.shift();
    }
    S.sentinel = d.states;
    if (d.lease_expiry != null) S.leaseExpiry = d.lease_expiry;
    const e = d.event;
    if (e.type === "STATE_CHANGE") toast(`${e.cell}  ${e.from_state} → ${e.to_state}  (${e.detector} ${e.value != null ? e.value.toFixed(0) : ""})`, e.to_state === "HEALTHY" ? "ok" : "bad");
    mark("s2", "open", "s5", "bar");
  },
  health(h) { S.health[h.component === "C12" ? "audit_healthy" : "ledger_healthy"] = h.healthy; mark("bar"); },
  cellstats(c) { S.cellstats = c; mark("s5", "open"); },
  tally(t) { S.tally = t; mark("s1", "s3", "bar"); if ($("#endcard").classList.contains("on")) RENDER.end(); },
};

const es = new EventSource("/api/stream");
for (const k of Object.keys(H)) es.addEventListener(k, (e) => H[k](JSON.parse(e.data)));
es.onopen = () => { $("#live").classList.add("on"); $("#v-stream").textContent = "live"; };
es.onerror = () => { $("#live").classList.remove("on"); $("#v-stream").textContent = "reconnecting"; };

function toast(msg, cls = "") {
  const t = document.createElement("div");
  t.className = "toast " + cls;
  t.textContent = msg;
  $("#toast").append(t);
  while ($("#toast").children.length > 5) $("#toast").firstChild.remove();
  setTimeout(() => t.remove(), 6000);
}

/* ---------------------------------------------------------------- top bar */
const RENDER = {};
RENDER.bar = () => {
  $("#v-mode").textContent = S.mode + (S.providers === "sim" ? " · sim" : "");
  $("#v-scorer").textContent = S.scorer || "—";
  $("#v-head").textContent = S.head ? `#${S.head.seq} ${(S.head.hash ?? "").slice(0, 8)}` : "—";
  $("#p-audit").className = "pill " + (S.health.audit_healthy === false ? "bad" : "ok");
  $("#p-ledger").className = "pill " + (S.health.ledger_healthy === false ? "bad" : "ok");
  const n = S.tickets.size;
  $("#badge").textContent = n;
  $("#badge").classList.toggle("hidden", !n);
};
setInterval(() => {
  const rem = S.leaseExpiry - now();
  const arc = $("#lease .arc");
  arc.style.strokeDashoffset = 94.25 * (1 - clamp(rem / S.ttl));
  arc.style.stroke = rem > 0 ? "" : css("--revoked");
  $("#v-lease").textContent = S.leaseExpiry ? (rem > 0 ? rem.toFixed(1) + " s" : "lapsed") : "—";
  for (const e of $$(".undo i")) {
    const left = (+e.dataset.deadline - now()) / S.undoS;
    e.style.setProperty("--left", clamp(left));
  }
}, 100);

/* ---------------------------------------------------------------- screen 1: live call */
const confCls = (c) => (c == null ? "na" : c >= 0.85 ? "hi" : c >= 0.6 ? "mid" : "lo");
const logPos = (x) => clamp((Math.log10(Math.max(x, 1e-4)) + 4) * 25, 0, 100);
const KIND = { COMMIT: "commit", PROBE: "probe", REPROMPT: "probe", ESCALATE: "escalate", REFUSE: "escalate" };
const fmtSlot = (k, v, c) => (k === "amount" ? inr(v) : k === "party" ? nameOf(c, v) : k === "intent" ? (INTENT[v] ?? v) : v ?? "—");

function turnHtml(it, c) {
  if (it.k === "sys") {
    return `<div class="turn sys"><div class="who">Lexi</div><div class="said">“${esc(it.text)}”<span class="exp">${esc(it.expects)}${it.probe ? " · " + esc(it.probe) : ""}</span></div></div>`;
  }
  const r = it.r;
  const ents = r.entities.filter((e) => e.type !== "CUE").map((e) => `<div class="ent"><div class="t">${esc(e.type)}<em>${esc(e.surface)}</em></div>${
    e.candidates.map((x) => `<div class="cand" style="--p:${(x.p * 100).toFixed(0)}%"><b>${esc(typeof x.value === "number" ? inr(x.value) : /^P\d+$/.test(x.value) ? nameOf(c, x.value) : x.value)}</b><span>${x.p.toFixed(2)}</span></div>`).join("")}</div>`).join("");
  const slots = Object.entries(r.slots).map(([k, v]) => `${k} ${esc(fmtSlot(k, v.top, c))} <span class="faint">${v.p.toFixed(2)}</span>`).join(" · ");
  return `<div class="turn"><div class="who">Caller<br>${r.stage ? "after probe " + r.stage : "first turn"}</div><div>
    <div class="words">${r.words.map((w) => `<span class="w ${confCls(w.conf)}" title="confidence ${w.conf ?? "missing"}">${esc(w.w)}</span>`).join("")}</div>
    <div class="lattice">${ents}</div><div class="said" style="font-size:14px;margin-top:8px">${slots}</div></div></div>`;
}

function meterHtml(r) {
  const d = r.decision;
  const text = d.lam == null ? `no certified λ — ${esc(d.reason)}` : `λ = ${lam(d.lam)} · RG${d.rung}${d.hedged ? " · hedged" : ""}`;
  return `<div class="meter"><div class="row"><span class="big">s = ${lam(r.s)}</span><span class="mono dim">${text} · ${esc(r.cell ?? "no cell")}</span>
    <span class="stamp ${KIND[d.kind]} in" style="margin-left:auto">${esc(d.kind)}</span></div>
    <div class="scale" style="--lam:${d.lam == null ? 0 : logPos(d.lam)}%;--s:${logPos(r.s)}%"><i class="lamtick"></i><i class="needle"></i></div>
    <div class="axis"><span>10⁻⁴</span><span>10⁻³</span><span>10⁻²</span><span>10⁻¹</span><span>1</span></div></div>`;
}

function entryHtml(c) {
  const e = c.entry;
  if (!e) return "";
  const done = e.reached.has("POSTED"), undone = e.reached.has("COMPENSATED") || e.reached.has("REJECTED");
  const steps = ["PREPARED", "DELIVERED", "POSTED"].map((s) => `<span class="${e.reached.has(s) ? "on" : ""}">${s}</span>`).join("");
  const stamp = done ? `<span class="stamp posted in">Posted</span>` : undone ? `<span class="stamp undone in">${e.reached.has("REJECTED") ? "Rejected" : "Undone"}</span>` : "";
  const bar = !done && !undone && e.window_deadline ? `<div class="undo"><i data-deadline="${e.window_deadline}"></i></div>` : "";
  return `<div class="entry"><div class="line"><span>${esc(INTENT[e.intent] ?? e.intent)} · ${esc(nameOf(c, e.party_id))}${e.date ? " · due " + esc(e.date) : ""} · ${esc(e.provenance)}</span><span class="amt">${inr(e.amount_inr)}</span></div>
    <div class="progress">${steps}${stamp}</div>${bar}</div>`;
}

RENDER.s1 = () => {
  const t = S.tally;
  const hook = t?.intercepts?.[0];
  $("#headline").innerHTML = hook
    ? `Same call, same transcript. The baseline posts <em>${inr(hook.ghost.amount)}</em> to ${esc(hook.ghost.party_name ?? hook.ghost.party)}. LEXI ${hook.final === "COMMIT" ? `asks first and posts <em>${inr(hook.lexi?.amount)}</em>` : "sends it to a person"}.`
    : t?.calls ? `${t.calls} calls so far. LEXI has made ${t.wrong} wrong commits; the baseline has made ${t.baseline.wrong}.` : "Waiting for traffic. Start the stream on Scenario control.";

  $("#calls").innerHTML = S.order.slice(-14).map((id) => {
    const c = S.calls.get(id);
    const k = !c.done ? "live" : c.sum.wrong ? "wrong" : c.sum.final === "COMMIT" ? "commit" : "escalate";
    return `<span class="callchip ${k}${id === S.sel ? " on" : ""}" data-id="${esc(id)}">${esc(id.slice(-5))}</span>`;
  }).join("");

  const c = S.calls.get(S.sel);
  const box = $("#callbox");
  if (!c) box.innerHTML = `<div class="empty">No call yet. Start the stream on Scenario control.</div>`;
  else {
    const g = S.gold.get(c.id), r = c.latest;
    const intended = g ? `${INTENT[g.gold_intent] ?? g.gold_intent} · ${nameOf(c, g.gold_slots.party)}${g.gold_slots.amount ? " · " + inr(g.gold_slots.amount) : ""}${g.gold_slots.date ? " · " + g.gold_slots.date : ""}` : "";
    box.innerHTML = `<div class="callhead"><span class="who">${esc(intended || c.id)}</span>${r ? `<span class="cqi ${esc(r.cqi)}">${esc(r.cqi)}</span><span class="mono dim">${r.snr_db.toFixed(1)} dB</span>` : ""}
      ${g ? `<span class="mono dim">${esc(g.noise ?? "clean")} · ${esc(g.codec ?? "wb")}${g.tags.length ? " · " + esc(g.tags.join(", ")) : ""}</span>` : ""}<span class="mono faint" style="margin-left:auto">intended entry · ${esc(c.id)}</span></div>
      ${c.items.map((it) => turnHtml(it, c)).join("")}${r ? meterHtml(r) : ""}${entryHtml(c)}`;
  }

  $("#hook").classList.toggle("on", !!hook);
  if (hook) {
    $("#hook").innerHTML = `<h4>Interception · ${esc(hook.cell)} · ${hook.probes} probe${hook.probes === 1 ? "" : "s"}</h4><div class="eq">
      <div><div class="dim">baseline posts</div><div class="num a">${inr(hook.ghost.amount)}</div><div class="mono dim">${esc(hook.ghost.party_name ?? hook.ghost.party)} · ${esc(INTENT[hook.ghost.intent] ?? hook.ghost.intent)}</div></div><div class="num">→</div>
      <div><div class="dim">${hook.final === "COMMIT" ? "LEXI posts" : "LEXI escalates"}</div><div class="num b">${hook.final === "COMMIT" ? inr(hook.lexi?.amount) : "to a person"}</div><div class="mono dim">${esc(hook.lexi_party_name ?? "")}</div></div></div>`;
  }

  $("#ghost").innerHTML = (t?.ghost ?? []).slice(0, 12).map((g) => `<div class="ghostline ${g.wrong ? "wrong" : "ok"}"><span><span class="tag">${g.wrong ? "wrong post" : "correct"}</span> ${esc(g.party)} · ${esc(INTENT[g.intent] ?? g.intent)}${g.date ? " · " + esc(g.date) : ""}</span>
    <span class="amt">${inr(g.amount)}</span><span class="sub">LEXI: ${g.lexi === "COMMIT" ? `posted ${inr(g.lexi_amount)} to ${esc(g.lexi_party)}` : "sent to a person"}</span></div>`).join("") || `<div class="empty">The ghost ledger fills as calls finish.</div>`;
  $("#counters").innerHTML = t ? `<div><div class="lab">LEXI wrong commits</div><div class="val lexi">${t.wrong}</div><small>of ${t.autonomous} committed</small></div>
    <div><div class="lab">Baseline wrong commits</div><div class="val ghost">${t.baseline.wrong}</div><small>of ${t.baseline.commits} posted</small></div>` : "";
};

document.addEventListener("click", (e) => {
  const chip = e.target.closest(".callchip");
  if (chip) { S.sel = chip.dataset.id; S.follow = chip.dataset.id === S.order.at(-1); mark("s1"); }
});

/* ---------------------------------------------------------------- screen 2: certificate board */
const cellParts = (c) => c.split(".");
function plotFor(cell, host, height, big) {
  const w = host.clientWidth;
  if (!w || typeof uPlot !== "function") return null;  // a browser whose locale uPlot rejects still gets the board, just without charts
  let p = S.plots[host.id || cell];
  if (p && p.root.parentNode === host) return p;
  host.innerHTML = "";
  const draw = (u) => {
    const t = S.sentinel[cell]?.thresholds;
    if (!t) return;
    const { ctx } = u;
    ctx.save();
    ctx.lineWidth = 1; ctx.setLineDash([4, 4]);
    for (const [v, col] of [[t.m1.watch, "--watch"], [t.m1.alert, "--alert"], [t.m1.revoke, "--revoked"]]) {
      const y = u.valToPos(v, "y", true);
      ctx.strokeStyle = css(col);
      ctx.beginPath(); ctx.moveTo(u.bbox.left, y); ctx.lineTo(u.bbox.left + u.bbox.width, y); ctx.stroke();
    }
    ctx.restore();
  };
  p = new uPlot({
    width: w, height, padding: [4, big ? 8 : 4, 0, 0], legend: { show: false }, cursor: { show: !!big },
    scales: { x: { time: true }, y: { distr: 3, range: () => [0.3, 3e5] } },
    axes: big ? [{ stroke: css("--paper-faint"), grid: { stroke: css("--line-soft") } }, { stroke: css("--paper-faint"), grid: { stroke: css("--line-soft") }, size: 56 }] : [{ show: false }, { show: false }],
    series: [{}, { stroke: css("--srm1"), width: 1.6 }, { stroke: css("--srm2"), width: 1.6 }], hooks: { draw: [draw] },
  }, [[], [], []], host);
  S.plots[host.id || cell] = p;
  return p;
}
const plotData = (cell) => {
  const tr = S.traces[cell] ?? [];
  return [tr.map((r) => r[0]), tr.map((r) => Math.max(r[1], 0.31)), tr.map((r) => Math.max(r[2], 0.31))];
};

function ensureCard(cell) {
  let el = $(`.cell[data-cell="${cell}"]`);
  if (el) return el;
  const [lang, cqi, tier] = cellParts(cell);
  el = document.createElement("div");
  el.className = "cell box NONE";
  el.dataset.cell = cell;
  el.innerHTML = `<div class="top"><span class="name">${lang}<span>.</span>${cqi}<span>.</span>${tier}</span><span class="state"></span></div>
    <div class="lam"><b></b><small></small><span class="rung"></span></div><div class="sentence"></div>
    <div class="stats"><div><small>accepted n</small><b class="n">—</b></div><div><small>errors</small><b class="e">—</b></div><div><small>p-value</small><b class="p">—</b></div></div>
    <div class="spark"></div><div class="foot"><span><i style="background:var(--srm1)"></i>SRM1</span><span><i style="background:var(--srm2)"></i>SRM2</span><span class="m"></span></div>`;
  $("#board").append(el);
  return el;
}

function cellView(cell) {
  const e = S.registry?.cells[cell];
  const eff = e?.effective;
  const row = e && eff?.rung != null ? e.ladder.find((l) => l.rung === eff.rung) : null;
  const st = e?.ladder.length ? e.state : "NONE";  // a cell that never earned a certificate has nothing to be healthy about
  return { e, eff, row, st };
}

function sentence({ e, eff, row, st }) {
  if (st === "NONE") return ["No certificate yet. Every call in this cell goes to a person.", true];
  if (st === "REVOKED") return ["Revoked. No autonomous commits here until a new certificate is earned.", true];
  if (eff.hedged) return [`Stricter rung unavailable, so λ is tightened. Wrong-action rate ≤ ${pct(e.ladder[0]?.alpha, 0)} still bounds RG0.`, false];
  if (!row) return ["No certified λ in force.", true];
  return [`wrong-action rate ≤ ${pct(row.alpha, 1)} with probability ≥ ${pct(1 - row.delta, 1)}`, false];
}

RENDER.s2 = () => {
  $("#reg-line").textContent = S.registry ? `registry v${S.registry.version} · scorer ${S.registry.scorer_version}` : "no registry";
  for (const cell of S.catalog.cells) {
    const el = ensureCard(cell);
    const v = cellView(cell), s = S.sentinel[cell];
    el.className = `cell box ${v.st}`;
    $(".state", el).textContent = v.st === "NONE" ? "no certificate" : v.st;
    $(".lam b", el).textContent = lam(v.eff?.lam);
    $(".lam small", el).textContent = v.eff?.lam == null ? "" : "λ";
    const rung = $(".rung", el);
    rung.textContent = v.eff?.rung == null ? "—" : `RG${v.eff.rung}${v.eff.hedged ? " hedged" : ""}`;
    rung.className = "rung" + (v.eff?.hedged ? " hedged" : "");
    const [text, none] = sentence(v);
    const sen = $(".sentence", el);
    sen.textContent = text; sen.className = "sentence" + (none ? " none" : "");
    $(".n", el).textContent = v.row?.n ?? "—";
    $(".e", el).textContent = v.row?.errors ?? "—";
    $(".p", el).textContent = v.row ? v.row.p.toExponential(1) : "—";
    $(".m", el).textContent = s ? `SRM1 ${s.srm1.toFixed(1)} · SRM2 ${s.srm2.toFixed(1)}` : "";
    const p = plotFor(cell, $(".spark", el), 54, false);
    p?.setData(plotData(cell));
  }
};
$("#board").addEventListener("click", (e) => { const c = e.target.closest(".cell"); if (c) openCell(c.dataset.cell); });

function openCell(cell) { S.open = cell; $("#drawer").classList.add("on"); delete S.plots["dchart"]; RENDER.open(); }
RENDER.open = () => {
  const cell = S.open;
  if (!cell) return;
  const v = cellView(cell), s = S.sentinel[cell], cs = S.cellstats?.cells[cell];
  const drawer = $("#drawer");
  if (!$("#dchart", drawer)) {
    drawer.innerHTML = `<button class="btn sm close" id="dclose">Close</button><h3></h3><div class="mono dim" id="dsub"></div><div id="dbody"></div>
      <div class="legend"><span><i style="background:var(--srm1)"></i>SRM1 (calibration of commits)</span><span><i style="background:var(--srm2)"></i>SRM2 (first-pass risk)</span></div><div class="chart" id="dchart"></div>
      <p class="dim" style="font-size:12px">Dashed lines: WATCH, ALERT, REVOKE. Log scale.</p>`;
    $("#dclose").onclick = () => { S.open = null; drawer.classList.remove("on"); };
  }
  $("h3", drawer).textContent = cell;
  $("#dsub").textContent = `${v.st}${v.e ? ` · registry v${S.registry.version}` : ""}`;
  const ladder = (v.e?.ladder ?? []).map((l) => `<tr class="${l.rung === v.eff?.rung ? "eff" : ""}"><td>RG${l.rung}</td><td>${pct(l.alpha, 2)}</td><td>${lam(l.lam)}</td><td>${l.n}</td><td>${l.errors}</td><td>${l.p.toExponential(2)}</td><td>${l.cert_id.slice(-8)}</td></tr>`).join("");
  $("#dbody").innerHTML = `<table><tr><th>rung</th><th>α</th><th>λ</th><th>n</th><th>errors</th><th>p</th><th>cert</th></tr>${ladder || `<tr><td colspan="7" class="dim">No certificate issued.</td></tr>`}</table>
    <dl class="kv">${s ? `<dt>SRM1 / SRM2</dt><dd>${s.srm1.toFixed(2)} / ${s.srm2.toFixed(2)}</dd><dt>labeled commits seen (M1)</dt><dd>${s.n_m1}</dd><dt>first-pass decisions seen (M2)</dt><dd>${s.n_m2}</dd>
      <dt>reference scores</dt><dd>${s.reference_n}</dd><dt>calm decisions</dt><dd>${s.calm}</dd><dt>label age</dt><dd>${s.label_age_s.toFixed(0)} s${s.stale ? " · stale" : ""}</dd>` : ""}
      ${cs ? `<dt>Calib records</dt><dd>${cs.n_records}</dd><dt>deficit by rung</dt><dd>${cs.deficit_by_rung.join(" / ")}</dd><dt>expected acceptance</dt><dd>${cs.est_accept_by_rung.map((x) => x.toFixed(0)).join(" / ")}</dd>` : ""}</dl>`;
  const p = plotFor(cell, $("#dchart"), 190, true);
  p?.setData(plotData(cell));
};

/* ---------------------------------------------------------------- screen 3: risk and autonomy */
RENDER.s3 = () => {
  const t = S.tally;
  if (!t) return;
  const b = t.baseline;
  const kpi = (cls, lab, val, sub, tag, tagCls) => `<div class="kpi box ${cls}"><div class="lab">${lab}</div>${tag ? `<span class="tag ${tagCls}">${tag}</span>` : ""}<div class="val">${val}</div><div class="sub">${sub}</div></div>`;
  $("#kpis").innerHTML =
    kpi("lexi", "Autonomy rate", pct(t.autonomy_rate), `${t.autonomous} of ${t.calls} calls committed without a person. Baseline: ${pct(b.autonomy_rate)}.`) +
    kpi("lexi", "Wrong-commit rate", pct(t.wrong_rate, 2), `${t.wrong} wrong of ${t.autonomous} autonomous commits. Baseline: ${pct(b.wrong_rate, 2)} (${b.wrong} of ${b.commits}).`, "gold labels", "gold") +
    kpi("gold", "CELB-cap", inr(t.celb.cap), `Σ α × ${inr(t.celb.cap_inr)} over ${t.celb.commits} certified commits. A valid upper bound on expected loss.`, "bound", "bound") +
    kpi("gold", "CELB-est", inr(t.celb.est), `Σ α × the amount actually committed. An estimate, not a bound.`, "estimate", "est") +
    kpi("ghost", "Rupees posted wrong", `${inr(t.inr.lexi_wrong)}<small>LEXI</small>`, `Baseline: ${inr(t.inr.base_wrong)}. Largest single error: LEXI ${inr(t.inr.max_loss_lexi)}, baseline ${inr(t.inr.max_loss_baseline)}.`);
  const row = (k, l, g) => `<tr><td>${k}</td><td class="l">${l}</td><td class="g">${g}</td></tr>`;
  $("#vs").innerHTML = `<tr><th></th><th>LEXI</th><th>Baseline</th></tr>` +
    row("Calls finished", t.calls, t.calls) + row("Committed autonomously", t.autonomous, b.commits) + row("Wrong commits", t.wrong, b.wrong) +
    row("Wrong-commit rate", pct(t.wrong_rate, 2), pct(b.wrong_rate, 2)) + row("Rupees posted wrong", inr(t.inr.lexi_wrong), inr(t.inr.base_wrong)) +
    row("Largest single error", inr(t.inr.max_loss_lexi), inr(t.inr.max_loss_baseline)) + row("Sent to a person", t.escalated, "0") +
    row("Wrong baseline posts LEXI stopped", t.intercepted, "—");
  $("#balances").innerHTML = `<tr><th>Party</th><th>LEXI ledger</th><th>Baseline ghost</th><th>Difference</th></tr>` +
    (Object.entries(t.net).map(([k, v]) => `<tr class="${v.lexi !== v.ghost ? "diff" : ""}"><td>${esc(S.names[k] ?? k)}</td><td>${inr(v.lexi)}</td><td>${inr(v.ghost)}</td><td>${inr(v.ghost - v.lexi)}</td></tr>`).join("") ||
      `<tr><td colspan="4" class="dim">No entries yet.</td></tr>`);
  const host = $("#cum");
  const w = host.clientWidth;
  if (w) {
    if (!S.cum || S.cum.root.parentNode !== host) {
      host.innerHTML = "";
      S.cum = new uPlot({ width: w, height: 190, legend: { show: false }, cursor: { show: false }, scales: { x: { time: false } },
        axes: [{ stroke: css("--paper-faint"), grid: { stroke: css("--line-soft") } }, { stroke: css("--paper-faint"), grid: { stroke: css("--line-soft") } }],
        series: [{}, { stroke: css("--lexi"), width: 2, paths: uPlot.paths.stepped({ align: 1 }) }, { stroke: css("--ghost"), width: 2, paths: uPlot.paths.stepped({ align: 1 }) }] }, [[], [], []], host);
    }
    const s = t.series;
    S.cum.setData([s.map((r) => r[0]), s.map((r) => r[1]), s.map((r) => r[2])]);
  }
};
$("#reset").onclick = () => post("/api/tally/reset").then(() => toast("Counters reset", "ok"));

/* ---------------------------------------------------------------- screen 4: escalation queue */
const T_INTENTS = Object.keys(INTENT);
RENDER.s4 = () => {
  const list = [...S.tickets.values()];
  $("#tickets").innerHTML = list.map((t) => `<div class="tk${t.ticket_id === S.ticket ? " on" : ""}" data-id="${esc(t.ticket_id)}"><div class="r">${esc(t.reason_code)}</div>
    <div class="txt">${esc(t.evidence_summary.text ?? [t.evidence_summary.intent, t.evidence_summary.party, t.evidence_summary.amount].filter(Boolean).join(" · "))}</div><small>${esc(t.cell ?? "no cell")} · ${esc(t.session_id.slice(-5))}</small></div>`).join("") || `<div class="empty">Queue empty.</div>`;
  const t = S.tickets.get(S.ticket);
  const box = $("#ticket");
  if (!t) { box.innerHTML = `<div class="empty">Select a ticket.</div>`; S.form = null; return; }
  if (S.form?.id !== t.ticket_id) {
    const top = (slot) => t.candidates[slot]?.[0]?.[0];
    S.form = { id: t.ticket_id, intent: top("intent") ?? T_INTENTS[0], party: top("party") ?? "", amount: top("amount") ?? "", date: top("date") ?? "" };
  }
  const c = S.calls.get(t.session_id);
  const parties = c?.view?.parties ?? [];
  const opts = (slot, fmt) => (t.candidates[slot] ?? []).map(([v, p]) => `<button class="opt${String(S.form[slot]) === String(v) ? " on" : ""}" data-slot="${slot}" data-v="${esc(v)}">${esc(fmt(v))}<small>${p.toFixed(2)}</small></button>`).join("");
  box.innerHTML = `<div class="callhead"><span class="who">${esc(t.evidence_summary.text ?? "Ledger rejected an entry")}</span><span class="mono dim">${esc(t.reason_code)}${t.evidence_summary.cqi ? " · " + esc(t.evidence_summary.cqi) : ""}</span></div>
    <div class="slot"><div class="lab">Intent</div><div class="opts">${T_INTENTS.map((i) => `<button class="opt${S.form.intent === i ? " on" : ""}" data-slot="intent" data-v="${i}">${INTENT[i]}</button>`).join("")}</div></div>
    <div class="slot"><div class="lab">Party</div><div class="opts">${opts("party", (v) => nameOf(c, v))}</div>
      <div class="row" style="margin-top:8px"><select id="f-party"><option value="">choose…</option>${parties.map((p) => `<option value="${esc(p.id)}"${S.form.party === p.id ? " selected" : ""}>${esc(p.display)}</option>`).join("")}</select></div></div>
    <div class="slot"><div class="lab">Amount (₹)</div><div class="opts">${opts("amount", inr)}</div><div class="row" style="margin-top:8px"><input type="number" id="f-amount" min="1" value="${esc(S.form.amount)}"></div></div>
    ${["PROMISE_TO_PAY", "RESCHEDULE_DUE"].includes(S.form.intent) ? `<div class="slot"><div class="lab">Date</div><div class="opts">${opts("date", (v) => v)}</div><div class="row" style="margin-top:8px"><input type="date" id="f-date" value="${esc(S.form.date)}"></div></div>` : ""}
    <div class="slot"><button class="btn primary" id="resolve">Commit as operator</button></div>`;
};
$("#tickets").addEventListener("click", (e) => { const t = e.target.closest(".tk"); if (t) { S.ticket = t.dataset.id; S.form = null; mark("s4"); } });
$("#ticket").addEventListener("click", (e) => {
  const o = e.target.closest(".opt");
  if (o) { S.form[o.dataset.slot] = o.dataset.slot === "amount" ? +o.dataset.v : o.dataset.v; mark("s4"); }
  if (e.target.id === "resolve") resolve();
});
$("#ticket").addEventListener("input", (e) => {
  const k = { "f-party": "party", "f-amount": "amount", "f-date": "date" }[e.target.id];
  if (k) S.form[k] = e.target.value;
});
async function resolve() {
  const f = S.form;
  if (!f.party || !f.amount) return toast("Choose a party and an amount first", "bad");
  const body = { intent: f.intent, party_id: f.party, amount_inr: +f.amount, date: ["PROMISE_TO_PAY", "RESCHEDULE_DUE"].includes(f.intent) ? f.date || null : null };
  const r = await post(`/api/tickets/${encodeURIComponent(f.id)}/resolve`, body);
  toast(r.status ? `Entry ${r.status.toLowerCase()} (human)` : `Rejected: ${JSON.stringify(r.detail ?? r)}`, r.status && r.status !== "REJECTED" ? "ok" : "bad");
}

/* ---------------------------------------------------------------- screen 5: scenario control */
async function pollTwin() {
  try { S.twin = await (await fetch("/api/twin")).json(); } catch { S.twin = null; }
  if (S.screen === "s5") RENDER.s5();
}
setInterval(() => { if (S.screen === "s5") pollTwin(); }, 2000);

RENDER.s5 = () => {
  $("#scn").innerHTML = SCENARIOS.map(([id, name, sub]) => `<button data-id="${id}" class="${id === "S4" ? "rec " : ""}${S.twin?.scenario === id || (!S.twin?.running && S.scn === id) ? "on" : ""}"><b>${name}</b><small>${sub}</small></button>`).join("");
  if (!S.speedSet && S.clock.speed) { $("#speed").value = S.clock.speed; S.speedSet = true; }
  const w = S.twin;
  $("#twin").innerHTML = !w || w.reachable === false ? `<dt>Twin</dt><dd class="dim">not running. Start it with <span class="mono">python -m lexi.cli twin</span></dd>` :
    `<dt>Stream</dt><dd>${w.running ? "running · " + w.scenario : "stopped"}</dd><dt>Rate</dt><dd>${w.rate_per_min ?? 0} calls / min</dd><dt>Time ×</dt><dd>${w.speed}</dd><dt>In flight</dt><dd>${w.in_flight}</dd>
     <dt>Calls done / failed</dt><dd>${w.completed ?? 0} / ${w.failed ?? 0}</dd><dt>Credits spent</dt><dd>${(w.credits?.spent ?? 0).toFixed(1)} of ${Number.isFinite(w.credits?.usable) ? w.credits.usable.toFixed(0) : "∞"}</dd>
     <dt>Canary rate</dt><dd>${Object.entries(w.canary_rate ?? {}).map(([c, r]) => `${c} ${r}/min`).join(", ") || "idle"}</dd>`;
  const speedNote = $("#speed-note");
  speedNote.textContent = +$("#speed").value !== S.clock.speed ? `The server clock runs ×${S.clock.speed}. Start the server with --speed ${$("#speed").value} so call time, lease and undo window agree with the twin.` : "";
  const sick = S.catalog.cells.filter((c) => S.registry?.cells[c]?.ladder.length && ((S.registry.cells[c].state !== "HEALTHY") || S.sentinel[c]?.pending_cert));
  $("#recert").innerHTML = sick.map((c) => {
    const cs = S.cellstats?.cells[c];
    const tot = cs ? cs.n_records + cs.deficit_by_rung[0] : 0;
    const w2 = cs && tot ? (cs.n_records / tot) * 100 : 0;
    return `<div class="recert"><div class="t"><span>${esc(c)} · ${esc(S.registry?.cells[c]?.state ?? "NONE")}</span><span>${cs ? `${cs.n_records} / ${tot}` : "—"}</span></div><div class="bar" style="--w:${w2}%"><i></i></div></div>`;
  }).join("") || `<div class="empty">Every certificate is in force.</div>`;
};
$("#scn").addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  S.scn = b.dataset.id;
  if (S.twin?.running) await command("INJECT");
  else mark("s5");
});
$("#go").onclick = () => command("START");
$("#stop").onclick = () => command("STOP");
$("#speed").addEventListener("input", () => mark("s5"));
async function command(action) {
  const r = await post("/api/twin/command", { action, scenario: S.scn, params: { rate: +$("#rate").value, speed: +$("#speed").value } });
  if (r.error) toast(r.error, "bad"); else toast(action === "STOP" ? "Stream stopped" : `${action === "INJECT" ? "Injected" : "Started"} ${S.scn}`, "ok");
  S.twin = r; mark("s5");
}

/* ---------------------------------------------------------------- screen 6: eval */
const val = (v) => (v === null || v === undefined ? "—" : typeof v === "number" ? (Number.isInteger(v) ? v : +v.toPrecision(4)) : typeof v === "object" && v !== null ? JSON.stringify(v) : String(v));
async function loadReports() {
  const list = await (await fetch("/api/reports")).json();
  const pick = $("#report-pick");
  pick.innerHTML = list.map((r) => `<option value="${esc(r.tag)}">${esc(r.tag)}${r.passed ? " ✓" : ""}</option>`).join("");
  if (list.length) showReport(list[0].tag);
}
/* one small chart per cell: x = share of calls committed, y = share of those committed that were wrong; points are [lambda, coverage, risk] */
function riskCoverage(cell, pts) {
  const top = Math.max(0.02, ...pts.map((p) => p[2])), W = 150, H = 72;
  const xy = pts.map((p) => `${(p[1] * W).toFixed(1)},${(H - (p[2] / top) * (H - 6) - 3).toFixed(1)}`).join(" ");
  return `<figure><svg viewBox="0 0 ${W} ${H}"><line x1="0" y1="${H - 3}" x2="${W}" y2="${H - 3}"/><polyline points="${xy}"/></svg><figcaption><span>${esc(cell)}</span><span>max ${(top * 100).toFixed(1)}%</span></figcaption></figure>`;
}
async function showReport(tag) {
  const r = await (await fetch(`/api/reports/${encodeURIComponent(tag)}`)).json();
  const rows = r.slo_table.map((x) => `<tr><td>${esc(x.id)}</td><td>${esc(x.cls)}</td><td>${esc(x.desc)}</td><td>${esc(x.op)} ${esc(val(x.target))}</td><td>${esc(val(x.value))}</td><td class="${x.pass === null ? "na" : x.pass ? "pass" : "fail"}">${x.pass === null ? "not measured" : x.pass ? "pass" : "fail"}</td></tr>`).join("");
  const curves = Object.entries(r.metrics.risk_coverage || {}).map(([cell, pts]) => riskCoverage(cell, pts)).join("");
  const groups = Object.entries(r.metrics).filter(([g, v]) => g !== "risk_coverage" && typeof v === "object" && v !== null && !Array.isArray(v)).map(([g, m]) => `<div class="box"><h4>${esc(g)}</h4><dl>${Object.entries(m).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(val(v))}</dd>`).join("")}</dl></div>`).join("");
  $("#eval").innerHTML = `<div class="box"><div class="cap">${esc(r.release_tag)}<span class="right">${r.passed ? "all MUST criteria pass" : "a MUST criterion fails"}</span></div>
    <table class="slo"><tr><th>id</th><th>class</th><th>criterion</th><th>target</th><th>observed</th><th></th></tr>${rows}</table></div><div class="metrics">${groups}</div>${curves ? `<div class="box" style="margin-top:16px"><div class="cap">Selective risk against coverage<span class="right">Test split, one curve per cell</span></div><div class="rc">${curves}</div></div>` : ""}`;
}
$("#report-pick").onchange = (e) => showReport(e.target.value);
RENDER.s6 = loadReports;

/* ---------------------------------------------------------------- screen 7: softphone */
const WORKLET = `class Tap extends AudioWorkletProcessor {
  constructor() { super(); this.ratio = sampleRate / 8000; this.pos = 0; this.out = new Int16Array(160); this.n = 0; this.sum = 0; this.cnt = 0; }
  process(inputs) {
    const ch = inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      this.sum += ch[i]; this.cnt++; this.pos++;
      if (this.pos >= this.ratio) {
        this.pos -= this.ratio;
        this.out[this.n++] = Math.max(-1, Math.min(1, this.sum / this.cnt)) * 32767;
        this.sum = 0; this.cnt = 0;
        if (this.n === 160) { this.port.postMessage(this.out.slice().buffer); this.n = 0; }
      }
    }
    return true;
  }
}
registerProcessor("tap", Tap);`;
const phone = { ws: null, ctx: null, stream: null, next: 0, playing: [] };

RENDER.s7 = () => {
  const host = $("#phone");
  if (phone.ws) return;
  if (S.providers === "sim") {
    host.innerHTML = `<div class="big">Simulation mode</div><p class="dim">The simulated speech recogniser hears the caller twin's script, not audio, so a microphone call cannot be understood.
      Set <span class="mono">providers.mode: live</span> in <span class="mono">config/runtime.yaml</span> and put your key in <span class="mono">.env</span> to speak to the agent from here.</p>`;
    return;
  }
  host.innerHTML = `<div class="big">Call the agent</div><div class="row" style="margin:14px 0">
    <label class="dim">Account <select id="ph-acc">${S.accounts.map((a) => `<option value="${esc(a.id)}" data-lang="${esc(a.lang)}">${esc(a.id)} · ${esc(a.lang)} · ${esc(a.parties.slice(0, 3).join(", "))}</option>`).join("")}</select></label>
    <button class="btn primary" id="ph-go">Call</button></div><div class="meterbar"><i id="ph-level"></i></div><div class="said" id="ph-said">&nbsp;</div><div class="mono dim" id="ph-state"></div>`;
  $("#ph-go").onclick = dial;
};

async function dial() {
  const sel = $("#ph-acc"), acc = sel.value, lang = sel.selectedOptions[0].dataset.lang;
  const id = "web-" + Math.random().toString(36).slice(2, 8);
  try {
    phone.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 } });
  } catch { return toast("Microphone permission is needed", "bad"); }
  phone.ctx = new AudioContext();
  await phone.ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([WORKLET], { type: "text/javascript" })));
  phone.ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/call?session_id=${id}&account=${encodeURIComponent(acc)}&lang=${lang}&client=softphone`);
  phone.ws.binaryType = "arraybuffer";
  const tap = new AudioWorkletNode(phone.ctx, "tap");
  phone.ctx.createMediaStreamSource(phone.stream).connect(tap);
  tap.port.onmessage = (e) => {
    if (phone.ws?.readyState === 1) phone.ws.send(e.data);
    const pcm = new Int16Array(e.data);
    let sum = 0;
    for (const v of pcm) sum += v * v;
    $("#ph-level").style.width = clamp(Math.sqrt(sum / pcm.length) / 6000) * 100 + "%";
  };
  phone.ws.onmessage = (e) => {
    if (typeof e.data === "string") {
      const m = JSON.parse(e.data);
      if (m.type === "stop") { phone.playing.forEach((s) => s.stop()); phone.playing = []; phone.next = 0; }
      else if (m.text) $("#ph-said").textContent = `“${m.text}”`;
      return;
    }
    const pcm = new Int16Array(e.data), buf = phone.ctx.createBuffer(1, pcm.length, 8000);
    buf.copyToChannel(Float32Array.from(pcm, (v) => v / 32768), 0);
    const src = phone.ctx.createBufferSource();
    src.buffer = buf; src.connect(phone.ctx.destination);
    phone.next = Math.max(phone.ctx.currentTime, phone.next);
    src.start(phone.next); phone.next += buf.duration; phone.playing.push(src);
    src.onended = () => (phone.playing = phone.playing.filter((s) => s !== src));
  };
  phone.ws.onclose = hangup;
  $("#ph-go").textContent = "Hang up";
  $("#ph-go").onclick = () => phone.ws?.close();
  $("#ph-state").textContent = `session ${id}`;
  S.follow = true; S.holdUntil = 0;
}
function hangup() {
  phone.stream?.getTracks().forEach((t) => t.stop());
  phone.ctx?.close();
  Object.assign(phone, { ws: null, ctx: null, stream: null, next: 0, playing: [] });
  RENDER.s7();
}

/* ---------------------------------------------------------------- end card */
RENDER.end = () => {
  const t = S.tally;
  if (!t) { $("#endcard").innerHTML = `<button class="btn close" id="eclose">Close</button><h1>No calls yet.</h1>`; $("#eclose").onclick = closeEnd; return; }
  const cell = (cls, lab, v, note) => `<div><div class="lab">${lab}</div><div class="val ${cls}">${v}</div><div class="note">${note}</div></div>`;
  $("#endcard").innerHTML = `<button class="btn close" id="eclose">Close</button>
    <h1>LEXI made <em>${t.wrong} wrong commit${t.wrong === 1 ? "" : "s"}</em>.<br>The baseline made ${t.baseline.wrong}.</h1>
    <div class="sub">${t.calls} calls · the same transcripts · numbers computed from the audit stream and gold labels</div>
    <div class="ec">
      ${cell("l", "Wrong commits, LEXI", t.wrong, `of ${t.autonomous} autonomous commits (${pct(t.wrong_rate, 2)})`)}
      ${cell("g", "Wrong commits, baseline", t.baseline.wrong, `of ${t.baseline.commits} posts (${pct(t.baseline.wrong_rate, 2)})`)}
      ${cell("l", "Autonomy rate", pct(t.autonomy_rate), `baseline ${pct(t.baseline.autonomy_rate)}`)}
      ${cell("y", "Wrong commits before containment", t.wrong_before_containment ?? "—", t.contained ? "between shift onset and the first REVOKED cell" : "no cell has been revoked yet")}
      ${cell("y", "CELB-cap", inr(t.celb.cap), "upper bound on expected loss, from certificates")}
      ${cell("y", "CELB-est", inr(t.celb.est), "estimate from committed amounts")}
    </div>
    <div class="chain">Audit chain head <b>#${S.head?.seq ?? "—"}</b> ${esc(S.head?.hash ?? "")}</div><div class="tags">#GnaniAI</div>`;
  $("#eclose").onclick = closeEnd;
};
const closeEnd = () => $("#endcard").classList.remove("on");
$("#open-end").onclick = () => { RENDER.end(); $("#endcard").classList.add("on"); };

/* ---------------------------------------------------------------- navigation */
$("#rail").addEventListener("click", (e) => { const b = e.target.closest("button[data-s]"); if (b) show(b.dataset.s); });
document.addEventListener("keydown", (e) => {
  if (e.target.matches("input, select")) return;
  if (e.key === "Escape") { closeEnd(); S.open = null; $("#drawer").classList.remove("on"); }
  if (/^[1-7]$/.test(e.key)) show("s" + e.key);
});
window.addEventListener("resize", () => { for (const k of Object.keys(S.plots)) { S.plots[k].destroy(); delete S.plots[k]; } S.cum?.destroy(); S.cum = null; mark("s2", "s3", "open"); });
pollTwin();
show("s1");
