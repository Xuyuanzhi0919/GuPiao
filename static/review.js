const reviewNextDay = document.querySelector("#reviewNextDay");
const focusSummary = document.querySelector("#focusSummary");
const reviewReport = document.querySelector("#reviewReport");
const reviewHistory = document.querySelector("#reviewHistory");
const strategySummary = document.querySelector("#strategySummary");
const strategyAdvice = document.querySelector("#strategyAdvice");
const strategyRows = document.querySelector("#strategyRows");
const versionRows = document.querySelector("#versionRows");
const focusIntradaySummary = document.querySelector("#focusIntradaySummary");
const focusIntradayRows = document.querySelector("#focusIntradayRows");
const tuningAdvice = document.querySelector("#tuningAdvice");

function pct(value) {
  const sign = Number(value || 0) > 0 ? "+" : "";
  return `${sign}${Number(value || 0).toFixed(2)}%`;
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

function reviewClass(label) {
  if (label === "强兑现" || label === "持续走强") return "strong";
  if (label === "冲高回落" || label === "小幅兑现" || label === "小幅延续") return "watch";
  if (label === "低开走弱" || label === "触发回撤") return "weak";
  return "miss";
}

function maybeTrendPct(value) {
  return value === "" || value === null || value === undefined ? "--" : trendPct(value);
}

function reportSection(title, body) {
  return `<section class="report-section"><h3>${title}</h3>${body}</section>`;
}

function reportGrid(items) {
  return `<div class="report-grid">${items.map(([label, value]) => `<div class="report-pill"><span>${label}</span><strong>${value}</strong></div>`).join("")}</div>`;
}

function reportPairs(pairs) {
  const entries = Object.entries(pairs || {});
  if (!entries.length) return `<div class="muted-line">暂无</div>`;
  return reportGrid(entries.map(([label, value]) => [label, value]));
}

async function loadNextDay() {
  const response = await fetch("/api/focus/next-day?limit=200&include_shadow=1");
  const payload = await response.json();
  const records = payload.records || [];
  if (!records.length) {
    focusSummary.innerHTML = `<div class="muted-line">暂无可统计样本</div>`;
    reviewNextDay.innerHTML = `<tr><td colspan="8" class="empty">暂无强关注样本</td></tr>`;
    focusIntradaySummary.innerHTML = `<div class="muted-line">暂无可统计样本</div>`;
    focusIntradayRows.innerHTML = `<tr><td colspan="11" class="empty">暂无触发后表现</td></tr>`;
    return;
  }
  renderFocusSummary(records);
  renderFocusIntraday(records);
  reviewNextDay.innerHTML = records
    .map((item) => {
      const pending = !item.next_day_date;
      const ret = pending ? "--" : trendPct(item.next_return_pct);
      const label = pending ? "等待次日" : item.review_label || "未评估";
      const version = item.shadow ? ` · ${item.strategy_version}` : "";
      return `<tr>
        <td>
          <strong>${item.name} ${item.code}</strong>
          <span>${item.sector}${version}</span>
        </td>
        <td>
          <strong>${trendPct(item.trigger_change_pct)}</strong>
          <span>${item.trigger_date}</span>
        </td>
        <td>
          <em class="review-badge ${reviewClass(label)}">${label}</em>
          <span>${pending ? item.expected_next_trading_date || item.status : item.review_note || item.status}</span>
        </td>
        <td>${pending ? "--" : trendPct(item.gap_pct)}</td>
        <td>${pending ? "--" : trendPct(item.next_high_return_pct)}</td>
        <td>${ret}</td>
        <td>${pending ? "--" : trendPct(-Number(item.next_giveback_pct || 0))}</td>
        <td><strong>${pending ? "--" : item.review_score || 0}</strong></td>
      </tr>`;
    })
    .join("");
}

function renderFocusIntraday(records) {
  const tracked = records.filter((item) => Number(item.intraday_age_sec || 0) > 0);
  if (!tracked.length) {
    focusIntradaySummary.innerHTML = `<div class="muted-line">等待强关注触发后的实时行情</div>`;
    focusIntradayRows.innerHTML = `<tr><td colspan="11" class="empty">暂无触发后表现</td></tr>`;
    return;
  }

  const avg = (items, key) => items.length
    ? items.reduce((sum, item) => sum + Number(item[key] || 0), 0) / items.length
    : 0;
  const continued = tracked.filter((item) => ["持续走强", "小幅延续"].includes(item.intraday_label));
  const pulledBack = tracked.filter((item) => item.intraday_label === "冲高回落");
  const weak = tracked.filter((item) => ["触发回撤", "未延续"].includes(item.intraday_label));

  focusIntradaySummary.innerHTML = reportSection("触发后摘要", reportGrid([
    ["已跟踪", tracked.length],
    ["延续率", `${((continued.length / tracked.length) * 100).toFixed(1)}%`],
    ["冲高回落", pulledBack.length],
    ["弱延续", weak.length],
    ["平均当前", trendPct(avg(tracked, "intraday_current_return_pct"))],
    ["平均最高", trendPct(avg(tracked, "intraday_max_return_pct"))],
    ["平均最低", trendPct(avg(tracked, "intraday_min_return_pct"))],
    ["平均评分", avg(tracked, "intraday_score").toFixed(1)],
  ]));

  focusIntradayRows.innerHTML = tracked
    .sort((a, b) => Number(b.intraday_score || 0) - Number(a.intraday_score || 0))
    .map((item) => {
      const version = item.shadow ? ` · ${item.strategy_version}` : "";
      const age = Number(item.intraday_age_sec || 0);
      const ageLabel = age < 60 ? `${age}s` : `${Math.floor(age / 60)}m${age % 60 ? `${age % 60}s` : ""}`;
      const label = item.intraday_label || "未评估";
      return `<tr>
        <td>
          <strong>${item.name} ${item.code}</strong>
          <span>${item.trigger_date} · ${item.sector}${version} · ${ageLabel}</span>
        </td>
        <td><em class="review-badge ${reviewClass(label)}">${label}</em></td>
        <td>${trendPct(item.intraday_current_return_pct)}</td>
        <td>${maybeTrendPct(item.intraday_m1_return_pct)}</td>
        <td>${maybeTrendPct(item.intraday_m3_return_pct)}</td>
        <td>${maybeTrendPct(item.intraday_m5_return_pct)}</td>
        <td>${maybeTrendPct(item.intraday_m10_return_pct)}</td>
        <td>${trendPct(item.intraday_max_return_pct)}</td>
        <td>${trendPct(item.intraday_min_return_pct)}</td>
        <td><strong>${item.intraday_score || 0}</strong></td>
        <td class="reason">${item.intraday_note || "--"}</td>
      </tr>`;
    })
    .join("");
}

async function loadStrategy() {
  const [strategyResponse, candidateResponse, adviceResponse] = await Promise.all([
    fetch("/api/focus/strategy?days=30"),
    fetch("/api/candidates"),
    fetch("/api/focus/advice?limit=300"),
  ]);
  const payload = await strategyResponse.json();
  const candidatePayload = await candidateResponse.json();
  const advicePayload = await adviceResponse.json();
  const overall = payload.overall || {};
  const days = payload.days || [];
  const versions = payload.versions || [];
  renderStrategyAdvice(versions, candidatePayload.strategy_funnel || []);
  renderTuningAdvice(advicePayload);
  strategySummary.innerHTML = reportSection("最近表现", reportGrid([
    ["平均评分", overall.avg_score || 0],
    ["已评估天数", `${overall.tracked_day_count || 0}/${overall.day_count || 0}`],
    ["平均上涨率", `${Number(overall.avg_positive_rate || 0).toFixed(1)}%`],
    ["平均次日收益", trendPct(overall.avg_return_pct || 0)],
    ["平均最高收益", trendPct(overall.avg_high_return_pct || 0)],
  ]));

  versionRows.innerHTML = versions.length
    ? versions.map((item) => `<tr>
        <td>${item.strategy_version}</td>
        <td>${item.day_count}</td>
        <td>${item.tracked_day_count}</td>
        <td><strong>${item.avg_score}</strong></td>
        <td>${Number(item.avg_positive_rate || 0).toFixed(1)}%</td>
        <td>${trendPct(item.avg_return_pct || 0)}</td>
      </tr>`).join("")
    : `<tr><td colspan="6" class="empty">暂无版本对比</td></tr>`;

  if (!days.length) {
    strategyRows.innerHTML = `<tr><td colspan="10" class="empty">暂无策略样本</td></tr>`;
    return;
  }
  strategyRows.innerHTML = days
    .map((item) => `<tr>
      <td>${item.date}</td>
      <td>${item.strategy_version || "focus-v1"}</td>
      <td><strong>${item.score}</strong></td>
      <td>${item.tracked_count}/${item.sample_count}</td>
      <td>${Number(item.positive_rate || 0).toFixed(1)}%</td>
      <td>${trendPct(item.avg_return_pct)}</td>
      <td>${trendPct(item.avg_high_return_pct)}</td>
      <td>${trendPct(item.avg_low_return_pct)}</td>
      <td>${item.best_sector || "--"}</td>
      <td class="reason">${item.suggestion || "--"}</td>
    </tr>`)
    .join("");
}

function renderTuningAdvice(payload) {
  const stats = payload.stats || {};
  const advices = payload.advices || [];
  const topSectors = stats.top_sectors || [];
  const statGrid = reportGrid([
    ["盘中样本", stats.intraday_count || 0],
    ["延续率", `${Number(stats.intraday_continue_rate || 0).toFixed(1)}%`],
    ["冲高回落", `${Number(stats.intraday_pullback_rate || 0).toFixed(1)}%`],
    ["弱延续", `${Number(stats.intraday_weak_rate || 0).toFixed(1)}%`],
    ["次日样本", stats.next_day_count || 0],
    ["次日上涨率", `${Number(stats.next_positive_rate || 0).toFixed(1)}%`],
  ]);
  const adviceCards = advices.length
    ? advices.map((item) => `<article class="tuning-card ${item.kind || "keep"}">
        <strong>${item.title}</strong>
        <span>${item.problem}</span>
        <small>${item.evidence}</small>
        <em>${item.action}</em>
      </article>`).join("")
    : `<div class="muted-line">暂无调参建议</div>`;
  const sectors = topSectors.length
    ? `<div class="sector-advice">${topSectors.slice(0, 4).map((item) => `<span>${item.sector} · ${item.sample_count}只 · 盘中分 ${Number(item.avg_intraday_score || 0).toFixed(1)} · 次日分 ${Number(item.avg_review_score || 0).toFixed(1)}</span>`).join("")}</div>`
    : `<div class="muted-line">暂无板块样本</div>`;
  tuningAdvice.innerHTML = [
    reportSection("自动调参", statGrid),
    `<div class="tuning-grid">${adviceCards}</div>`,
    reportSection("优势板块", sectors),
  ].join("");
}

function renderStrategyAdvice(versions, funnel) {
  const v1 = versions.find((item) => item.strategy_version === "focus-v1");
  const v2 = versions.find((item) => item.strategy_version === "focus-v2-shadow");
  const v2Funnel = funnel.find((item) => item.strategy_version === "focus-v2-shadow");
  const advices = [];
  if (v2Funnel && v2Funnel.strong === 0) {
    advices.push(`v2 目前 0 命中，最卡条件是「${v2Funnel.top_miss_reason || "未知"}」，可考虑小幅放宽该条件。`);
  }
  if (v2 && v2.tracked_day_count && v2.avg_score > (v1?.avg_score || 0)) {
    advices.push("v2 已评估表现优于 v1，可以继续观察是否具备升级条件。");
  }
  if (v1 && v1.tracked_day_count && v1.avg_score < 50) {
    advices.push("v1 近期评分偏低，建议关注 v2 是否能降低噪音。");
  }
  if (!advices.length) {
    advices.push("策略仍在积累样本，暂不建议调整主交易规则。");
  }
  strategyAdvice.innerHTML = advices.map((item) => `<div class="advice-item">${item}</div>`).join("");
}

function renderFocusSummary(records) {
  const tracked = records.filter((item) => item.next_day_date);
  const positive = tracked.filter((item) => Number(item.next_return_pct || 0) > 0);
  const avg = (items, key) => items.length
    ? items.reduce((sum, item) => sum + Number(item[key] || 0), 0) / items.length
    : 0;
  const sectorStats = {};
  const labelStats = {};
  for (const item of tracked) {
    const sector = item.sector || "未分组";
    sectorStats[sector] ||= { count: 0, positive: 0, total: 0, high: 0 };
    sectorStats[sector].count += 1;
    sectorStats[sector].positive += Number(item.next_return_pct || 0) > 0 ? 1 : 0;
    sectorStats[sector].total += Number(item.next_return_pct || 0);
    sectorStats[sector].high += Number(item.next_high_return_pct || 0);
    const label = item.review_label || "未评估";
    labelStats[label] = (labelStats[label] || 0) + 1;
  }
  const sectorPairs = Object.entries(sectorStats)
    .sort((a, b) => b[1].count - a[1].count || b[1].total - a[1].total)
    .slice(0, 6)
    .map(([sector, item]) => [sector, `${item.count}只 / 胜率 ${((item.positive / item.count) * 100).toFixed(1)}% / 均 ${pct(item.total / item.count)}`]);
  focusSummary.innerHTML = [
    reportSection("摘要", reportGrid([
      ["样本数", records.length],
      ["已跟踪", tracked.length],
      ["次日上涨率", tracked.length ? `${((positive.length / tracked.length) * 100).toFixed(1)}%` : "--"],
      ["平均次日收益", trendPct(avg(tracked, "next_return_pct"))],
      ["平均最高收益", trendPct(avg(tracked, "next_high_return_pct"))],
      ["平均开盘", trendPct(avg(tracked, "gap_pct"))],
      ["平均回落", trendPct(-avg(tracked, "next_giveback_pct"))],
      ["平均复盘分", tracked.length ? avg(tracked, "review_score").toFixed(1) : "--"],
    ])),
    reportSection("结果分型", tracked.length ? reportPairs(labelStats) : `<div class="muted-line">等待次日数据</div>`),
    reportSection("板块表现", sectorPairs.length ? reportGrid(sectorPairs) : `<div class="muted-line">等待次日数据</div>`),
  ].join("");
}

async function loadReport() {
  const response = await fetch("/api/report");
  const payload = await response.json();
  const report = payload.report || {};
  const performance = report.performance || {};
  reviewReport.innerHTML = [
    reportSection("概览", reportGrid([
      ["信号数", report.total_signals || 0],
      ["跟踪样本", performance.total || 0],
      ["正收益率", `${Number(performance.positive_rate || 0).toFixed(1)}%`],
    ])),
    reportSection("板块", reportPairs(report.sector_counts)),
    reportSection("标签", reportPairs(report.tag_counts)),
    reportSection("等级表现", reportPairs(Object.fromEntries(Object.entries(performance.by_grade || {}).map(([grade, item]) => [grade, `${item.count}条 / ${Number(item.positive_rate || 0).toFixed(1)}%`])))),
  ].join("");
}

async function loadHistory() {
  const response = await fetch("/api/signals/history?limit=200");
  const payload = await response.json();
  const signals = payload.signals || [];
  if (!signals.length) {
    reviewHistory.innerHTML = `<div class="empty">还没有落盘的历史信号</div>`;
    return;
  }
  reviewHistory.innerHTML = signals
    .map((item) => {
      const time = new Date(item.ts * 1000).toLocaleString("zh-CN", { hour12: false });
      return `<div class="history-item">
        <span class="tag ${item.grade}">${item.grade}</span>
        <div>
          <strong>${item.name} ${item.code}</strong>
          <span>${time} · ${item.sector} · ${(item.reasons || []).slice(0, 3).join(" / ")}</span>
        </div>
        <strong>${item.score}</strong>
      </div>`;
    })
    .join("");
}

loadNextDay();
loadStrategy();
loadReport();
loadHistory();
setInterval(loadNextDay, 30000);
setInterval(loadStrategy, 30000);
setInterval(loadReport, 30000);
