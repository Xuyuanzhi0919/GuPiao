const rows = document.querySelector("#signalRows");
const sectorList = document.querySelector("#sectorList");
const alertList = document.querySelector("#alertList");
const trackList = document.querySelector("#trackList");
const nextDayList = document.querySelector("#nextDayList");
const signalCount = document.querySelector("#signalCount");
const gradeACount = document.querySelector("#gradeACount");
const hotSector = document.querySelector("#hotSector");
const latestSignal = document.querySelector("#latestSignal");
const trackCount = document.querySelector("#trackCount");
const positiveRate = document.querySelector("#positiveRate");
const performanceList = document.querySelector("#performanceList");
const connection = document.querySelector("#connection");
const pulse = document.querySelector("#pulse");
const dataAge = document.querySelector("#dataAge");
const clock = document.querySelector("#clock");
const runtimeList = document.querySelector("#runtimeList");
const historyButton = document.querySelector("#historyButton");
const reportButton = document.querySelector("#reportButton");
const soundButton = document.querySelector("#soundButton");
const historyDialog = document.querySelector("#historyDialog");
const closeHistory = document.querySelector("#closeHistory");
const historyList = document.querySelector("#historyList");
const reportDialog = document.querySelector("#reportDialog");
const closeReport = document.querySelector("#closeReport");
const reportContent = document.querySelector("#reportContent");
const gradeFilter = document.querySelector("#gradeFilter");
const sectorFilter = document.querySelector("#sectorFilter");
const scoreFilter = document.querySelector("#scoreFilter");
const turnoverFilter = document.querySelector("#turnoverFilter");
const watchOnlyFilter = document.querySelector("#watchOnlyFilter");
const showBlockedFilter = document.querySelector("#showBlockedFilter");
const configForm = document.querySelector("#configForm");
const resetConfigButton = document.querySelector("#resetConfigButton");
const universeForm = document.querySelector("#universeForm");
const universeCode = document.querySelector("#universeCode");
const universeListView = document.querySelector("#universeListView");
const sectorForm = document.querySelector("#sectorForm");
const sectorNameInput = document.querySelector("#sectorNameInput");
const sectorCodeInput = document.querySelector("#sectorCodeInput");
const sectorConfigList = document.querySelector("#sectorConfigList");
const candidateStatus = document.querySelector("#candidateStatus");
const filterStats = document.querySelector("#filterStats");
const candidateList = document.querySelector("#candidateList");
const focusList = document.querySelector("#focusList");
const candidateQualityFilter = document.querySelector("#candidateQualityFilter");
const alertItems = [];
let latestSignals = [];
let latestCandidates = [];
let latestCandidateSectors = [];
let activeGrade = "ALL";
let activeCandidateQuality = "ALL";
let soundEnabled = localStorage.getItem("radarSoundEnabled") === "1";
let audioContext = null;
let titleTimer = null;
const watchlist = new Set(JSON.parse(localStorage.getItem("radarWatchlist") || "[]"));
const blocklist = new Set(JSON.parse(localStorage.getItem("radarBlocklist") || "[]"));

const money = new Intl.NumberFormat("zh-CN", {
  maximumFractionDigits: 1,
});

function formatMoney(value) {
  if (value >= 100000000) return `${money.format(value / 100000000)}亿`;
  return `${money.format(value / 10000)}万`;
}

