const backtestForm = document.querySelector("#backtestForm");
const backtestSummary = document.querySelector("#backtestSummary");
const sectorRows = document.querySelector("#sectorRows");
const strategyBacktestRows = document.querySelector("#strategyBacktestRows");
const tradeRows = document.querySelector("#tradeRows");
const historyBacktestForm = document.querySelector("#historyBacktestForm");
const historySummary = document.querySelector("#historySummary");
const historyDateRows = document.querySelector("#historyDateRows");
const historySymbolRows = document.querySelector("#historySymbolRows");
const historySignalRows = document.querySelector("#historySignalRows");
const historyJobStatus = document.querySelector("#historyJobStatus");
let historyJobTimer = null;

function pct(value) {
  const number = Number(value || 0);
  const sign = number > 0 ? "+" : "";
  return `${sign}${number.toFixed(2)}%`;
}

function trendClass(value) {
  const number = Number(value || 0);
  if (number > 0) return "red";
  if (number < 0) return "green";
  return "flat";
}

function trendPct(value) {
  return `<span class="${trendClass(value)}">${pct(value)}</span>`;
}

function maybeTrendPct(value) {
  return value === "" || value === null || value === undefined ? "--" : trendPct(value);
}

function reportGrid(items) {
  return `<div class="report-grid">${items.map(([label, value]) => `<div class="report-pill"><span>${label}</span><strong>${value}</strong></div>`).join("")}</div>`;
}

function queryFromForm() {
  const data = new FormData(backtestForm);
  const params = new URLSearchParams();
  for (const [key, value] of data.entries()) {
    if (key === "include_shadow") {
      params.set(key, "1");
    } else {
      params.set(key, value);
    }
  }
  if (!data.has("include_shadow")) params.set("include_shadow", "0");
  return params;
}

function queryFromHistoryForm() {
  const data = new FormData(historyBacktestForm);
  const params = new URLSearchParams();
  for (const [key, value] of data.entries()) {
    if (key === "include_gem" || key === "include_star" || key === "require_limit_up") {
      params.set(key, "1");
    } else {
      params.set(key, value);
    }
  }
  if (!data.has("include_gem")) params.set("include_gem", "0");
  if (!data.has("include_star")) params.set("include_star", "0");
  if (!data.has("require_limit_up")) params.set("require_limit_up", "0");
  return params;
}

async function runBacktest() {
  backtestSummary.innerHTML = `<div class="muted-line">验证实盘样本</div>`;
  const response = await fetch(`/api/backtest/focus?${queryFromForm().toString()}`);
  const payload = await response.json();
  renderSummary(payload);
  renderGroupRows(sectorRows, payload.by_sector || [], "sector");
  renderGroupRows(strategyBacktestRows, payload.by_strategy || [], "strategy_version");
  renderTrades(payload.trades || []);
}

function renderSummary(payload) {
  const summary = payload.summary || {};
  const params = payload.params || {};
  backtestSummary.innerHTML = [
    reportGrid([
      ["样本数", summary.sample_count || 0],
      ["胜率", `${Number(summary.win_rate || 0).toFixed(1)}%`],
      ["平均收益", trendPct(summary.avg_return_pct)],
      ["最好单笔", trendPct(summary.best_return_pct)],
      ["最差单笔", trendPct(summary.worst_return_pct)],
      ["盈亏比", Number(summary.profit_factor || 0).toFixed(2)],
      ["买入", params.entry_label || "--"],
      ["卖出", params.exit_label || "--"],
    ]),
  ].join("");
}

function renderGroupRows(target, rows, labelKey) {
  if (!rows.length) {
    target.innerHTML = `<tr><td colspan="6" class="empty">暂无样本</td></tr>`;
    return;
  }
  target.innerHTML = rows.map((item) => `<tr>
    <td>${item[labelKey] || "--"}</td>
    <td>${item.sample_count || 0}</td>
    <td>${Number(item.win_rate || 0).toFixed(1)}%</td>
    <td>${trendPct(item.avg_return_pct)}</td>
    <td>${trendPct(item.best_return_pct)}</td>
    <td>${trendPct(item.worst_return_pct)}</td>
  </tr>`).join("");
}

