// 节奏型模板管理前端：CRUD + 栅格可视化编辑 + Web Audio 试听。
// 纯 vanilla JS，无依赖。服务端只返回 JSON 音符列表，合成在浏览器。

const $ = (id) => document.getElementById(id);
const STATUS = $("status");
let current = null;      // 当前编辑的模板 {id, ...fields}
let templates = [];      // [{id, name, technique, ...}]
let audioCtx = null;
let playTimers = [];

function setStatus(msg) { STATUS.textContent = msg || ""; }

// ── API ──────────────────────────────────────────────────────────────
async function api(method, path, body) {
  const opt = { method, headers: {} };
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  const text = await r.text();
  const data = text ? JSON.parse(text) : null;
  if (!r.ok) throw new Error((data && data.error) || `HTTP ${r.status}`);
  return data;
}

async function loadList() {
  templates = (await api("GET", "/api/templates")).templates || [];
  renderTable();
  setStatus(`共 ${templates.length} 个模板`);
}

// ── 列表渲染 ─────────────────────────────────────────────────────────
function renderTable() {
  const q = $("search").value.trim().toLowerCase();
  const tbody = $("template-table").querySelector("tbody");
  tbody.innerHTML = "";
  for (const t of templates) {
    if (q && !t.name.toLowerCase().includes(q)) continue;
    const tr = document.createElement("tr");
    tr.dataset.id = t.id;
    if (current && current.id === t.id) tr.classList.add("active");
    tr.innerHTML = `<td>${esc(t.name)}</td><td>${esc(t.technique)}</td><td>${esc(String(t.motif_beats))}</td><td>${esc(t.style)}</td>`;
    tr.onclick = () => selectTemplate(t.id);
    tbody.appendChild(tr);
  }
}