function sparkline(points) {
  if (!points || points.length < 2) return `<span class="muted-line">--</span>`;
  const width = 96;
  const height = 30;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = Math.max(max - min, 0.01);
  const step = width / (points.length - 1);
  const path = points
    .map((price, index) => {
      const x = index * step;
      const y = height - ((price - min) / span) * (height - 4) - 2;
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");
  const baseY = height - ((points[0] - min) / span) * (height - 4) - 2;
  return `<svg class="sparkline" viewBox="0 0 ${width} ${height}" aria-hidden="true">
    <path class="base" d="M0 ${baseY.toFixed(1)} L${width} ${baseY.toFixed(1)}"></path>
    <path d="${path}"></path>
  </svg>`;
}

function qualityTags(tags) {
  if (!tags || !tags.length) return "";
  return `<div class="tags">${tags
    .map((tag) => {
      const risk = ["冲高回落", "分歧放大", "临近涨停"].includes(tag);
      return `<span class="quality-tag ${risk ? "risk" : ""}">${tag}</span>`;
    })
    .join("")}</div>`;
}

function pct(value) {
  const sign = value > 0 ? "+" : "";
  return `${sign}${Number(value).toFixed(2)}%`;
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

function render(payload) {
  latestSignals = payload.signals || [];
  updateSectorOptions(latestSignals);
  const filteredSignals = applyFilters(latestSignals);
  latestSignal.textContent = payload.new_signals?.[0]
    ? `${payload.new_signals[0].name} ${payload.new_signals[0].grade}`
    : filteredSignals[0]
      ? `${filteredSignals[0].name} ${filteredSignals[0].grade}`
      : "--";

  renderSectors(payload.sector_heat || {});
  renderRuntime(payload.runtime || {});
  renderConfig(payload.config || {});
  renderAlerts(payload.new_signals || []);
  renderTracks(payload.tracked_alerts || []);
  renderPerformance(payload.performance || {});
  notifyNewSignals(payload.new_signals || []);
  renderFilteredView(filteredSignals);
}

function renderConfig(config) {
  if (!configForm || configForm.dataset.loaded === "1") return;
  for (const element of configForm.elements) {
    if (!element.name || config[element.name] === undefined) continue;
    element.value = config[element.name];
  }
  configForm.dataset.loaded = "1";
}

function renderRuntime(runtime) {
  const age = runtime.data_age_sec;
  const ageText = age === null || age === undefined ? "--" : `${Number(age).toFixed(1)}s`;
  const status = runtime.status || "STALE";
  const session = runtime.session || {};
  const upstream = runtime.upstream_health || {};
  const upstreamRows = upstream.source ? upstreamRuntimeRows(upstream) : [];
  dataAge.textContent = `${session.label || "时段--"} ${status} ${ageText}`;
  dataAge.classList.toggle("stale", status !== "OK");

  if (!runtime.source) {
    runtimeList.innerHTML = `<div class="muted-line">等待行情</div>`;
    return;
  }

  runtimeList.innerHTML = [
    ["状态", status],
    ["时段", session.label || "--"],
    ["时间", session.time || "--"],
    ["数据源", runtime.source],
    ["延迟", ageText],
    ["批次", runtime.batch_count || 0],
    ["Tick", runtime.tick_count || 0],
    ["连接", runtime.client_count || 0],
    ["坏行", runtime.bad_row_count || 0],
    ["错误", runtime.error_count || 0],
    ["重试", runtime.retry_count || 0],
    ["运行", formatDuration(runtime.uptime_sec || 0)],
    ...upstreamRows,
    ...(runtime.last_bad_row_error ? [["坏行错误", runtime.last_bad_row_error]] : []),
    ...(runtime.last_error ? [["最近错误", runtime.last_error]] : []),
  ]
    .map(([label, value]) => `<div class="runtime-row"><span>${label}</span><strong title="${value}">${value}</strong></div>`)
    .join("");
}

function upstreamRuntimeRows(upstream) {
  if (upstream.source === "opentdx") {
    return [
      ["上游", `${upstream.source} ${upstream.last_error ? "异常" : "运行中"}`],
      ["候选", upstream.tick_count || 0],
      ["扫描批次", upstream.batch_count || 0],
      ["过滤", upstream.filtered_count || 0],
      ...(upstream.last_error ? [["上游错误", upstream.last_error]] : []),
    ];
  }

  const upstreamState = upstream.connected
    ? (upstream.tick_count || 0) > 0
      ? "已连接"
      : "已连接/无数据"
    : "未连接";
  return [
    ["上游", `${upstream.source} ${upstreamState}`],
    ["订阅", `${upstream.symbol_count || (upstream.symbols || []).length}只`],
    ["上游Tick", upstream.tick_count || 0],
    ...(upstream.last_raw_message ? [["上游消息", upstream.last_raw_message]] : []),
    ...(upstream.last_error ? [["上游错误", upstream.last_error]] : []),
  ];
}

async function loadCandidates() {
  if (!candidateList) return;
  try {
    const response = await fetch("/api/candidates");
    const payload = await response.json();
    renderCandidates(payload);
  } catch (error) {
    candidateStatus.textContent = "候选池异常";
    candidateList.innerHTML = `<div class="muted-line">${error.message}</div>`;
  }
}

async function loadNextDayFocus() {
  if (!nextDayList) return;
  try {
    const response = await fetch("/api/focus/next-day?limit=80");
    const payload = await response.json();
    renderNextDayFocus(payload.records || []);
  } catch (error) {
    nextDayList.innerHTML = `<div class="muted-line">${error.message}</div>`;
  }
}

function renderCandidates(payload) {
  const health = payload.health || {};
  latestCandidates = payload.candidates || [];
  const reasons = health.filtered_reasons || {};
  latestCandidateSectors = payload.sector_heat || health.sector_heat || [];
  if (latestCandidateSectors.length) renderCandidateSectors(latestCandidateSectors);
  updateSectorOptions(latestSignals);
  const candidates = applyCandidateFilters(latestCandidates);
  candidateStatus.textContent = payload.error
    ? payload.error
    : `${health.source || "--"} · ${candidates.length}/${latestCandidates.length}只 · 过滤${health.filtered_count || 0}`;
  filterStats.innerHTML = Object.entries(reasons).length
    ? Object.entries(reasons)
        .map(([reason, count]) => `<span>${filterReasonName(reason)} ${count}</span>`)
        .join("")
    : `<span>无过滤项</span>`;

  renderCandidateList(candidates);
}

function renderCandidateSectors(sectors) {
  hotSector.textContent = sectors[0] ? `${sectors[0].sector} ${sectors[0].count}` : "--";
  const current = sectorFilter.value;
  const visible = sectors.slice(0, 6);
  const max = Math.max(...visible.map((item) => item.count), 1);
  sectorList.innerHTML = visible
    .map((item) => {
      const width = Math.max(8, (item.count / max) * 100);
      const active = item.active_top ? `<em>${item.active_top}精盯</em>` : "";
      const selected = current === item.sector;
      return `<button class="sector ${selected ? "selected" : ""}" type="button" data-sector="${item.sector}" title="筛选 ${item.sector}">
        <span class="sector-name">${item.sector}${active}</span>
        <strong>${item.count}</strong>
        <small>均${Number(item.avg_score || 0).toFixed(0)} · 高${Number(item.max_score || 0).toFixed(0)}</small>
        <div class="sector-bar"><span style="width:${width}%"></span></div>
      </button>`;
    })
    .join("") + (sectors.length > visible.length ? `<div class="muted-line">其余 ${sectors.length - visible.length} 个板块已折叠</div>` : "");
}

function topReasonName(reason) {
  if (reason === "alert") return "报警保留";
  if (reason === "score") return "精盯中";
  return "精盯中";
}

function filterReasonName(reason) {
  const names = {
    st: "ST",
    new_stock: "新股",
    bj: "北交所",
    gem: "创业板",
    star: "科创板",
    gem_change: "创业板涨幅",
    gem_speed_noise: "创业板噪音",
    gem_turnover: "创业板换手",
    bad_price: "价格异常",
    main_weak_change: "主板弱涨幅",
    main_high: "主板高位",
    main_turnover: "主板换手",
    main_amount: "主板额弱",
    main_buy_weak: "主买弱",
    main_spike: "瞬时尖峰",
  };
  return names[reason] || reason;
}

function candidateScore(item) {
  if (item.candidate_score !== undefined) return Math.round(item.candidate_score);
  const score =
    Number(item.rise_speed_pct || 0) * 35 +
    Math.min(Number(item.min2_amount || 0) / 1_000_000, 30) +
    Number(item.vol_rise_speed_pct || 0) * 2 +
    Number(item.short_turnover_pct || 0) * 1.5 +
    Math.max(Number(item.active_buy_ratio || 0) - 0.5, 0) * 20;
  return Math.round(score);
}

async function loadUniverse() {
  const response = await fetch("/api/universe");
  const payload = await response.json();
  renderUniverse(payload.universe || {});
}

async function loadSectorConfig() {
  const response = await fetch("/api/sectors");
  const payload = await response.json();
  renderSectorConfig(payload.sectors || {});
}

function renderSectorConfig(sectors) {
  const entries = Object.entries(sectors).sort((a, b) => a[0].localeCompare(b[0], "zh-CN"));
  if (!entries.length) {
    sectorConfigList.innerHTML = `<div class="muted-line">空</div>`;
    return;
  }
  sectorConfigList.innerHTML = entries
    .map(([sector, codes]) => {
      const chips = codes
        .map((code) => `<span class="code-chip">${code}<button type="button" data-sector="${sector}" data-code="${code}">x</button></span>`)
        .join("");
      return `<div class="sector-config-group"><div class="sector-config-title">${sector}</div>${chips}</div>`;
    })
    .join("");
}

function renderUniverse(universe) {
  const include = universe.include || [];
  const exclude = universe.exclude || [];
  universeListView.innerHTML = [
    universeGroup("关注池", "include", include),
    universeGroup("排除池", "exclude", exclude),
  ].join("");
}

function universeGroup(title, listName, codes) {
  const chips = codes.length
    ? codes.map((code) => `<span class="code-chip">${code}<button type="button" data-list="${listName}" data-code="${code}">x</button></span>`).join("")
    : `<div class="muted-line">空</div>`;
  return `<div class="universe-group"><div class="universe-title">${title}</div>${chips}</div>`;
}

function formatDuration(seconds) {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

function renderFilteredView(filteredSignals = applyFilters(latestSignals)) {
  signalCount.textContent = `${filteredSignals.length}/${latestSignals.length}`;
  gradeACount.textContent = filteredSignals.filter((item) => item.grade === "A").length;
  renderRows(filteredSignals);
  renderFocusList();
  if (latestCandidates.length) renderCandidateList(applyCandidateFilters(latestCandidates));
}

function applyFilters(signals) {
  const gradeRank = { C: 1, B: 2, A: 3 };
  const minGrade = activeGrade === "ALL" ? 0 : gradeRank[activeGrade];
  const sector = sectorFilter.value;
  const minScore = Number(scoreFilter.value || 0);
  const minTurnover = Number(turnoverFilter.value || 0) * 10000;
  const watchOnly = watchOnlyFilter.checked;
  const showBlocked = showBlockedFilter.checked;

  return signals.filter((item) => {
    if (!showBlocked && blocklist.has(item.code)) return false;
    if (watchOnly && !watchlist.has(item.code)) return false;
    if (gradeRank[item.grade] < minGrade) return false;
    if (sector !== "ALL" && item.sector !== sector) return false;
    if (item.score < minScore) return false;
    if (item.turnover_1m < minTurnover) return false;
    return true;
  });
}

function applyCandidateFilters(candidates) {
  return candidates.filter((item) => {
    if (!isCandidateVisible(item)) return false;
    if (activeCandidateQuality !== "ALL" && candidateQuality(item).level !== activeCandidateQuality) return false;
    return true;
  }).sort(compareCandidates);
}

function isCandidateVisible(item) {
  const sector = sectorFilter.value;
  const showBlocked = showBlockedFilter.checked;
  if (!showBlocked && blocklist.has(item.code)) return false;
  if (sector !== "ALL" && item.sector !== sector) return false;
  return true;
}

function compareCandidates(left, right) {
  const rank = { strong: 3, watch: 2, caution: 1 };
  const leftQuality = candidateQuality(left).level;
  const rightQuality = candidateQuality(right).level;
  const qualityDelta = (rank[rightQuality] || 0) - (rank[leftQuality] || 0);
  if (qualityDelta !== 0) return qualityDelta;
  return candidateAdjustedScore(right) - candidateAdjustedScore(left);
}

function sectorPulse(sector) {
  const sectorItem = latestCandidateSectors.find((item) => item.sector === sector);
  if (sectorItem && sectorItem.count >= 3) return `${sectorItem.sector}${sectorItem.count}只共振`;
  return "";
}

function candidateExplanation(item) {
  if (item.explanation) return item.explanation;
  const positives = [];
  const cautions = [];
  const pulseText = sectorPulse(item.sector);
  if (pulseText) positives.push(pulseText);
  if (Number(item.rise_speed_pct || 0) >= 1.2) positives.push("涨速启动");
  if (Number(item.min2_amount || 0) >= 10_000_000) positives.push("2分钟放量");
  if (Number(item.active_buy_ratio || 0) >= 0.58) positives.push("主买占优");
  if (Number(item.change_pct || 0) >= 1 && Number(item.change_pct || 0) <= 5) positives.push("涨幅适中");
  if (Number(item.turnover_rate || 0) <= 10) positives.push("换手可控");

  if (Number(item.rise_speed_pct || 0) >= 4) cautions.push("瞬时尖峰");
  if (Number(item.active_buy_ratio || 0) < 0.45) cautions.push("主买不足");
  if (Number(item.turnover_rate || 0) >= 14) cautions.push("换手偏高");
  if (Number(item.change_pct || 0) >= 7) cautions.push("位置偏高");

  const lead = cautions.length ? "谨慎" : pulseText ? "板块共振" : "观察";
  const body = positives.length ? positives.slice(0, 4).join(" + ") : (item.candidate_reasons || []).slice(0, 3).join(" + ");
  return `${lead}：${body || "等待量价确认"}${cautions.length ? `；${cautions.slice(0, 2).join("、")}` : ""}`;
}

function candidateRiskFlags(item) {
  if (Array.isArray(item.risk_flags)) return item.risk_flags;
  const flags = [];
  if (Number(item.rise_speed_pct || 0) >= 4) flags.push("瞬时尖峰");
  if (Number(item.active_buy_ratio || 0) < 0.45) flags.push("主买不足");
  if (Number(item.turnover_rate || 0) >= 14) flags.push("换手偏高");
  if (Number(item.change_pct || 0) >= 7) flags.push("位置偏高");
  return flags;
}

function candidateQuality(item) {
  if (item.quality_level && item.quality_label) {
    return { level: item.quality_level, label: item.quality_label, flags: candidateRiskFlags(item) };
  }
  const pulseText = sectorPulse(item.sector);
  const riskFlags = candidateRiskFlags(item);
  const hasVolume = Number(item.min2_amount || 0) >= 10_000_000;
  const hasBuy = Number(item.active_buy_ratio || 0) >= 0.46;
  const hasStart = Number(item.rise_speed_pct || 0) >= 1;
  if (riskFlags.length) return { level: "caution", label: "谨慎", flags: riskFlags };
  if (hasStart && hasVolume && (pulseText || hasBuy)) return { level: "strong", label: "强关注", flags: [] };
  return { level: "watch", label: "观察", flags: [] };
}

function candidateAdjustedScore(item) {
  if (item.adjusted_score !== undefined) return Number(item.adjusted_score);
  const quality = candidateQuality(item);
  const pulseBonus = sectorPulse(item.sector) ? 6 : 0;
  const buyBonus = Number(item.active_buy_ratio || 0) >= 0.58 ? 4 : 0;
  const volumeBonus = Number(item.min2_amount || 0) >= 20_000_000 ? 3 : 0;
  const riskPenalty = quality.flags.length * 10;
  return candidateScore(item) + pulseBonus + buyBonus + volumeBonus - riskPenalty;
}

function signalExplanation(item) {
  const positives = [];
  const cautions = [];
  if (Number(item.sector_heat || 0) >= 2) positives.push(`${item.sector}${item.sector_heat}只共振`);
  if (Number(item.rise_1m_pct || 0) >= 1) positives.push("1分钟拉升");
  if (Number(item.rise_3m_pct || 0) >= 1.8) positives.push("3分钟趋势延续");
  if (Number(item.volume_spike || 0) >= 2) positives.push("量能突增");
  if (Number(item.active_buy_ratio || 0) >= 0.58) positives.push("主动买入占优");
  if (Number(item.order_book_bias || 0) >= 0.12) positives.push("买盘承接强");

  for (const tag of item.quality_tags || []) {
    if (["冲高回落", "分歧放大", "临近涨停"].includes(tag)) cautions.push(tag);
  }
  if (Number(item.active_buy_ratio || 0) < 0.5) cautions.push("主买不足");
  if (Number(item.volume_spike || 0) < 1.4) cautions.push("量能一般");

  const lead = cautions.length ? "谨慎" : Number(item.sector_heat || 0) >= 2 ? "板块共振" : "刚启动";
  const body = positives.length ? positives.slice(0, 4).join(" + ") : (item.reasons || []).slice(0, 3).join(" + ");
  return `${lead}：${body || "等待持续性确认"}${cautions.length ? `；${[...new Set(cautions)].slice(0, 2).join("、")}` : ""}`;
}

function renderCandidateList(candidates) {
  if (!candidateList) return;
  if (!candidates.length) {
    candidateList.innerHTML = `<div class="muted-line">${sectorFilter.value === "ALL" ? "暂无候选" : "当前板块暂无候选"}</div>`;
    return;
  }

  candidateList.innerHTML = candidates
    .slice(0, 10)
    .map((item, index) => {
      const score = candidateScore(item);
      const adjustedScore = candidateAdjustedScore(item);
      const quality = candidateQuality(item);
      const active = item.top_status === "active";
      const cooling = item.top_status === "cooldown";
      const hold = active && item.top_age_sec !== undefined ? ` ${formatDuration(Math.floor(item.top_age_sec))}` : "";
      const state = active
        ? `${topReasonName(item.top_reason)}${hold}`
        : cooling
          ? `冷却中 ${formatDuration(Math.floor(item.cooldown_sec || 0))}`
          : "";
      return `<div class="candidate-card">
        <div class="candidate-card-head">
          <span class="rank">${index + 1}</span>
          <div class="candidate-stock">
            <strong>${item.name}<b class="quality ${quality.level}">${quality.label}</b>${state ? `<em class="${cooling ? "cooling" : ""}">${state}</em>` : ""}</strong>
            <span>${item.code} · ${item.sector || item.board}</span>
          </div>
          <strong class="candidate-score" title="原始分 ${score}">${Math.round(adjustedScore)}</strong>
        </div>
        <div class="candidate-card-metrics">
          <span><small>涨速</small>${trendPct(item.rise_speed_pct)}</span>
          <span><small>2m额</small><b>${formatMoney(item.min2_amount || 0)}</b></span>
          <span><small>主买</small><b>${Math.round((item.active_buy_ratio || 0) * 100)}%</b></span>
          <span><small>换手</small><b>${Number(item.turnover_rate || 0).toFixed(1)}%</b></span>
        </div>
        <div class="candidate-card-reason">${candidateExplanation(item)}</div>
      </div>`;
    })
    .join("");
}

function renderFocusList() {
  if (!focusList) return;
  const signalCodes = new Set(latestSignals.map((item) => item.code));
  const focus = latestCandidates
    .filter((item) => isCandidateVisible(item))
    .filter((item) => candidateQuality(item).level === "strong" || signalCodes.has(item.code))
    .sort(compareCandidates)
    .slice(0, 5);

  if (!focus.length) {
    focusList.innerHTML = `<div class="muted-line">暂无重点盯盘</div>`;
    return;
  }

  focusList.innerHTML = focus
    .map((item) => {
      const quality = candidateQuality(item);
      const live = signalCodes.has(item.code) ? `<em>异动</em>` : "";
      return `<div class="focus-item">
        <div class="focus-stock"><strong>${item.name}<b class="quality ${quality.level}">${quality.label}</b>${live}</strong><span>${item.code} · ${item.sector || item.board}</span></div>
        <span>${trendPct(item.rise_speed_pct)}</span>
        <span>主买${Math.round((item.active_buy_ratio || 0) * 100)}%</span>
        <small>${candidateExplanation(item)}</small>
      </div>`;
    })
    .join("");
}

function renderNextDayFocus(records) {
  if (!records.length) {
    nextDayList.innerHTML = `<div class="muted-line">暂无强关注样本</div>`;
    return;
  }
  nextDayList.innerHTML = records
    .slice(0, 10)
    .map((item) => {
      const pending = !item.next_day_date;
      const status = pending ? "等待次日" : item.status || "次日跟踪中";
      const returnText = pending ? "--" : trendPct(item.next_return_pct || 0);
      const rangeText = pending
        ? "次日未开始"
        : `高 ${trendPct(item.next_high_return_pct || 0)} / 低 ${trendPct(item.next_low_return_pct || 0)}`;
      return `<div class="nextday-item">
        <div><strong>${item.name}</strong><span>${item.code} · ${item.sector}</span></div>
        <strong>${returnText}</strong>
        <small>${item.trigger_date} · ${status} · ${rangeText}</small>
      </div>`;
    })
    .join("");
}

function updateSectorOptions(signals) {
  const sectors = [...new Set([
    ...signals.map((item) => item.sector),
    ...latestCandidates.map((item) => item.sector),
    ...latestCandidateSectors.map((item) => item.sector),
  ].filter(Boolean))].sort();
  const current = sectorFilter.value;
  const options = [`<option value="ALL">全部</option>`]
    .concat(sectors.map((sector) => `<option value="${sector}">${sector}</option>`))
    .join("");
  if (sectorFilter.innerHTML !== options) {
    sectorFilter.innerHTML = options;
    sectorFilter.value = sectors.includes(current) ? current : "ALL";
  }
}

function renderAlerts(newSignals) {
  for (const item of newSignals) {
    if (blocklist.has(item.code) && !showBlockedFilter.checked) continue;
    alertItems.unshift(item);
  }
  alertItems.splice(30);

  if (!alertItems.length) {
    alertList.innerHTML = `<div class="muted-line">等待新信号</div>`;
    return;
  }

  alertList.innerHTML = alertItems
    .map((item) => {
      const time = new Date(item.ts * 1000).toLocaleTimeString("zh-CN", { hour12: false });
      return `<div class="alert">
        <span class="tag ${item.grade}">${item.grade}</span>
        <div>
          <strong>${item.name} ${item.score}</strong>
          <span>${time} · ${item.sector}</span>
        </div>
      </div>`;
    })
    .join("");
}

function renderTracks(tracks) {
  const visibleTracks = tracks.filter((item) => !blocklist.has(item.code) || showBlockedFilter.checked);
  if (!visibleTracks.length) {
    trackList.innerHTML = `<div class="muted-line">等待报警后表现</div>`;
    return;
  }

  trackList.innerHTML = visibleTracks
    .slice(0, 12)
    .map((item) => {
      const age = item.age_sec < 60 ? `${item.age_sec}s` : `${Math.floor(item.age_sec / 60)}m`;
      return `<div class="track">
        <div class="track-head">
          <strong>${item.name}</strong>
          ${trendPct(item.current_return_pct)}
        </div>
        <div class="track-stats">
          <span>高 ${trendPct(item.max_return_pct)}</span>
          <span>低 ${trendPct(item.min_return_pct)}</span>
          <span>${age}</span>
        </div>
      </div>`;
    })
    .join("");
}

function renderPerformance(performance) {
  trackCount.textContent = performance.total || 0;
  positiveRate.textContent = `${Number(performance.positive_rate || 0).toFixed(1)}%`;

  const entries = Object.entries(performance.by_grade || {});
  if (!entries.length) {
    performanceList.innerHTML = `<div class="muted-line">等待统计样本</div>`;
    return;
  }

  performanceList.innerHTML = entries
    .map(([grade, item]) => {
      return `<div class="performance-row">
        <span class="tag ${grade}">${grade}</span>
        <div>
          <div class="track-head">
            <strong>${item.count} 条</strong>
            <span>${Number(item.positive_rate).toFixed(1)}%</span>
          </div>
          <div class="performance-grid">
            <span>现 ${trendPct(item.avg_current_return_pct)}</span>
            <span>高 ${trendPct(item.avg_max_return_pct)}</span>
            <span>低 ${trendPct(item.avg_min_return_pct)}</span>
          </div>
        </div>
      </div>`;
    })
    .join("");
}

function updateSoundButton() {
  if (!soundButton) return;
  soundButton.textContent = soundEnabled ? "声音开" : "声音关";
  soundButton.classList.toggle("enabled", soundEnabled);
  soundButton.setAttribute("aria-pressed", String(soundEnabled));
}

function toggleSound() {
  soundEnabled = !soundEnabled;
  localStorage.setItem("radarSoundEnabled", soundEnabled ? "1" : "0");
  if (soundEnabled) {
    const AudioEngine = window.AudioContext || window.webkitAudioContext;
    if (!AudioEngine) {
      soundEnabled = false;
      localStorage.setItem("radarSoundEnabled", "0");
      updateSoundButton();
      return;
    }
    audioContext = audioContext || new AudioEngine();
    playTone("B");
  }
  updateSoundButton();
}

function notifyNewSignals(newSignals) {
  const visibleSignals = newSignals.filter((item) => !blocklist.has(item.code) || showBlockedFilter.checked);
  if (!visibleSignals.length) return;
  const strongest = [...visibleSignals].sort((a, b) => b.score - a.score)[0];
  showTitleAlert(strongest);

  if (!soundEnabled) return;
  if (!["A", "B"].includes(strongest.grade)) return;
  playTone(strongest.grade);
}

function showTitleAlert(signal) {
  clearTimeout(titleTimer);
  document.title = `${signal.grade} ${signal.name} ${signal.score} | A股分时异动雷达`;
  titleTimer = setTimeout(() => {
    document.title = "A股分时异动雷达";
  }, 12000);
}

function playTone(grade) {
  const AudioEngine = window.AudioContext || window.webkitAudioContext;
  if (!AudioEngine) return;
  audioContext = audioContext || new AudioEngine();
  const now = audioContext.currentTime;
  const gain = audioContext.createGain();
  gain.connect(audioContext.destination);
  gain.gain.setValueAtTime(0.0001, now);
  gain.gain.exponentialRampToValueAtTime(0.12, now + 0.03);
  gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.34);

  const oscillator = audioContext.createOscillator();
  oscillator.type = "sine";
  oscillator.frequency.setValueAtTime(grade === "A" ? 880 : 660, now);
  oscillator.connect(gain);
  oscillator.start(now);
  oscillator.stop(now + 0.36);

  if (grade === "A") {
    const second = audioContext.createOscillator();
    second.type = "sine";
    second.frequency.setValueAtTime(1046, now + 0.18);
    second.connect(gain);
    second.start(now + 0.18);
    second.stop(now + 0.48);
  }
}

function persistLists() {
  localStorage.setItem("radarWatchlist", JSON.stringify([...watchlist]));
  localStorage.setItem("radarBlocklist", JSON.stringify([...blocklist]));
}

function toggleList(action, code) {
  if (action === "watch") {
    if (watchlist.has(code)) {
      watchlist.delete(code);
    } else {
      watchlist.add(code);
      blocklist.delete(code);
    }
  }
  if (action === "block") {
    if (blocklist.has(code)) {
      blocklist.delete(code);
    } else {
      blocklist.add(code);
      watchlist.delete(code);
    }
  }
  persistLists();
  renderFilteredView();
}

function renderSectors(heat) {
  if (latestCandidateSectors.length) return;
  const entries = Object.entries(heat).sort((a, b) => b[1] - a[1]);
  hotSector.textContent = entries[0] ? `${entries[0][0]} ${entries[0][1]}` : "--";

  if (!entries.length) {
    sectorList.innerHTML = `<div class="sector"><span class="sector-name">暂无板块共振</span><span>0</span><div class="sector-bar"><span style="width:0%"></span></div></div>`;
    return;
  }

  const max = Math.max(...entries.map((entry) => entry[1]), 1);
  sectorList.innerHTML = entries
    .map(([sector, count]) => {
      const width = Math.max(8, (count / max) * 100);
      return `<div class="sector">
        <span class="sector-name">${sector}</span>
        <strong>${count}</strong>
        <div class="sector-bar"><span style="width:${width}%"></span></div>
      </div>`;
    })
    .join("");
}

function renderRows(signals) {
  if (!signals.length) {
    rows.innerHTML = `<tr><td colspan="11" class="empty">没有符合当前过滤条件的信号</td></tr>`;
    return;
  }

  rows.innerHTML = signals
    .map((item) => {
      const buyClass = item.active_buy_ratio >= 0.6 ? "red" : "flat";
      const watched = watchlist.has(item.code);
      const blocked = blocklist.has(item.code);
      const signalAge = item.signal_age_sec === undefined ? "--" : `${formatDuration(Math.floor(item.signal_age_sec))}前`;
      const explanation = signalExplanation(item);
      return `<tr class="signal-main ${blocked ? "blocked" : ""}">
        <td><span class="tag ${item.grade}">${item.grade}</span></td>
        <td class="stock"><strong>${item.name}</strong><span>${item.code}</span></td>
        <td>${item.sector}</td>
        <td>${sparkline(item.sparkline)}</td>
        <td>${item.price.toFixed(2)}</td>
        <td>${trendPct(item.change_pct)}</td>
        <td>${trendPct(item.rise_1m_pct)}</td>
        <td>${formatMoney(item.turnover_1m)}</td>
        <td class="${buyClass}">${Math.round(item.active_buy_ratio * 100)}%</td>
        <td><strong>${item.score}</strong></td>
        <td>
          <div class="actions">
            <button type="button" data-action="watch" data-code="${item.code}" class="${watched ? "active-watch" : ""}">${watched ? "已关注" : "关注"}</button>
            <button type="button" data-action="block" data-code="${item.code}" class="${blocked ? "active-block" : ""}">${blocked ? "已屏蔽" : "屏蔽"}</button>
          </div>
        </td>
      </tr>
      <tr class="signal-detail ${blocked ? "blocked" : ""}">
        <td></td>
        <td colspan="10" class="reason">${qualityTags(item.quality_tags)}<span class="explain-text">${explanation}</span> · 3m ${trendPct(item.rise_3m_pct)} · 量能 ${item.volume_spike.toFixed(2)}x · 买压 ${item.order_book_bias.toFixed(2)} · 更新 ${signalAge}</td>
      </tr>`;
    })
    .join("");
}

function connect() {
  const source = new EventSource("/events");
  source.onopen = () => {
    connection.textContent = "实时连接";
    pulse.classList.add("live");
  };
  source.onerror = () => {
    connection.textContent = "重连中";
    pulse.classList.remove("live");
  };
  source.addEventListener("snapshot", (event) => render(JSON.parse(event.data)));
  source.addEventListener("market", (event) => render(JSON.parse(event.data)));
}

async function showHistory() {
  historyList.innerHTML = `<div class="empty">加载历史信号</div>`;
  historyDialog.showModal();
  const response = await fetch("/api/signals/history?limit=100");
  const payload = await response.json();
  const signals = payload.signals || [];
  if (!signals.length) {
    historyList.innerHTML = `<div class="empty">还没有落盘的历史信号</div>`;
    return;
  }
  historyList.innerHTML = signals
    .map((item) => {
      const time = new Date(item.ts * 1000).toLocaleTimeString("zh-CN", { hour12: false });
      return `<div class="history-item">
        <span class="tag ${item.grade}">${item.grade}</span>
        <div>
          <strong>${item.name} ${item.code}</strong>
          <span>${time} · ${item.sector} · ${item.reasons.slice(0, 3).join(" / ")}</span>
        </div>
        <strong>${item.score}</strong>
      </div>`;
    })
    .join("");
}

async function showReport() {
  reportContent.innerHTML = `<div class="empty">生成复盘</div>`;
  reportDialog.showModal();
  const response = await fetch("/api/report");
  const payload = await response.json();
  renderReport(payload.report || {}, payload.runtime || {});
}

function renderReport(report, runtime) {
  const performance = report.performance || {};
  reportContent.innerHTML = [
    reportSection("概览", reportGrid([
      ["信号数", report.total_signals || 0],
      ["跟踪样本", performance.total || 0],
      ["正收益率", `${Number(performance.positive_rate || 0).toFixed(1)}%`],
    ])),
    reportSection("板块", reportPairs(report.sector_counts || {})),
    reportSection("标签", reportPairs(report.tag_counts || {})),
    reportSection("等级表现", reportGradePerformance(performance.by_grade || {})),
    reportSection("高分信号", reportSignals(report.top_signals || [])),
    reportSection("运行", reportGrid([
      ["时段", runtime.session?.label || "--"],
      ["数据源", runtime.source || "--"],
      ["坏行", runtime.bad_row_count || 0],
      ["上游", runtime.upstream_health?.source ? `${runtime.upstream_health.source} ${runtime.upstream_health.connected ? "已连接" : "未连接"}` : "--"],
    ])),
  ].join("");
}

function reportSection(title, body) {
  return `<section class="report-section"><h3>${title}</h3>${body}</section>`;
}

function reportGrid(items) {
  return `<div class="report-grid">${items.map(([label, value]) => `<div class="report-pill"><span>${label}</span><strong>${value}</strong></div>`).join("")}</div>`;
}

function reportPairs(pairs) {
  const entries = Object.entries(pairs);
  if (!entries.length) return `<div class="muted-line">暂无</div>`;
  return reportGrid(entries.map(([label, value]) => [label, value]));
}

function reportGradePerformance(byGrade) {
  const entries = Object.entries(byGrade);
  if (!entries.length) return `<div class="muted-line">暂无</div>`;
  return reportGrid(entries.map(([grade, item]) => [grade, `${item.count}条 / ${Number(item.positive_rate).toFixed(1)}% / 现${pct(item.avg_current_return_pct)}`]));
}

function reportSignals(signals) {
  if (!signals.length) return `<div class="muted-line">暂无</div>`;
  return signals
    .map((item) => `<div class="history-item"><span class="tag ${item.grade}">${item.grade}</span><div><strong>${item.name} ${item.score}</strong><span>${item.sector} · ${item.quality_tags.join(" / ") || item.reasons.slice(0, 2).join(" / ")}</span></div><strong>${trendPct(item.change_pct)}</strong></div>`)
    .join("");
}

if (clock) {
  setInterval(() => {
    clock.textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  }, 250);
}

historyButton?.addEventListener("click", showHistory);
reportButton?.addEventListener("click", showReport);
soundButton?.addEventListener("click", toggleSound);
closeHistory?.addEventListener("click", () => historyDialog.close());
closeReport?.addEventListener("click", () => reportDialog.close());
gradeFilter?.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-grade]");
  if (!button) return;
  activeGrade = button.dataset.grade;
  for (const item of gradeFilter.querySelectorAll("button")) {
    item.classList.toggle("active", item === button);
  }
  renderFilteredView();
});
candidateQualityFilter?.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-quality]");
  if (!button) return;
  activeCandidateQuality = button.dataset.quality;
  for (const item of candidateQualityFilter.querySelectorAll("button")) {
    item.classList.toggle("active", item === button);
  }
  renderFilteredView();
});
for (const control of [sectorFilter, scoreFilter, turnoverFilter].filter(Boolean)) {
  control.addEventListener("input", () => {
    if (control === sectorFilter) renderCandidateSectors(latestCandidateSectors);
    renderFilteredView();
  });
  control.addEventListener("change", () => {
    if (control === sectorFilter) renderCandidateSectors(latestCandidateSectors);
    renderFilteredView();
  });
}
sectorList?.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-sector]");
  if (!button) return;
  sectorFilter.value = sectorFilter.value === button.dataset.sector ? "ALL" : button.dataset.sector;
  renderCandidateSectors(latestCandidateSectors);
  renderFilteredView();
});
for (const control of [watchOnlyFilter, showBlockedFilter].filter(Boolean)) {
  control.addEventListener("change", () => renderFilteredView());
}
configForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const params = new URLSearchParams(new FormData(configForm));
  const response = await fetch(`/api/config/update?${params.toString()}`);
  const payload = await response.json();
  configForm.dataset.loaded = "0";
  renderConfig(payload.config || {});
});
resetConfigButton?.addEventListener("click", async () => {
  const response = await fetch("/api/config/reset");
  const payload = await response.json();
  configForm.dataset.loaded = "0";
  renderConfig(payload.config || {});
});
universeForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const params = new URLSearchParams(new FormData(universeForm));
  const response = await fetch(`/api/universe/add?${params.toString()}`);
  const payload = await response.json();
  universeCode.value = "";
  renderUniverse(payload.universe || {});
});
universeListView?.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-list][data-code]");
  if (!button) return;
  const params = new URLSearchParams({ list: button.dataset.list, code: button.dataset.code });
  const response = await fetch(`/api/universe/remove?${params.toString()}`);
  const payload = await response.json();
  renderUniverse(payload.universe || {});
});
sectorForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const params = new URLSearchParams(new FormData(sectorForm));
  const response = await fetch(`/api/sectors/add?${params.toString()}`);
  const payload = await response.json();
  sectorCodeInput.value = "";
  renderSectorConfig(payload.sectors || {});
});
sectorConfigList?.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-sector][data-code]");
  if (!button) return;
  const params = new URLSearchParams({ sector: button.dataset.sector, code: button.dataset.code });
  const response = await fetch(`/api/sectors/remove?${params.toString()}`);
  const payload = await response.json();
  renderSectorConfig(payload.sectors || {});
});
rows?.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action][data-code]");
  if (!button) return;
  toggleList(button.dataset.action, button.dataset.code);
});
updateSoundButton();
if (universeListView) loadUniverse();
if (sectorConfigList) loadSectorConfig();
loadCandidates();
loadNextDayFocus();
setInterval(loadCandidates, 5000);
setInterval(loadNextDayFocus, 30000);
connect();
