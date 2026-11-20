/* 股票筛选系统 前端 (无框架) */
"use strict";

// ---------------- 工具 ----------------
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtN = (v, d = 2) => (v === null || v === undefined || v === "" || Number.isNaN(+v))
  ? "—" : (+v).toFixed(d);
const fmtPct = v => (v === null || v === undefined) ? "—" : `${(+v).toFixed(2)}%`;
const pctCls = v => (v === null || v === undefined) ? "" : (v > 0 ? "up" : v < 0 ? "down" : "");

async function api(path, opts = {}) {
  const res = await fetch("/api" + path, {
    headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data));
  return data;
}
function toast(msg, ms = 2600) {
  const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.add("hidden"), ms);
}

const state = { tab: "screen", indicators: [], pools: [], curPool: null,
                conds: [], sortKey: "code", sortDir: "asc", searchQ: "" };

// ---------------- 侧栏状态 + 轮询 ----------------
let lastJobState = "idle";
async function pollStatus() {
  try {
    const st = await api("/meta/status");
    const job = st.job;
    let html = `股票 ${st.stock_count} · 池内 ${st.pool_stock_count}`;
    html += `<br>全市场更新: ${esc(st.last_full_update)}`;
    html += `<br>池内更新: ${esc(st.last_pool_update)}`;
    if (job.status === "running") {
      const pc = job.total ? Math.round(job.progress / job.total * 100) : 0;
      html += `<div style="margin-top:8px;color:#fff">${esc(job.message)}</div>
               <div class="progress" style="margin-top:5px"><div style="width:${pc}%"></div></div>`;
    }
    $("#side-status").innerHTML = html;
    if (lastJobState === "running" && job.status !== "running") {
      toast(job.status === "done" ? `任务完成: ${job.message}` : `任务失败: ${job.error}`);
      renderMain(); // 刷新当前页数据
    }
    lastJobState = job.status;
    return st;
  } catch (e) { /* 服务未就绪 */ }
}