function renderTrades(rows) {
  if (!rows.length) {
    tradeRows.innerHTML = `<tr><td colspan="11" class="empty">暂无符合条件的样本</td></tr>`;
    return;
  }
  tradeRows.innerHTML = rows.map((item) => `<tr>
    <td>${item.trigger_date || "--"}</td>
    <td>
      <strong>${item.name} ${item.code}</strong>
      <span>${item.shadow ? "影子" : "主策略"}</span>
    </td>
    <td>${item.sector || "--"}</td>
    <td>${item.strategy_version || "--"}</td>
    <td>${item.entry_label || "--"}</td>
    <td>${item.exit_label || "--"}</td>
    <td>${trendPct(item.return_pct)}</td>
    <td>${Number(item.score || 0).toFixed(1)}</td>
    <td>${Number(item.intraday_score || 0).toFixed(1)}</td>
    <td>${Number(item.review_score || 0).toFixed(1)}</td>
    <td class="reason">${item.intraday_label || item.review_label || "--"}</td>
  </tr>`).join("");
}

async function runHistoryBacktest() {
  if (historyJobTimer) clearInterval(historyJobTimer);
  historySummary.innerHTML = `<div class="muted-line">任务已提交，等待结果</div>`;
  historyDateRows.innerHTML = `<tr><td colspan="9" class="empty">扫描中</td></tr>`;
  historySymbolRows.innerHTML = `<tr><td colspan="7" class="empty">扫描中</td></tr>`;
  historySignalRows.innerHTML = `<tr><td colspan="16" class="empty">扫描中</td></tr>`;
  setJobStatus({ status: "RUNNING", progress: 0, message: "提交任务" });
  const response = await fetch(`/api/backtest/history-rapid/start?${queryFromHistoryForm().toString()}`);
  const payload = await response.json();
  const jobId = payload.job_id;
  if (!jobId) {
    setJobStatus({ status: "ERROR", progress: 0, message: "提交失败" });
    return;
  }
  historyJobTimer = setInterval(() => pollHistoryJob(jobId), 1000);
  pollHistoryJob(jobId);
}

async function pollHistoryJob(jobId) {
  const response = await fetch(`/api/backtest/history-rapid/job?id=${encodeURIComponent(jobId)}`);
  const payload = await response.json();
  const job = payload.job || {};
  setJobStatus(job);
  if (job.status === "RUNNING") return;
  clearInterval(historyJobTimer);
  historyJobTimer = null;
  if (job.status !== "DONE") {
    historySummary.innerHTML = `<div class="muted-line">${job.error || job.message || "历史回放失败"}</div>`;
    historyDateRows.innerHTML = `<tr><td colspan="9" class="empty">暂无结果</td></tr>`;
    historySymbolRows.innerHTML = `<tr><td colspan="9" class="empty">暂无结果</td></tr>`;
    historySignalRows.innerHTML = `<tr><td colspan="16" class="empty">暂无结果</td></tr>`;
    return;
  }
  const payloadResult = job.result || {};
  renderHistoryPayload(payloadResult);
}

function renderHistoryPayload(payload) {
  renderHistorySummary(payload);
  renderHistoryDates(payload.by_date || []);
  renderHistorySymbols(payload.by_symbol || []);
  renderHistorySignals(payload.signals || []);
}

function setJobStatus(job) {
  const progress = Math.max(0, Math.min(100, Number(job.progress || 0)));
  const text = job.status === "DONE"
    ? "完成"
    : job.status === "ERROR"
      ? `失败：${job.error || ""}`
      : `${job.message || "运行中"} · ${progress}%`;
  historyJobStatus.innerHTML = `<span>${text}</span><div><i style="width:${progress}%"></i></div>`;
}

function renderHistorySummary(payload) {
  const summary = payload.summary || {};
  const params = payload.params || {};
  historySummary.innerHTML = reportGrid([
    ["扫描股票", `${payload.scanned_count || 0}/${params.code_count || 0}`],
    ["日期数", params.date_count || (params.date ? 1 : 0)],
    ["触发样本", summary.sample_count || 0],
    ["1m胜率", `${Number(summary.win_1m || 0).toFixed(1)}%`],
    ["3m胜率", `${Number(summary.win_3m || 0).toFixed(1)}%`],
    ["5m均收益", trendPct(summary.avg_5m_pct)],
    ["10m均收益", trendPct(summary.avg_10m_pct)],
    ["10m均高点", trendPct(summary.avg_high_10m_pct)],
    ["次日样本", summary.next_day_count || 0],
    ["次日收盘胜率", `${Number(summary.next_close_win || 0).toFixed(1)}%`],
    ["次日收盘均值", trendPct(summary.avg_next_close_pct)],
    ["错误", payload.error_count || 0],
  ]);
}

