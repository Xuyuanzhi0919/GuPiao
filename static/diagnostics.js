const diagnosticsCards = document.querySelector("#diagnosticsCards");
const diagnosticsRuntime = document.querySelector("#diagnosticsRuntime");
const diagnosticsCandidates = document.querySelector("#diagnosticsCandidates");
const strategyFunnel = document.querySelector("#strategyFunnel");
const diagnosticsClock = document.querySelector("#diagnosticsClock");

function row(label, value) {
  return `<div class="runtime-row"><span>${label}</span><strong title="${value ?? "--"}">${value ?? "--"}</strong></div>`;
}

function duration(seconds) {
  const value = Number(seconds || 0);
  if (value < 60) return `${Math.floor(value)}s`;
  const minutes = Math.floor(value / 60);
  if (minutes < 60) return `${minutes}m`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

async function loadDiagnostics() {
  const [snapshotResponse, candidateResponse, fullHealthResponse] = await Promise.all([
    fetch("/api/snapshot"),
    fetch("/api/candidates"),
    fetch("/api/health/full"),
  ]);
  const snapshot = await snapshotResponse.json();
  const candidatePayload = await candidateResponse.json();
  const fullHealth = await fullHealthResponse.json();
  const runtime = snapshot.runtime || {};
  const session = runtime.session || {};
  const upstream = runtime.upstream_health || {};
  const health = candidatePayload.health || {};
  const nextDay = await fetch("/api/focus/next-day?limit=200").then((response) => response.json()).catch(() => ({ records: [] }));
  renderCards(runtime, upstream, candidatePayload, health, nextDay.records || [], fullHealth.components || {});
  diagnosticsRuntime.innerHTML = [
    row("状态", runtime.status),
    row("时段", session.label),
    row("时间", session.time),
    row("数据源", runtime.source),
    row("延迟", runtime.data_age_sec === null || runtime.data_age_sec === undefined ? "--" : `${runtime.data_age_sec}s`),
    row("批次", runtime.batch_count),
    row("Tick", runtime.tick_count),
    row("连接", runtime.client_count),
    row("错误", runtime.error_count),
    row("坏行", runtime.bad_row_count),
    row("运行", duration(runtime.uptime_sec)),
    row("上游", upstream.source ? `${upstream.source} ${upstream.last_error ? "异常" : "运行中"}` : "--"),
    ...(runtime.last_error ? [row("最近错误", runtime.last_error)] : []),
    ...(upstream.last_error ? [row("上游错误", upstream.last_error)] : []),
  ].join("");

  const reasons = health.filtered_reasons || {};
  diagnosticsCandidates.innerHTML = [
    row("候选源", health.source || "--"),
    row("候选数", (candidatePayload.candidates || []).length),
    row("过滤数", health.filtered_count || 0),
    row("扫描批次", health.batch_count || 0),
    row("板块数", (candidatePayload.sector_heat || health.sector_heat || []).length),
    ...Object.entries(reasons).map(([reason, count]) => row(reason, count)),
    ...(candidatePayload.error ? [row("候选错误", candidatePayload.error)] : []),
  ].join("");
  renderStrategyFunnel(candidatePayload.strategy_funnel || []);
}

function renderCards(runtime, upstream, candidatePayload, health, records, components) {
  const tracked = records.filter((item) => item.next_day_date).length;
  const main = components.main || {};
  const candidate = components.candidates || {};
  const tdx = components.tdx || {};
  const tickdb = components.tickdb || {};
  const calendar = components.calendar || {};
  const focus = components.focus_next_day || {};
  diagnosticsCards.innerHTML = [
    card("主服务", main.ok ? "正常" : runtime.status || "未知", main.ok ? "ok" : "warn", main.detail || `延迟 ${runtime.data_age_sec ?? "--"}s`),
    card("TDX", tdx.ok ? "正常" : "异常", tdx.ok ? "ok" : "bad", tdx.detail || tdx.label || "--"),
    card("候选池", candidate.ok ? "更新中" : "异常", candidate.ok ? "ok" : "warn", `${(candidatePayload.candidates || []).length}只 / 过滤${health.filtered_count || 0}`),
    card("TickDB", tickdb.ok ? "可用" : "未连接", tickdb.ok ? "ok" : "warn", tickdb.required ? tickdb.detail : "可选链路"),
    card("交易日历", calendar.ok ? "正常" : "异常", calendar.ok ? "ok" : "warn", calendar.label || "--"),
    card("次日样本", focus.ok ? `${records.length}条` : "暂无", focus.ok ? "ok" : "warn", tracked ? `${tracked}条已跟踪` : "等待次日"),
  ].join("");
}

function card(title, value, tone, meta) {
  return `<div class="status-card ${tone}">
    <span>${title}</span>
    <strong>${value}</strong>
    <small>${meta}</small>
  </div>`;
}

function renderStrategyFunnel(funnel) {
  if (!funnel.length) {
    strategyFunnel.innerHTML = `<div class="muted-line">暂无策略漏斗</div>`;
    return;
  }
  strategyFunnel.innerHTML = funnel
    .map((item) => {
      const miss = Object.entries(item.miss_reasons || {})
        .sort((a, b) => b[1] - a[1])
        .slice(0, 5)
        .map(([reason, count]) => `<span>${reason} ${count}</span>`)
        .join("");
      return `<div class="funnel-card">
        <div>
          <strong>${item.strategy_version}${item.shadow ? " 影子" : ""}</strong>
          <span>${item.label} · 总${item.total} · 命中${item.strong}</span>
        </div>
        <div class="funnel-bars">
          <span style="width:${pctWidth(item.strong, item.total)}%" class="hit"></span>
          <span style="width:${pctWidth(item.watch, item.total)}%" class="watch"></span>
          <span style="width:${pctWidth(item.caution, item.total)}%" class="caution"></span>
        </div>
        <small>${item.top_miss_reason ? `最卡：${item.top_miss_reason}` : "命中条件良好"}</small>
        <div class="filter-stats">${miss || "<span>无未命中原因</span>"}</div>
      </div>`;
    })
    .join("");
}

function pctWidth(value, total) {
  if (!total) return 0;
  return Math.max(2, (Number(value || 0) / total) * 100);
}

setInterval(() => {
  diagnosticsClock.textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false });
}, 250);

loadDiagnostics();
setInterval(loadDiagnostics, 5000);