// ---------------- 路由 ----------------
function nav(tab) { location.hash = "#/" + tab; }
window.addEventListener("hashchange", route);
function route() {
  const h = location.hash.replace(/^#\//, "");
  if (h.startsWith("detail/")) { renderDetail(decodeURIComponent(h.slice(7))); return; }
  state.tab = h || "screen";
  $$(".nav-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === state.tab));
  renderMain();
}
$$(".nav-btn").forEach(b => b.onclick = () => nav(b.dataset.tab));

function renderMain() {
  ({ screen: renderScreen, pool: renderPool, monitor: renderMonitor, settings: renderSettings }
  )[state.tab]();
}

// ---------------- 指标下拉 ----------------
function indicatorSelect(sel, kindFilter = null) {
  const groups = {};
  state.indicators.forEach(i => {
    if (["code", "name"].includes(i.key)) return;
    if (kindFilter === "value" && i.kind !== "value") return;
    (groups[i.group] = groups[i.group] || []).push(i);
  });
  sel.innerHTML = Object.entries(groups).map(([g, items]) =>
    `<optgroup label="${esc(g)}">${items.map(i =>
      `<option value="${esc(i.key)}">${esc(i.name)}</option>`).join("")}</optgroup>`).join("");
}
const indByKey = key => state.indicators.find(i => i.key === key);

// ================= 筛选页 =================
const QUICK_CHIPS = [
  { label: "跌破MA120", conds: [{ key: "below_ma120", op: "yes" }] },
  { label: "低于BOLL下轨", conds: [{ key: "below_boll_low", op: "yes" }] },
  { label: "价格分位<10%", conds: [{ key: "px_pct", op: "lt", value: 10 }] },
  { label: "PE分位<20%", conds: [{ key: "pe_pct", op: "lt", value: 20 }] },
  { label: "股息率≥4%", conds: [{ key: "dv_ttm", op: "ge", value: 4 }] },
  { label: "跌破年线", conds: [{ key: "below_ma250", op: "yes" }] },
];
const SCREEN_COLS = [
  { key: "code", name: "代码", fmt: "text" }, { key: "name", name: "名称", fmt: "text" },
  { key: "close", name: "现价", fmt: "price" }, { key: "pct_chg", name: "涨跌幅%", fmt: "pct" },
  { key: "ma120", name: "MA120", fmt: "price" }, { key: "boll_low", name: "BOLL下轨", fmt: "price" },
  { key: "below_ma120", name: "破120线", fmt: "flag" }, { key: "below_boll_low", name: "破BOLL", fmt: "flag" },
  { key: "pe_ttm", name: "PE-TTM", fmt: "num2" }, { key: "pb", name: "PB", fmt: "num2" },
  { key: "dv_ttm", name: "股息率%", fmt: "num2" },
  { key: "px_pct", name: "价格分位", fmt: "num1" }, { key: "pe_pct", name: "PE分位", fmt: "num1" },
  { key: "pb_pct", name: "PB分位", fmt: "num1" },
  { key: "mktcap", name: "总市值(亿)", fmt: "num0" }, { key: "daily_date", name: "数据日", fmt: "text" },
];

function renderScreen() {
  $("#main").innerHTML = `
  <div class="tab-title">全市场筛选 <span class="muted">(数据截至上次更新, 设置页可手动更新)</span></div>
  <div class="card">
    <div class="row">
      <input id="q" placeholder="按名称 / 代码搜索…" style="width:220px" value="${esc(state.searchQ)}">
      <button class="primary" id="btn-search">查询</button>
      <button id="btn-reset">重置</button>
      <div class="spacer"></div>
      <span class="muted">快捷条件:</span>
      ${QUICK_CHIPS.map((c, i) => `<span class="chip" data-chip="${i}">${esc(c.label)}</span>`).join("")}
      <button class="mini" id="btn-add-cond">+ 自定义条件</button>
    </div>
    <div id="cond-list"></div>
  </div>
  <div class="card">
    <div class="row" style="margin-bottom:8px">
      <span id="result-count" class="muted">…</span><div class="spacer"></div>
      <span class="muted">点击行查看个股详情</span>
    </div>
    <div style="overflow:auto; max-height: calc(100vh - 300px)">
      <table id="result-table"><thead></thead><tbody></tbody></table>
    </div>
  </div>`;
  $("#q").oninput = e => { state.searchQ = e.target.value; };
  $("#q").onkeydown = e => { if (e.key === "Enter") doSearch(); };
  $("#btn-search").onclick = doSearch;
  $("#btn-reset").onclick = () => { state.conds = []; state.searchQ = "";
    $("#q").value = ""; renderCondList(); doSearch(); };
  $("#btn-add-cond").onclick = () => { state.conds.push({ key: "px_pct", op: "lt", value: 10 });
    renderCondList(); };
  $$("[data-chip]").forEach(el => el.onclick = () => {
    const chip = QUICK_CHIPS[+el.dataset.chip];
    const on = el.classList.toggle("on");
    if (on) chip.conds.forEach(c => state.conds.push({ ...c }));
    else chip.conds.forEach(c => {
      const idx = state.conds.findIndex(x => x.key === c.key && x.op === c.op && x.value === c.value);
      if (idx >= 0) state.conds.splice(idx, 1);
    });
    renderCondList(); doSearch();
  });
  renderCondList();
  doSearch();
}

function renderCondList() {
  const box = $("#cond-list"); if (!box) return;
  box.innerHTML = state.conds.map((c, i) => {
    const ind = indByKey(c.key) || { kind: "value" };
    const ops = ind.kind === "flag"
      ? [["yes", "为真"], ["no", "为假"]]
      : [["lt", "<"], ["le", "≤"], ["gt", ">"], ["ge", "≥"]];
    return `<div class="cond-row" data-i="${i}">
      <select data-act="key">${indSelectOptions(c.key)}</select>
      <select data-act="op">${ops.map(([v, t]) =>
        `<option value="${v}" ${v === c.op ? "selected" : ""}>${t}</option>`).join("")}</select>
      <input data-act="val" type="number" step="any" value="${c.value ?? ""}"
             style="width:100px" ${ind.kind === "flag" ? 'class="hidden"' : ""}>
      <button class="del" data-act="del" title="删除">×</button>
    </div>`;
  }).join("");
  $$("[data-i]", box).forEach(row => {
    const i = +row.dataset.i;
    row.querySelector('[data-act="key"]').onchange = e => {
      state.conds[i].key = e.target.value;
      const ind = indByKey(e.target.value);
      if (ind.kind === "flag") state.conds[i].op = "yes";
      renderCondList(); syncChips();
    };
    row.querySelector('[data-act="op"]').onchange = e => { state.conds[i].op = e.target.value; };
    row.querySelector('[data-act="val"]').onchange = e =>
      { state.conds[i].value = e.target.value === "" ? null : +e.target.value; };
    row.querySelector('[data-act="del"]').onclick = () =>
      { state.conds.splice(i, 1); renderCondList(); syncChips(); };
  });
}
function indSelectOptions(selected) {
  const groups = {};
  state.indicators.forEach(i => {
    if (["code", "name"].includes(i.key)) return;
    (groups[i.group] = groups[i.group] || []).push(i);
  });
  return Object.entries(groups).map(([g, items]) =>
    `<optgroup label="${esc(g)}">${items.map(i =>
      `<option value="${esc(i.key)}" ${i.key === selected ? "selected" : ""}>${esc(i.name)}</option>`
    ).join("")}</optgroup>`).join("");
}
function syncChips() {
  $$("[data-chip]").forEach(el => {
    const chip = QUICK_CHIPS[+el.dataset.chip];
    const on = chip.conds.some(c => state.conds.some(x =>
      x.key === c.key && x.op === c.op && x.value === c.value));
    el.classList.toggle("on", on);
  });
}

async function doSearch() {
  const filters = state.conds.filter(c =>
    indByKey(c.key) && (indByKey(c.key).kind === "flag" || c.value !== null && c.value !== undefined));
  const params = new URLSearchParams({
    q: state.searchQ, filters: JSON.stringify(filters),
    sort: state.sortKey, dir: state.sortDir, limit: 500,
  });
  try {
    const data = await api("/stocks/search?" + params);
    $("#result-count").textContent = `共 ${data.total} 只 (最多显示500)`;
    renderResultTable(data.items);
  } catch (e) { toast("查询失败: " + e.message); }
}

function renderResultTable(items) {
  const thead = $("#result-table thead"), tbody = $("#result-table tbody");
  thead.innerHTML = "<tr>" + SCREEN_COLS.map(c =>
    `<th data-key="${c.key}" class="${["code","name"].includes(c.key) ? "l" : ""}">${esc(c.name)}`
    + `${state.sortKey === c.key ? (state.sortDir === "asc" ? " ▲" : " ▼") : ""}</th>`).join("") + "</tr>";
  $$("th", thead).forEach(th => th.onclick = () => {
    const k = th.dataset.key;
    if (state.sortKey === k) state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
    else { state.sortKey = k; state.sortDir = "desc"; }
    doSearch();
  });
  tbody.innerHTML = items.map(r => "<tr data-code=\"" + esc(r.code) + "\">" + SCREEN_COLS.map(c => {
    const v = r[c.key];
    let html;
    if (c.fmt === "flag") html = v === 1 ? '<span class="pill red">是</span>' : '<span class="pill gray">否</span>';
    else if (c.fmt === "pct") html = `<span class="${pctCls(v)}">${fmtPct(v)}</span>`;
    else if (c.fmt === "text") html = esc(v ?? "—");
    else html = `<span class="${c.key === "close" ? pctCls(r.pct_chg) : ""}">${fmtN(v, c.fmt === "num0" ? 0 : c.fmt === "num1" ? 1 : 2)}</span>`;
    return `<td class="${["code","name"].includes(c.key) ? "l" : ""}">${html}</td>`;
  }).join("") + "</tr>").join("");
  $$("tbody tr", tbody).forEach(tr => tr.onclick = () =>
    nav("detail/" + tr.dataset.code));
}

// ================= 股票池页 =================
function renderPool() {
  $("#main").innerHTML = `
  <div class="tab-title">股票池</div>
  <div class="two-col">
    <div class="left">
      <div class="card">
        <div class="row" style="margin-bottom:10px">
          <input id="pool-name" placeholder="新池名称" style="width:110px">
          <button class="primary mini" id="btn-new-pool">新建</button>
        </div>
        <div id="pool-list"></div>
      </div>
    </div>
    <div class="right">
      <div class="card" id="pool-panel">← 选择或新建一个股票池</div>
    </div>
  </div>`;
  $("#btn-new-pool").onclick = async () => {
    const name = $("#pool-name").value.trim();
    if (!name) return toast("请输入池名称");
    try {
      await api("/pools", { method: "POST", body: { name } });
      $("#pool-name").value = ""; await loadPools(); toast("已创建");
    } catch (e) { toast(e.message); }
  };
  loadPools();
}
async function loadPools() {
  state.pools = await api("/pools");
  const box = $("#pool-list");
  if (box) box.innerHTML = state.pools.map(p =>
    `<div class="pool-item ${state.curPool === p.id ? "active" : ""}" data-id="${p.id}">
       <span>${esc(p.name)}</span><span class="muted">${p.stock_count}</span>
     </div>`).join("") || '<div class="muted">暂无股票池</div>';
  $$(".pool-item", box).forEach(el => el.onclick = () => { state.curPool = +el.dataset.id; loadPools(); renderPoolPanel(); });
  if (state.curPool && state.pools.some(p => p.id === state.curPool)) renderPoolPanel();
}
function renderPoolPanel() {
  const panel = $("#pool-panel"); if (!panel) return;
  const pool = state.pools.find(p => p.id === state.curPool);
  if (!pool) { panel.innerHTML = "← 选择一个股票池"; return; }
  panel.innerHTML = `
    <div class="row" style="margin-bottom:10px">
      <b style="font-size:15px">${esc(pool.name)}</b>
      <span class="muted">${esc(pool.note || "")}</span>
      <div class="spacer"></div>
      <input id="add-code" placeholder="股票代码 如 600519" style="width:150px">
      <input id="add-target" type="number" step="any" placeholder="预期价(可选)" style="width:110px">
      <button class="primary mini" id="btn-add-stock">加入</button>
      <button class="mini" id="btn-refresh-pool">更新池内数据</button>
      <button class="danger mini" id="btn-del-pool">删除此池</button>
    </div>
    <div style="overflow:auto"><table id="pool-table">
      <thead><tr><th class="l">代码</th><th class="l">名称</th><th>现价</th><th>涨跌幅%</th>
      <th>股息率%</th><th>价格分位</th><th class="l">标签</th><th class="l">备注</th>
      <th>预期价</th><th>达预期</th><th class="l">操作</th></tr></thead>
      <tbody></tbody></table></div>`;
  $("#btn-add-stock").onclick = () => addStock(pool.id);
  $("#add-code").onkeydown = e => { if (e.key === "Enter") addStock(pool.id); };
  $("#btn-refresh-pool").onclick = async () => {
    try { const r = await api("/admin/update/pool", { method: "POST" });
      toast(r.msg); pollStatus(); } catch (e) { toast(e.message); }
  };
  $("#btn-del-pool").onclick = async () => {
    if (!confirm(`删除股票池「${pool.name}」及其成员记录?(规则不受影响)`)) return;
    await api("/pools/" + pool.id, { method: "DELETE" });
    state.curPool = null; loadPools(); renderPoolPanel();
  };
  loadPoolStocks(pool.id);
}
async function loadPoolStocks(poolId) {
  const items = await api(`/pools/${poolId}/stocks`);
  const tbody = $("#pool-table tbody"); if (!tbody) return;
  tbody.innerHTML = items.map(r => `<tr data-code="${esc(r.code)}">
    <td class="l">${esc(r.code)}</td>
    <td class="l">${esc(r.name ?? "—")}</td>
    <td class="num">${fmtN(r.close, 2)}</td>
    <td class="num ${pctCls(r.pct_chg)}">${fmtPct(r.pct_chg)}</td>
    <td class="num">${fmtN(r.dv_ttm, 2)}</td>
    <td class="num">${fmtN(r.px_pct, 1)}</td>
    <td class="l"><input data-f="tag" value="${esc(r.tag || "")}"></td>
    <td class="l"><input data-f="note" value="${esc(r.note || "")}"></td>
    <td class="num"><input data-f="target_price" type="number" step="any" style="width:90px"
        value="${r.target_price ?? ""}"></td>
    <td>${r.target_price && r.close && r.close >= r.target_price
      ? '<span class="pill red">已达</span>' : '<span class="pill gray">—</span>'}</td>
    <td class="l"><button class="mini" data-act="detail">详情</button>
        <button class="mini danger" data-act="remove">移除</button></td>
  </tr>`).join("") || '<tr><td colspan="11" class="muted l">池内暂无股票</td></tr>';
  $$("tbody tr", tbody).forEach(tr => {
    const code = tr.dataset.code;
    $$("input", tr).forEach(inp => inp.onchange = async () => {
      const body = { tag: $('input[data-f="tag"]', tr).value,
                     note: $('input[data-f="note"]', tr).value,
                     target_price: $('input[data-f="target_price"]', tr).value === "" ? null
                                   : +$('input[data-f="target_price"]', tr).value };
      try { await api(`/pools/${poolId}/stocks/${code}`, { method: "PUT", body });
            toast("已保存"); renderPoolPanel(); } catch (e) { toast(e.message); }
    });
    $('[data-act="detail"]', tr).onclick = () => nav("detail/" + code);
    $('[data-act="remove"]', tr).onclick = async () => {
      await api(`/pools/${poolId}/stocks/${code}`, { method: "DELETE" });
      loadPoolStocks(poolId); loadPools();
    };
  });
}
async function addStock(poolId) {
  const code = $("#add-code").value.trim();
  if (!code) return toast("请输入代码");
  const target = $("#add-target").value;
  try {
    await api(`/pools/${poolId}/stocks`, { method: "POST",
      body: { code, target_price: target === "" ? null : +target } });
    $("#add-code").value = ""; $("#add-target").value = "";
    toast("已加入并拉取数据"); loadPoolStocks(poolId); loadPools();
  } catch (e) { toast(e.message); }
}

// ================= 监控页 =================
function renderMonitor() {
  $("#main").innerHTML = `
  <div class="tab-title">监控规则与信号</div>
  <div class="card">
    <div class="row" style="margin-bottom:10px">
      <b>规则</b><div class="spacer"></div>
      <button class="primary mini" id="btn-eval">立即评估</button>
    </div>
    <table id="rules-table"><thead><tr><th class="l">名称</th><th class="l">条件</th>
      <th class="l">范围</th><th>邮件</th><th>启用</th><th class="l">操作</th></tr></thead>
      <tbody></tbody></table>
    <hr class="sep">
    <div class="row" id="rule-form">
      <input id="r-name" placeholder="规则名(可空)" style="width:130px">
      <select id="r-ind"></select>
      <select id="r-op"></select>
      <input id="r-val" type="number" step="any" placeholder="阈值" style="width:90px">
      <select id="r-pool"></select>
      <label><input type="checkbox" id="r-email"> 邮件提醒</label>
      <button class="primary mini" id="btn-add-rule">添加规则</button>
    </div>
  </div>
  <div class="card">
    <div class="row" style="margin-bottom:8px"><b>信号</b>
      <select id="sig-days"><option value="3">近3天</option><option value="14" selected>近14天</option>
      <option value="60">近60天</option></select><div class="spacer"></div>
      <span class="muted">信号按 规则×股票×交易日 去重</span></div>
    <div style="overflow:auto"><table id="signals-table">
      <thead><tr><th class="l">日期</th><th class="l">股票</th><th class="l">规则</th>
      <th class="l">详情</th><th>邮件</th></tr></thead><tbody></tbody></table></div>
  </div>`;
  indicatorSelect($("#r-ind"), "value");
  const flagAlso = $("#r-ind");
  // 允许 flag 指标(建两个 optgroup 太复杂, 重新生成含全部指标)
  flagAlso.innerHTML = indSelectOptions("px_pct");
  syncRuleOpOptions();
  $("#r-ind").onchange = syncRuleOpOptions;
  $("#r-pool").innerHTML = '<option value="">所有池</option>' + state.pools.map(p =>
    `<option value="${p.id}">${esc(p.name)}</option>`).join("");
  $("#btn-add-rule").onclick = addRule;
  $("#btn-eval").onclick = async () => {
    try { const r = await api("/admin/update/evaluate", { method: "POST" });
      toast(r.msg); loadSignals(); } catch (e) { toast(e.message); }
  };
  $("#sig-days").onchange = loadSignals;
  loadRules(); loadSignals();
}
function syncRuleOpOptions() {
  const ind = indByKey($("#r-ind").value);
  const ops = ind && ind.kind === "flag"
    ? [["yes", "为真"], ["no", "为假"]] : [["lt", "<"], ["le", "≤"], ["gt", ">"], ["ge", "≥"]];
  $("#r-op").innerHTML = ops.map(([v, t]) => `<option value="${v}">${t}</option>`).join("");
  $("#r-val").classList.toggle("hidden", ind && ind.kind === "flag");
}
async function loadRules() {
  const rules = await api("/rules");
  const tbody = $("#rules-table tbody"); if (!tbody) return;
  tbody.innerHTML = rules.map(r => `<tr data-id="${r.id}">
    <td class="l">${esc(r.name)}</td>
    <td class="l">${esc(r.desc)}</td>
    <td class="l">${r.pool_name ? esc(r.pool_name) : '<span class="pill">所有池</span>'}</td>
    <td>${r.email ? "✉️" : "—"}</td>
    <td><input type="checkbox" data-act="enable" ${r.enabled ? "checked" : ""}></td>
    <td class="l"><button class="mini danger" data-act="del">删除</button></td>
  </tr>`).join("") || '<tr><td colspan="6" class="muted l">暂无规则</td></tr>';
  $$("tbody tr", tbody).forEach(tr => {
    const id = tr.dataset.id;
    $('[data-act="enable"]', tr).onchange = async e => {
      const r = rules.find(x => x.id === +id);
      await api("/rules/" + id, { method: "PUT", body: {
        name: r.name, indicator: r.indicator, op: r.op, value: r.value,
        pool_id: r.pool_id, email: !!r.email, enabled: e.target.checked } });
      loadRules();
    };
    $('[data-act="del"]', tr).onclick = async () => {
      if (!confirm("删除该规则及其历史信号?")) return;
      await api("/rules/" + id, { method: "DELETE" }); loadRules();
    };
  });
}
async function addRule() {
  const body = {
    name: $("#r-name").value.trim(),
    indicator: $("#r-ind").value, op: $("#r-op").value,
    value: $("#r-val").value === "" ? null : +$("#r-val").value,
    pool_id: $("#r-pool").value === "" ? null : +$("#r-pool").value,
    email: $("#r-email").checked, enabled: true,
  };
  if (indByKey(body.indicator).kind === "value" && body.value === null)
    return toast("数值型指标需要阈值");
  try { await api("/rules", { method: "POST", body }); toast("规则已添加");
    $("#r-name").value = ""; loadRules(); } catch (e) { toast(e.message); }
}
async function loadSignals() {
  const days = +($("#sig-days")?.value || 14);
  const rows = await api(`/signals?days=${days}`);
  const tbody = $("#signals-table tbody"); if (!tbody) return;
  tbody.innerHTML = rows.map(r => `<tr data-code="${esc(r.code)}">
    <td class="l">${esc(r.trade_date)}</td>
    <td class="l">${esc(r.stock_name || "")} ${esc(r.code)}</td>
    <td class="l">${esc(r.rule_name || r.rule_id)}</td>
    <td class="l">${esc(r.detail || "")}</td>
    <td>${r.email ? (r.emailed ? '<span class="pill">已发</span>'
      : '<span class="pill gray">未发</span>') : "—"}</td>
  </tr>`).join("") || '<tr><td colspan="5" class="muted l">暂无信号</td></tr>';
  $$("tbody tr", tbody).forEach(tr => tr.onclick = () => nav("detail/" + tr.dataset.code));
}

// ================= 设置页 =================
function renderSettings() {
  $("#main").innerHTML = `
  <div class="tab-title">设置</div>
  <div class="card">
    <b>数据更新</b>
    <div class="row" style="margin-top:10px">
      <button class="primary" id="btn-full">全市场全量更新</button>
      <button id="btn-pool">更新池内数据</button>
      <button id="btn-snap">盘中快照刷新</button>
      <button id="btn-eval2">评估监控规则</button>
    </div>
    <p class="muted" style="margin-top:8px">
      自动调度: 每个交易日 <b>12:30</b> 与 <b>15:30</b> 自动更新<b>池内</b>股票并评估规则;
      全市场数据仅在首次或手动时更新。首次使用请先点「全市场全量更新」(约20~30分钟)。
      服务为手动启动(无开机自启);启动时会自动补跑当天错过的更新。
      电脑休眠期间服务暂停, 唤醒后重新打开网页即可, 数据会自动补齐。
    </p>
    <div id="job-box" style="margin-top:10px"></div>
  </div>
  <div class="card">
    <b>邮件通知 (SMTP)</b>
    <p class="muted" style="margin:6px 0">用于「达到预期线」等开启邮件的规则。例: QQ邮箱 host=smtp.qq.com port=465 勾选SSL, 授权码作为密码。</p>
    <div class="row" style="margin-top:6px">
      <input id="s-host" placeholder="SMTP服务器" style="width:170px">
      <input id="s-port" placeholder="端口" style="width:70px">
      <label><input type="checkbox" id="s-ssl" checked> SSL</label>
      <input id="s-user" placeholder="发件邮箱账号" style="width:200px">
      <input id="s-pass" type="password" placeholder="密码/授权码" style="width:160px">
      <input id="s-to" placeholder="收件邮箱" style="width:200px">
      <button class="primary mini" id="btn-save-smtp">保存</button>
      <button class="mini" id="btn-test-mail">发送测试邮件</button>
    </div>
  </div>`;
  $("#btn-full").onclick = async () => {
    if (!confirm("全市场全量更新约需20~30分钟, 确认开始?")) return;
    const r = await api("/admin/update/full", { method: "POST" });
    toast(r.ok ? "已开始, 进度见左侧/下方" : r.msg); pollStatus();
  };
  $("#btn-pool").onclick = async () => { const r = await api("/admin/update/pool", { method: "POST" }); toast(r.msg); };
  $("#btn-snap").onclick = async () => { const r = await api("/admin/update/snapshot", { method: "POST" });
    toast(r.ok ? "盘中快照刷新中(约80秒)…" : r.msg); };
  $("#btn-eval2").onclick = async () => { const r = await api("/admin/update/evaluate", { method: "POST" }); toast(r.msg); };
  $("#btn-save-smtp").onclick = saveSettings;
  $("#btn-test-mail").onclick = async () => {
    await saveSettings(true);
    const r = await api("/settings/test_email", { method: "POST" });
    toast(r.msg);
  };
  fillSettings();
}
async function fillSettings() {
  try {
    const s = await api("/settings");
    $("#s-host").value = s.smtp_host || ""; $("#s-port").value = s.smtp_port || "465";
    $("#s-ssl").checked = s.smtp_ssl === "1"; $("#s-user").value = s.smtp_user || "";
    $("#s-pass").value = s.smtp_pass || ""; $("#s-to").value = s.mail_to || "";
  } catch (e) {}
}
async function saveSettings(silent = false) {
  const body = {
    smtp_host: $("#s-host").value.trim(), smtp_port: $("#s-port").value.trim() || "465",
    smtp_ssl: $("#s-ssl").checked ? "1" : "0", smtp_user: $("#s-user").value.trim(),
    smtp_pass: $("#s-pass").value, mail_to: $("#s-to").value.trim(),
  };
  await api("/settings", { method: "POST", body });
  if (!silent) toast("设置已保存");
}

// ================= 个股详情 =================
const DETAIL_METRICS = [
  ["close", "现价", "price"], ["pct_chg", "涨跌幅", "pct"], ["ma120_gap", "120日线偏离", "pct"],
  ["ma20", "MA20", "price"], ["ma60", "MA60", "price"], ["ma120", "MA120", "price"],
  ["ma250", "MA250", "price"], ["boll_up", "BOLL上轨", "price"], ["boll_low", "BOLL下轨", "price"],
  ["pe_ttm", "PE-TTM", "num2"], ["pb", "PB", "num2"], ["peg", "PEG", "num2"],
  ["dv_ttm", "股息率TTM%", "num2"], ["px_pct", "价格分位(全历史)", "num1"],
  ["pe_pct", "PE分位(全历史)", "num1"], ["pb_pct", "PB分位(全历史)", "num1"],
  ["drawdown_250d", "距一年最高回撤", "pct"], ["vol_250d", "年化波动率", "num1"],
  ["mktcap", "总市值(亿)", "num0"], ["turnover", "换手率%", "num2"],
];
async function renderDetail(code) {
  $$(".nav-btn").forEach(b => b.classList.remove("active"));
  $("#main").innerHTML = '<div class="muted">加载中…</div>';
  let d, k, v;
  try {
    [d, k, v] = await Promise.all([
      api("/stocks/" + code), api(`/stocks/${code}/kline?days=450`),
      api(`/stocks/${code}/valuation`)]);
  } catch (e) { $("#main").innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
  const s = d.stock;
  const below = (key, label) => s[key] === 1
    ? `<span class="pill red">${label}</span>` : "";
  $("#main").innerHTML = `
  <button class="back-btn" onclick="history.back()">← 返回</button>
  <div class="card">
    <div class="detail-head">
      <h2>${esc(s.name || "")}</h2>
      <span class="muted">${esc(s.code)} · ${esc(s.market || "")}</span>
      <span class="px ${pctCls(s.pct_chg)}">${fmtN(s.close, 2)}</span>
      <span class="${pctCls(s.pct_chg)}">${fmtPct(s.pct_chg)}</span>
      ${below("below_ma120", "跌破MA120")}${below("below_ma250", "跌破年线")}
      ${below("below_boll_low", "低于BOLL下轨")}
      <div class="spacer"></div>
      <select id="d-pool" style="width:130px"></select>
      <input id="d-target" type="number" step="any" placeholder="预期价" style="width:95px">
      <button class="primary mini" id="btn-add-to-pool">加入股票池</button>
    </div>
    <div class="grid-metrics" style="margin-top:14px">
      ${DETAIL_METRICS.map(([k2, label, fmt]) =>
        `<div class="metric"><div class="k">${esc(label)}</div>
         <div class="v ${k2 === "pct_chg" || k2 === "ma120_gap" || k2 === "drawdown_250d" ? pctCls(s[k2]) : ""}">
         ${fmt === "pct" ? fmtPct(s[k2]) : fmtN(s[k2], fmt === "num0" ? 0 : fmt === "num1" ? 1 : 2)}</div></div>`).join("")}
    </div>
    ${s.daily_date ? `<p class="muted" style="margin-top:10px">行情数据日: ${esc(s.daily_date)} · 估值数据日: ${esc(s.val_date || "—")} · 分位口径: 全历史</p>` : '<p class="muted" style="margin-top:10px">⚠️ 该股票尚无历史数据, 请在股票池中更新或执行全市场更新</p>'}
  </div>
  <div class="card"><div class="row"><b>K线 · 均线 · BOLL</b>
    <span class="muted">前复权 · 最近450个交易日</span></div>
    <div id="chart-k" class="chart"></div></div>
  <div class="card"><b>估值分位带</b>
    <span class="muted">水平线为全历史 10/25/50/75/90 分位</span>
    <div id="chart-pe" class="chart-sm"></div>
    <div id="chart-pb" class="chart-sm"></div></div>
  <div class="card">
    <b>近期分红</b>
    <table style="margin-top:8px; max-width:420px">
      <thead><tr><th class="l">除权除息日</th><th>每股派息(元)</th></tr></thead>
      <tbody>${d.dividends.map(r => `<tr><td class="l">${esc(r.ex_date)}</td>
        <td class="num">${fmtN(r.cash, 3)}</td></tr>`).join("")
        || '<tr><td colspan="2" class="muted l">无记录</td></tr>'}</tbody></table>
  </div>`;
  // 股票池下拉
  const sel = $("#d-pool");
  sel.innerHTML = state.pools.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("")
    || '<option value="">(先创建股票池)</option>';
  $("#btn-add-to-pool").onclick = async () => {
    if (!sel.value) return toast("请先创建股票池");
    const t = $("#d-target").value;
    try {
      await api(`/pools/${sel.value}/stocks`, { method: "POST",
        body: { code, target_price: t === "" ? null : +t } });
      toast("已加入股票池");
    } catch (e) { toast(e.message); }
  };
  drawKline($("#chart-k"), k);
  drawValuation($("#chart-pe"), v, "pe_ttm", "PE-TTM", "pe_lines");
  drawValuation($("#chart-pb"), v, "pb", "PB", "pb_lines");
}

function drawKline(el, k) {
  if (!k.dates.length) { el.innerHTML = '<p class="muted" style="padding:40px">暂无K线数据</p>'; return; }
  const maColors = { ma5: "#f59e0b", ma10: "#3b82f6", ma20: "#a855f7",
                     ma60: "#10b981", ma120: "#ef4444", ma250: "#64748b" };
  const maNames = { ma5: "MA5", ma10: "MA10", ma20: "MA20",
                    ma60: "MA60", ma120: "MA120", ma250: "MA250" };
  const series = [
    { name: "K线", type: "candlestick", data: k.k,
      itemStyle: { color: "#dc2626", color0: "#059669", borderColor: "#dc2626", borderColor0: "#059669" } },
    ...Object.keys(k.ma).map(m => ({ name: maNames[m], type: "line", data: k.ma[m],
      showSymbol: false, lineStyle: { width: 1.2, color: maColors[m] }, itemStyle: { color: maColors[m] } })),
    { name: "BOLL上轨", type: "line", data: k.boll.up, showSymbol: false,
      lineStyle: { width: 1, type: "dashed", color: "#94a3b8" }, itemStyle: { color: "#94a3b8" } },
    { name: "BOLL下轨", type: "line", data: k.boll.low, showSymbol: false,
      lineStyle: { width: 1, type: "dashed", color: "#94a3b8" }, itemStyle: { color: "#94a3b8" } },
    { name: "成交量", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: k.volume,
      itemStyle: { color: "#cbd5e1" } },
  ];
  echarts.init(el).setOption({
    animation: false,
    tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
    legend: { top: 0, type: "scroll",
      data: ["K线", ...Object.values(maNames).filter(n => k.ma[n.toLowerCase()]), "BOLL上轨", "BOLL下轨"] },
    grid: [{ left: 70, right: 20, top: 30, height: "58%" },
           { left: 70, right: 20, top: "76%", height: "16%" }],
    xAxis: [
      { type: "category", data: k.dates, boundaryGap: true },
      { type: "category", data: k.dates, gridIndex: 1, axisLabel: { show: false } },
    ],
    yAxis: [
      { scale: true, splitLine: { lineStyle: { color: "#eef1f5" } } },
      { gridIndex: 1, splitLine: { show: false }, axisLabel: { show: false } },
    ],
    dataZoom: [{ type: "inside", xAxisIndex: [0, 1], start: 55, end: 100 },
               { type: "slider", xAxisIndex: [0, 1], top: "94%", height: 18, start: 55, end: 100 }],
    series,
  });
}

function drawValuation(el, v, field, name, linesKey) {
  if (!v.dates?.length) { el.innerHTML = ""; return; }
  const data = v[field];
  const lines = v[linesKey] || {};
  const colors = { p10: "#059669", p25: "#10b981", p50: "#6b7280",
                   p75: "#f59e0b", p90: "#dc2626" };
  echarts.init(el).setOption({
    animation: false, title: { text: name, left: 10, top: 4, textStyle: { fontSize: 13 } },
    tooltip: { trigger: "axis" },
    grid: { left: 70, right: 20, top: 34, bottom: 50 },
    xAxis: { type: "category", data: v.dates },
    yAxis: { scale: true, splitLine: { lineStyle: { color: "#eef1f5" } } },
    dataZoom: [{ type: "inside" }, { type: "slider", bottom: 8, height: 16 }],
    series: [{ name, type: "line", data, showSymbol: false, lineStyle: { width: 1.3, color: "#2563eb" },
      markLine: { symbol: "none", label: { position: "insideEndTop", fontSize: 10 },
        data: Object.entries(lines).map(([p, val]) => ({
          yAxis: val, name: p,
          lineStyle: { color: colors[p] || "#999", type: "dashed", width: 1 },
          label: { formatter: `${p}分位 ${val}` } })) } }],
  });
}

// ---------------- 启动 ----------------
(async function init() {
  try {
    const [inds, st] = await Promise.all([api("/meta/indicators"), pollStatus()]);
    state.indicators = inds;
    state.pools = await api("/pools");
    route();
    setInterval(pollStatus, 5000);
  } catch (e) {
    $("#main").innerHTML = `<div class="card"><b>服务未就绪</b>
      <p class="muted" style="margin-top:8px">${esc(e.message)}<br>请确认服务已启动后刷新页面。</p></div>`;
  }
})();