function renderHistoryDates(rows) {
  if (!rows.length) {
    historyDateRows.innerHTML = `<tr><td colspan="9" class="empty">单日回放暂无日期对比</td></tr>`;
    return;
  }
  historyDateRows.innerHTML = rows.map((item) => `<tr>
    <td>${item.date || "--"}</td>
    <td>${item.next_day || "--"}</td>
    <td>${item.sample_count || 0}</td>
    <td>${item.scanned_count || 0}</td>
    <td>${trendPct(item.avg_5m_pct)}</td>
    <td>${trendPct(item.avg_next_open_pct)}</td>
    <td>${trendPct(item.avg_next_high_pct)}</td>
    <td>${trendPct(item.avg_next_close_pct)}</td>
    <td>${Number(item.next_close_win || 0).toFixed(1)}%</td>
  </tr>`).join("");
}

function renderHistorySymbols(rows) {
  if (!rows.length) {
    historySymbolRows.innerHTML = `<tr><td colspan="9" class="empty">暂无触发股票</td></tr>`;
    return;
  }
  historySymbolRows.innerHTML = rows.map((item) => `<tr>
    <td>${item.symbol || item.code}</td>
    <td>${item.sample_count || 0}</td>
    <td>${Number(item.win_1m || 0).toFixed(1)}%</td>
    <td>${Number(item.win_3m || 0).toFixed(1)}%</td>
    <td>${trendPct(item.avg_5m_pct)}</td>
    <td>${trendPct(item.avg_10m_pct)}</td>
    <td>${item.next_day_count || 0}</td>
    <td>${trendPct(item.avg_next_close_pct)}</td>
    <td>${trendPct(item.avg_high_10m_pct)}</td>
  </tr>`).join("");
}

function compactAmount(value) {
  const number = Number(value || 0);
  if (number >= 100000000) return `${(number / 100000000).toFixed(2)}亿`;
  if (number >= 10000) return `${(number / 10000).toFixed(0)}万`;
  return number.toFixed(0);
}

function renderHistorySignals(rows) {
  if (!rows.length) {
    historySignalRows.innerHTML = `<tr><td colspan="16" class="empty">没有符合条件的快速拉升</td></tr>`;
    return;
  }
  historySignalRows.innerHTML = rows.map((item) => `<tr>
    <td>${item.time || "--"}</td>
    <td>${item.symbol || item.code}</td>
    <td>${Number(item.entry_price || 0).toFixed(3)}</td>
    <td>${trendPct(item.rise_1m_pct)}</td>
    <td>${trendPct(item.rise_3m_pct)}</td>
    <td>${item.limit_up ? item.limit_up_time || "是" : "--"}</td>
    <td>${compactAmount(item.amount_2m)}</td>
    <td>${trendPct(item.ret_1m_pct)}</td>
    <td>${trendPct(item.ret_3m_pct)}</td>
    <td>${trendPct(item.ret_5m_pct)}</td>
    <td>${trendPct(item.ret_10m_pct)}</td>
    <td>高 ${trendPct(item.high_10m_pct)} / 低 ${trendPct(item.low_10m_pct)}</td>
    <td>${item.next_close_return_pct === "" || item.next_close_return_pct === null || item.next_close_return_pct === undefined ? "缺失" : item.next_day_date || "有"}</td>
    <td>${maybeTrendPct(item.next_open_return_pct)}</td>
    <td>${maybeTrendPct(item.next_high_return_pct)}</td>
    <td>${maybeTrendPct(item.next_close_return_pct)}</td>
  </tr>`).join("");
}

function defaultHistoryDate() {
  const input = historyBacktestForm?.querySelector('input[name="date"]');
  if (!input) return;
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60000;
  input.value = new Date(now.getTime() - offset).toISOString().slice(0, 10);
}

backtestForm.addEventListener("submit", (event) => {
  event.preventDefault();
  runBacktest();
});

historyBacktestForm.addEventListener("submit", (event) => {
  event.preventDefault();
  runHistoryBacktest();
});

defaultHistoryDate();
runBacktest();
