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
    if (c.type === "stroke") return { type: "stroke", direction: c.direction };
    if (c.type === "rest") return { type: "rest" };
    if (c.type === "pluck") return { type: "pluck", role: c.role || null };
    return { type: "rest" };
  });
}
function cellsToData(cells) {
  return cells.map(c => {
    if (c.type === "stroke") return { type: "stroke", direction: c.direction };
    if (c.type === "pluck") return { type: "pluck", role: c.role };
    return { type: "rest" };
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

  const rm = document.createElement("button"); rm.className = "rm"; rm.textContent = "✕";
  rm.onclick = () => { gridCells.splice(idx, 1); paintGrid(); };
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
    grid_motif: [{ type: "stroke", direction: "D" }, { type: "rest" }, { type: "rest" }, { type: "rest" }],
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
$("add-cell-btn").onclick = () => { gridCells.push({ type: "rest" }); paintGrid(); };
$("add-beat-btn").onclick = () => { for (let i = 0; i < 4; i++) gridCells.push({ type: "rest" }); paintGrid(); };

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

// 吉他近似音色：三角波 + 衰减包络。duration_tick=1（逐格断音），故音长不依赖 durSec，
// 给一个固定的自然余音（约 0.2 秒指数衰减），既断音清晰又不生硬。
function playNote(ctx, midi, startSec, durSec, vel) {
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = "triangle";
  osc.frequency.value = midiToFreq(midi);
  const peak = 0.18 * (vel / 100);
  const tail = 0.20;  // 自然余音衰减时长（秒），与 BPM 无关
  gain.gain.setValueAtTime(0, startSec);
  gain.gain.linearRampToValueAtTime(peak, startSec + 0.005);
  gain.gain.exponentialRampToValueAtTime(0.0008, startSec + tail);
  osc.connect(gain).connect(ctx.destination);
  osc.start(startSec);
  osc.stop(startSec + tail + 0.02);
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
    const durSec = Math.max(0.05, n.duration_tick * tickSec * 0.95);
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
  // 按 tick 折叠到动机周期内：动作 tick 对应平铺栅格位置，对动机长度取模即得动机格下标。
  // 动机重复铺开时，每拍对应动机里同位置的格，故折叠映射语义正确。
  const motifLen = gridCells.length || 1;
  const cells = document.querySelectorAll("#grid .cell");
  nl.grid.forEach((g) => {
    const motifIdx = (g.tick / 4 | 0) * 4 % motifLen;  // tick→拍→拍内起点，折叠到动机
    // 进一步定位到该拍内最近的发声格（向前找第一个非 rest）
    let idx = motifIdx;
    for (let off = 0; off < 4; off++) {
      const cand = motifIdx + off;
      if (cand < motifLen && gridCells[cand].type !== "rest") { idx = cand; break; }
    }
    const startSec = (t0 + g.tick * tickSec - audioCtx.currentTime) * 1000;
    const t = setTimeout(() => {
      cells.forEach(c => c.classList.remove("playing"));
      const el = document.querySelector(`#grid .cell[data-idx="${idx}"]`);
      if (el) el.classList.add("playing");
    }, Math.max(0, startSec));
    playTimers.push({ osc: null, timer: t });
  });
}

// ── 事件绑定 + 初始化 ────────────────────────────────────────────────
$("save-btn").onclick = save;
$("delete-btn").onclick = del;
$("play-btn").onclick = play;
$("stop-btn").onclick = stopPlay;
$("new-btn").onclick = newTemplate;
$("search").oninput = renderTable;

// 动机拍数改变时，自动对齐 grid 长度到 4*motif（补 rest 或裁断），避免发到后端被
// __post_init__ 拒（grid_motif 长度必须 == 4 * motif_beats）。同时保证 min_beats >= motif。
$("f-motif").onchange = () => {
  const motif = Math.max(1, parseInt($("f-motif").value, 10) || 1);
  $("f-motif").value = motif;
  const need = 4 * motif;
  while (gridCells.length < need) gridCells.push({ type: "rest" });
  if (gridCells.length > need) gridCells.length = need;
  const minV = parseInt($("f-min").value, 10) || motif;
  $("f-min").value = Math.max(minV, motif);
  paintGrid();
};

loadList().catch(e => setStatus("加载失败: " + e.message));