function esc(s) { return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

// ── 模板 ↔ 编辑器表单 ────────────────────────────────────────────────
async function selectTemplate(id) {
  const t = await api("GET", `/api/templates/${encodeURIComponent(id)}`);
  current = t;
  fillForm();
  renderTable();
}

function fillForm() {
  const t = current;
  $("f-id").value = t.id;
  $("f-name").value = t.name;
  $("f-technique").value = t.technique;
  $("f-style").value = t.style;
  $("f-motif").value = t.motif_beats;
  $("f-min").value = t.min_beats;
  $("f-ideal").value = (t.ideal_beats || []).join(",");
  $("f-sections").value = (t.sections || []).join(",");
  $("f-positions").value = (t.positions || []).join(",");
  renderGrid(t.grid_motif || []);
}

// 把编辑器表单读回模板 dict（不含 id）
function readForm() {
  const motif = parseInt($("f-motif").value, 10) || 1;
  const grid = readGrid();
  return {
    name: $("f-name").value.trim(),
    technique: $("f-technique").value,
    style: $("f-style").value,
    motif_beats: motif,
    min_beats: parseInt($("f-min").value, 10) || motif,
    ideal_beats: parseCsvInt($("f-ideal").value),
    sections: parseCsv($("f-sections").value),
    positions: parseCsv($("f-positions").value),
    grid_motif: grid,
  };
}
function parseCsv(s) { return (s || "").split(",").map(x => x.trim()).filter(Boolean); }
function parseCsvInt(s) { return parseCsv(s).map(x => parseInt(x, 10)).filter(x => !isNaN(x)); }

// ── 栅格编辑器 ───────────────────────────────────────────────────────
// 内部表示每格: {type: 'stroke'|'rest'|'pluck', direction?, role?}
// role: null | {kind:'root|third|fifth|seventh', region} | {kind:'topn', n, span} | {kind:'all'}

const ROLE_KINDS_DEGREE = ["root", "third", "fifth", "seventh"];
const REGIONS = ["", "bass", "treble", "avoid_bass"];
const SPANS = ["", "comfortable", "narrow"];

// 把后端 grid_motif（cell dicts）转成内部表示
function cellsFromData(cells) {
  return cells.map(c => {
    const cell = { type: c.type, duration: c.duration || 1 };  // duration 默认 1 兼容旧存档
    if (c.type === "stroke") cell.direction = c.direction;
    if (c.type === "pluck") cell.role = c.role || null;
    return cell;
  });
}
function cellsToData(cells) {
  return cells.map(c => {
    const out = { type: c.type, duration: c.duration };
    if (c.type === "stroke") out.direction = c.direction;
    if (c.type === "pluck") out.role = c.role;
    return out;
  });
}

let gridCells = [];

function renderGrid(cells) {
  gridCells = cellsFromData(cells || []);
  paintGrid();
}
function readGrid() { return cellsToData(gridCells); }

function paintGrid() {
  const el = $("grid");
  el.innerHTML = "";
  gridCells.forEach((cell, i) => {
    el.appendChild(makeCellEl(cell, i));
  });
}

function makeCellEl(cell, idx) {
  const div = document.createElement("div");
  div.className = `cell t-${cell.type}${cell.type === "stroke" ? "-" + cell.direction : ""}`;
  div.dataset.idx = idx;

  // 类型切换按钮组
  const seg = document.createElement("div"); seg.className = "seg";
  const types = [
    ["D", "stroke", "D"], ["U", "stroke", "U"], [".", "rest", "rest"], ["P", "pluck", "pluck"]
  ];
  for (const [label, t, sub] of types) {
    const b = document.createElement("button");
    b.className = "t";
    b.textContent = label;
    b.dataset.t = sub;
    const active = (t === "stroke" && cell.type === "stroke" && cell.direction === sub)
                || (t === "rest" && cell.type === "rest")
                || (t === "pluck" && cell.type === "pluck");
    if (active) b.classList.add("active");
    b.onclick = () => {
      if (t === "stroke") { cell.type = "stroke"; cell.direction = sub; delete cell.role; }
      else if (t === "rest") { cell.type = "rest"; delete cell.direction; delete cell.role; }
      else { cell.type = "pluck"; delete cell.direction; if (!cell.role) cell.role = { kind: "root", region: null }; }
      paintGrid();
    };
    seg.appendChild(b);
  }
  div.appendChild(seg);

  // 拨弦角色编辑
  if (cell.type === "pluck") {
    div.appendChild(makeRoleEl(cell));
  }

  // 时值输入（占多少 16 分位置）。每个动作/休止自带 duration，逐格控制延续/断音。
  const durWrap = document.createElement("div"); durWrap.className = "dur-row";
  const durLbl = document.createElement("span"); durLbl.textContent = "时值"; durLbl.style.fontSize = "11px";
  const durInp = document.createElement("input"); durInp.type = "number"; durInp.min = 1; durInp.max = 16; durInp.value = cell.duration || 1;
  durInp.style.width = "42px";
  durInp.onchange = () => { cell.duration = Math.max(1, parseInt(durInp.value, 10) || 1); durInp.value = cell.duration; updateMotifSum(); };
  durWrap.appendChild(durLbl); durWrap.appendChild(durInp);
  div.appendChild(durWrap);

  const rm = document.createElement("button"); rm.className = "rm"; rm.textContent = "✕";
  rm.onclick = () => { gridCells.splice(idx, 1); paintGrid(); updateMotifSum(); };
  div.appendChild(rm);
  return div;
}

function makeRoleEl(cell) {
  const wrap = document.createElement("div"); wrap.className = "role-row";
  const role = cell.role || (cell.role = { kind: "root", region: null });

  const kindSel = document.createElement("select");
  for (const k of [...ROLE_KINDS_DEGREE, "topn", "all"]) {
    const o = document.createElement("option"); o.value = k; o.textContent = k;
    if (role && role.kind === k) o.selected = true;
    kindSel.appendChild(o);
  }
  kindSel.onchange = () => {
    const k = kindSel.value;
    if (ROLE_KINDS_DEGREE.includes(k)) cell.role = { kind: k, region: null };
    else if (k === "topn") cell.role = { kind: "topn", n: 2, span: null };
    else cell.role = { kind: "all" };
    paintGrid();
  };
  wrap.appendChild(kindSel);

  if (role) {
    if (ROLE_KINDS_DEGREE.includes(role.kind)) {
      const rSel = document.createElement("select");
      for (const rg of REGIONS) {
        const o = document.createElement("option"); o.value = rg; o.textContent = rg || "默认";
        if ((role.region || "") === rg) o.selected = true;
        rSel.appendChild(o);
      }
      rSel.onchange = () => { role.region = rSel.value || null; };
      wrap.appendChild(rSel);
    } else if (role.kind === "topn") {
      const nInp = document.createElement("input"); nInp.type = "number"; nInp.min = 1; nInp.max = 6; nInp.value = role.n || 2;
      nInp.style.width = "40px";
      nInp.onchange = () => { role.n = parseInt(nInp.value, 10) || 2; };
      wrap.appendChild(nInp);
      const sSel = document.createElement("select");
      for (const sp of SPANS) {
        const o = document.createElement("option"); o.value = sp; o.textContent = sp || "无";
        if ((role.span || "") === sp) o.selected = true;
        sSel.appendChild(o);
      }
      sSel.onchange = () => { role.span = sSel.value || null; };
      wrap.appendChild(sSel);
    }
  }
  return wrap;
}

// ── CRUD 操作 ────────────────────────────────────────────────────────
async function save() {
  if (!current) return alert("请先选择或新建模板");
  const dict = readForm();
  const id = current.id;
  try {
    const saved = await api("PUT", `/api/templates/${encodeURIComponent(id)}`, dict);
    current = saved;
    fillForm();
    const t = templates.find(x => x.id === id);
    const summary = { id, name: saved.name, technique: saved.technique, motif_beats: saved.motif_beats, style: saved.style };
    if (t) Object.assign(t, summary);
    else templates.push(summary);
    renderTable();
    setStatus(`已保存「${saved.name}」`);
  } catch (e) { alert("保存失败: " + e.message); }
}

async function del() {
  if (!current) return;
  if (!confirm(`删除模板「${current.name}」？此操作不可撤销。`)) return;
  try {
    await api("DELETE", `/api/templates/${encodeURIComponent(current.id)}`);
    templates = templates.filter(t => t.id !== current.id);
    current = null;
    gridCells = [];
    document.getElementById("editor").querySelectorAll("input,select").forEach(c => { if (c.id !== "p-chord" && c.id !== "p-beats" && c.id !== "p-bpm") c.value = ""; });
    $("grid").innerHTML = "";
    renderTable();
    setStatus("已删除");
  } catch (e) { alert("删除失败: " + e.message); }
}

function newTemplate() {
  const id = prompt("新模板 ID（创建后不可改，建议用英文 slug）:", "my-pattern");
  if (!id) return;
  const motif = 1;
  current = {
    id, name: id, technique: "strum", style: "pop",
    motif_beats: motif, min_beats: motif, ideal_beats: [], sections: ["chorus"], positions: [],
    grid_motif: [{ type: "stroke", direction: "D", duration: 4 }],
  };
  // 先建后端记录，再填表单
  api("POST", "/api/templates", { id, template: readFormOnInit() }).then(() => {
    templates.push({ id, name: current.name, technique: current.technique, motif_beats: current.motif_beats, style: current.style });
    fillForm(); renderTable(); setStatus(`已新建「${current.name}」`);
  }).catch(async (e) => {
    // 已存在则从服务器拉取真实记录来编辑（不可用默认模板覆盖，否则会丢数据）
    if (String(e.message).includes("已存在")) {
      try { current = await api("GET", `/api/templates/${encodeURIComponent(id)}`); fillForm(); renderTable(); }
      catch (e2) { alert("加载已有模板失败: " + e2.message); }
    } else {
      alert("新建失败: " + e.message);
    }
  });
}
function readFormOnInit() {
  return {
    name: current.name, technique: current.technique, style: current.style,
    motif_beats: current.motif_beats, min_beats: current.min_beats,
    ideal_beats: current.ideal_beats, sections: current.sections, positions: current.positions,
    grid_motif: current.grid_motif,
  };
}

// ── 追加格 ───────────────────────────────────────────────────────────
$("add-cell-btn").onclick = () => { gridCells.push({ type: "rest", duration: 1 }); paintGrid(); updateMotifSum(); };
$("add-beat-btn").onclick = () => { gridCells.push({ type: "rest", duration: 4 }); paintGrid(); updateMotifSum(); };

// 动机时值之和 vs 4*motif_beats 显示（后端 __post_init__ 要求 sum==4*motif）。
function motifTotal() { return gridCells.reduce((s, c) => s + (c.duration || 1), 0); }
function updateMotifSum() {
  const motif = Math.max(1, parseInt($("f-motif").value, 10) || 1);
  const need = 4 * motif;
  const got = motifTotal();
  const el = $("motif-sum") || (() => { const e = document.createElement("div"); e.id = "motif-sum"; e.style.cssText = "font-size:12px;margin-top:4px;"; $("grid").parentNode.insertBefore(e, $("grid").nextSibling); return e; })();
  el.textContent = `时值之和 ${got} / 需要 ${need}（${got === need ? "✓对齐" : "✗未对齐，保存会被拒"}）`;
  el.style.color = got === need ? "#2e7d32" : "#c62828";
}

// ── 试听：Web Audio 合成 ─────────────────────────────────────────────
function ensureCtx() {
  if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  if (audioCtx.state === "suspended") audioCtx.resume();
  return audioCtx;
}

function stopPlay() {
  playTimers.forEach(t => { if (t.timer) clearTimeout(t.timer); if (t.osc) { try { t.osc.stop(); } catch (e) { /* 未启动的 osc.stop 会抛，忽略 */ } } });
  playTimers = [];
  document.querySelectorAll(".cell.playing").forEach(e => e.classList.remove("playing"));
}

function midiToFreq(m) { return 440 * Math.pow(2, (m - 69) / 12); }

// 吉他近似音色：三角波 + 包络。音持续 durSec（动作真实时值），末尾指数衰减。
// durSec 来自动作的 duration_tick（延续=长、断音=短），由用户逐格控制。
function playNote(ctx, midi, startSec, durSec, vel) {
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = "triangle";
  osc.frequency.value = midiToFreq(midi);
  const peak = 0.18 * (vel / 100);
  const tail = 0.15;  // 末尾自然衰减时长（秒），与动作时值无关
  const sustainEnd = startSec + Math.max(0.02, durSec);  // 持续到动作时值结束
  const releaseEnd = sustainEnd + tail;                  // 再衰减尾巴
  gain.gain.setValueAtTime(0, startSec);
  gain.gain.linearRampToValueAtTime(peak, startSec + 0.005);
  gain.gain.setValueAtTime(peak, sustainEnd);            // 持续段保持
  gain.gain.exponentialRampToValueAtTime(0.0008, releaseEnd);
  osc.connect(gain).connect(ctx.destination);
  osc.start(startSec);
  osc.stop(releaseEnd + 0.02);
  return osc;
}

async function play() {
  if (!current) return alert("请先选择模板");
  stopPlay();
  ensureCtx();
  const dict = readForm();
  const body = {
    template: dict,
    chord: $("p-chord").value.trim() || "C",
    beats: parseInt($("p-beats").value, 10) || 4,
    bpm: parseInt($("p-bpm").value, 10) || 90,
  };
  let nl;
  try { nl = await api("POST", "/api/preview", body); }
  catch (e) {
    $("notelist-info").textContent = "预览失败: " + e.message;
    setStatus("预览失败");
    return alert("预览失败: " + e.message);
  }

  $("notelist-info").textContent = `${nl.notes.length} 个音符 / ${nl.total_tick} tick / BPM ${nl.bpm}`;
  if (!nl.notes.length) { setStatus("无音符（voicing 解析失败？）"); return; }

  const ctx = audioCtx;
  const tickSec = 60 / nl.bpm / nl.ticks_per_beat;
  const t0 = ctx.currentTime + 0.05;
  for (const n of nl.notes) {
    const startSec = t0 + n.start_tick * tickSec;
    const durSec = n.duration_tick * tickSec;  // 动作真实时值，延续/断音由 duration 控制
    const osc = playNote(ctx, n.midi, startSec, durSec, n.velocity);
    playTimers.push({ osc, timer: null });
  }
  // 栅格高亮：按 grid tick 定位对应 cell
  highlightGrid(nl, t0, tickSec);
  const totalSec = nl.total_tick * tickSec + 0.3;
  playTimers.push({ osc: null, timer: setTimeout(() => stopPlay(), totalSec * 1000) });
  setStatus(`播放中…（${nl.notes.length} 音符）`);
}

function highlightGrid(nl, t0, tickSec) {
  // 每个 nl.grid 动作对应一个发音 cell（按其 tick 在动机周期内累计 duration 定位）。
  // 高亮该 cell，持续动作的 duration_tick。
  const cells = document.querySelectorAll("#grid .cell");
  const motifTotal = gridCells.reduce((s, c) => s + (c.duration || 1), 0) || 1;
  nl.grid.forEach((g) => {
    // 把 g.tick 折叠到动机周期内，找落在哪个编辑器 cell。
    const localTick = g.tick % motifTotal;
    let idx = 0, acc = 0;
    for (let i = 0; i < gridCells.length; i++) {
      if (acc === localTick) { idx = i; break; }
      acc += (gridCells[i].duration || 1);
      if (acc > localTick) { idx = i; break; }
    }
    const startMs = (t0 + g.tick * tickSec - audioCtx.currentTime) * 1000;
    const durMs = (g.duration_tick || 1) * tickSec * 1000;
    const tOn = setTimeout(() => {
      cells.forEach(c => c.classList.remove("playing"));
      const el = document.querySelector(`#grid .cell[data-idx="${idx}"]`);
      if (el) el.classList.add("playing");
    }, Math.max(0, startMs));
    playTimers.push({ osc: null, timer: tOn });
  });
}

// ── 事件绑定 + 初始化 ────────────────────────────────────────────────
$("save-btn").onclick = save;
$("delete-btn").onclick = del;
$("play-btn").onclick = play;
$("stop-btn").onclick = stopPlay;
$("new-btn").onclick = newTemplate;
$("search").oninput = renderTable;

// 动机拍数改变时，自动对齐 grid 时值之和到 4*motif（补 duration=1 rest 或削减末格 duration），
// 避免发到后端被 __post_init__ 拒（sum(duration) 必须 == 4 * motif_beats）。同时保证 min_beats >= motif。
$("f-motif").onchange = () => {
  const motif = Math.max(1, parseInt($("f-motif").value, 10) || 1);
  $("f-motif").value = motif;
  alignGridToMotif(motif);
  const minV = parseInt($("f-min").value, 10) || motif;
  $("f-min").value = Math.max(minV, motif);
  paintGrid();
  updateMotifSum();
};

// 把 grid 时值之和调整到 4*motif：不足补 duration=1 rest，超了从末格削减 duration、削光则删格。
function alignGridToMotif(motif) {
  const need = 4 * motif;
  let total = motifTotal();
  while (total < need) { gridCells.push({ type: "rest", duration: 1 }); total += 1; }
  while (total > need) {
    const last = gridCells[gridCells.length - 1];
    if (!last) break;
    if (last.duration > 1) { last.duration -= 1; total -= 1; }
    else { gridCells.pop(); total -= 1; }
  }
}

loadList().catch(e => setStatus("加载失败: " + e.message));
