const OUTCOME_LABELS = { home: "主胜", draw: "平", away: "客胜" };
const OUTCOME_COLORS = {
  home: "#22c55e",
  draw: "#f59e0b",
  away: "#ef4444",
};
const REFRESH_STORAGE_KEY = "dashboardAutoRefreshSeconds";
const DATA_MODE_STORAGE_KEY = "dashboardDataMode";
const DATE_TAB_STORAGE_KEY = "dashboardDateTabOffset";

let overview = null;
let allPredictions = [];
let predictions = [];
let activeMatchId = null;
let selectedDateOffset = 0;
let charts = {};
let refreshTimer = null;
let countdownTimer = null;

function confidenceClass(level) {
  if (level === "高") return "high";
  if (level === "中") return "mid";
  return "low";
}

function pct(value) {
  return `${(value * 100).toFixed(1)}%`;
}

function renderMatchIntelligence(intel) {
  const list = document.getElementById("match-intelligence");
  const detail = document.getElementById("intelligence-detail");
  const coverageEl = document.getElementById("intelligence-coverage");
  const card = document.getElementById("intelligence-card");
  if (!list || !card) return;

  if (!intel || (!intel.summary_bullets?.length && !intel.detail_sections)) {
    list.innerHTML = `<li class="empty-note">暂无结构化赛前情报（需三源统一索引或 overlay 数据）。</li>`;
    if (detail) detail.innerHTML = "";
    if (coverageEl) coverageEl.textContent = "";
    return;
  }

  if (detail && intel.detail_sections) {
    const ds = intel.detail_sections;
    const teamCards = (ds.teams || [])
      .map((team) => {
        const gs = team.group_stats || {};
        const profile = team.profile || {};
        const tags = (profile.style_tags || []).slice(0, 3).join(" · ") || "—";
        const statsText = gs.points != null
          ? `${gs.points} 分 · ${gs.goals_for || 0}:${gs.goals_against || 0}`
          : `${profile.region || "—"} · ${profile.confederation || "—"}`;
        return `<div class="intel-team-card">
          <div class="intel-team-head">${team.name}</div>
          <div class="intel-team-meta">${statsText}</div>
          <div class="intel-team-tags">${tags}</div>
        </div>`;
      })
      .join("");

    const absenceBlock = (side, title) => {
      const lines = (ds.absences || {})[side] || [];
      return `<div class="intel-absence-card">
        <div class="label">${title}</div>
        ${lines.length
          ? `<ul>${lines.map((line) => `<li>${line}</li>`).join("")}</ul>`
          : `<p class="hint">暂无结构化伤停</p>`}
      </div>`;
    };

    detail.innerHTML = `
      <div class="intel-grid">
        ${teamCards}
      </div>
      <div class="intel-grid two">
        ${absenceBlock("home", intel.home + " 伤停")}
        ${absenceBlock("away", intel.away + " 伤停")}
      </div>
      ${ds.venue ? `<div class="intel-meta-row"><span>场地</span><strong>${ds.venue.label || ds.venue.stadium || "—"}</strong></div>` : ""}
      ${ds.environment?.temperature_c != null ? `<div class="intel-meta-row"><span>气温</span><strong>${ds.environment.temperature_c}°C</strong></div>` : ""}
      ${ds.referee ? `<div class="intel-meta-row"><span>裁判</span><strong>${ds.referee}</strong></div>` : ""}
      ${ds.style_note ? `<div class="intel-style-note">${ds.style_note}</div>` : ""}
      ${ds.rotation_risk ? `<div class="intel-style-note">${ds.rotation_risk}</div>` : ""}
    `;
  } else if (detail) {
    detail.innerHTML = "";
  }

  const summaryLines = (intel.summary_bullets || []).filter(
    (line) => !isJsonLikeText(line)
  );
  list.innerHTML = summaryLines.map((line) => `<li>${line}</li>`).join("");

  const extras = [];
  if (intel.home_predicted_lineup?.length) {
    extras.push(`<li><strong>${intel.home} 预测首发：</strong>${intel.home_predicted_lineup.join("、")}</li>`);
  }
  if (intel.away_predicted_lineup?.length) {
    extras.push(`<li><strong>${intel.away} 预测首发：</strong>${intel.away_predicted_lineup.join("、")}</li>`);
  }
  if (intel.home_tactics) {
    extras.push(`<li><strong>${intel.home} 战术：</strong>${intel.home_tactics}</li>`);
  }
  if (intel.away_tactics) {
    extras.push(`<li><strong>${intel.away} 战术：</strong>${intel.away_tactics}</li>`);
  }
  if (extras.length) {
    list.innerHTML += extras.join("");
  }

  const cov = intel.coverage || {};
  const tags = [
    cov.home_away_splits ? "主客场拆分" : null,
    cov.style_profiles ? "风格对阵" : null,
    cov.venue_available ? "场地" : null,
    cov.referee_available ? "裁判" : null,
    cov.overlay_used ? "人工 overlay" : null,
    cov.injury_api ? "API 伤停" : "伤停需 overlay",
  ].filter(Boolean);
  if (coverageEl) {
    const src = (intel.data_sources || []).join(" · ");
    coverageEl.textContent = `${intel.disclaimer || ""} 来源：${src || "—"} · 覆盖：${tags.join(" · ")}`;
  }
}

function renderDataSources(payload) {
  const list = document.getElementById("data-sources");
  if (!list) return;
  const ds = payload.data_sources || {};
  const lines = [
    `体彩主盘：${ds.sporttery ? "✓" : "✗"}`,
    `FIFA 三源索引：${ds.unified ? "✓ 已匹配" : "✗ 本场未入索引"}`,
    `外网辅盘：${ds.foreign || "暂不可用（FOX/Polymarket/OddsAPI 均未命中）"}`,
    `总进球 ttg：${ds.pool_ttg ? "✓" : "✗"}`,
    `半全场 hafu：${ds.pool_hafu ? "✓" : "✗"}`,
    `情报源：${(ds.intelligence || []).join(" · ") || "基础模板"}`,
  ];
  list.innerHTML = lines.map((line) => `<li>${line}</li>`).join("");
}

function renderPoolAnalysis(pool) {
  const list = document.getElementById("pool-analysis");
  const kellyEl = document.getElementById("kelly-had");
  const card = document.getElementById("pool-analysis-card");
  if (!list || !card) return;

  if (!pool || !pool.summary_bullets?.length) {
    list.innerHTML = `<li class="empty-note">暂无 ttg/hafu 数据（需 getFixedBonus 历史）。</li>`;
    if (kellyEl) kellyEl.innerHTML = "";
    return;
  }

  list.innerHTML = pool.summary_bullets.map((line) => `<li>${line}</li>`).join("");

  if (kellyEl && pool.kelly_had?.length) {
    kellyEl.innerHTML = pool.kelly_had
      .map((row) => {
        const valueTag = row.is_value ? " · 价值" : "";
        return `<div class="stat-card compact ${row.is_value ? "delta-alert" : ""}">
          <div class="label">${row.label}</div>
          <div class="value" style="font-size:16px">凯利 ${row.kelly_index.toFixed(3)}</div>
          <div class="hint">SP ${row.odds.toFixed(2)} · 外网 ${pct(row.reference_prob)} · 偏差 ${row.value_edge_pp >= 0 ? "+" : ""}${row.value_edge_pp.toFixed(1)}pp${valueTag}</div>
        </div>`;
      })
      .join("");
  } else if (kellyEl) {
    kellyEl.innerHTML = "";
  }
}

function getMatchIdFromUrl() {
  const matched = window.location.pathname.match(/^\/match\/([^/]+)/);
  return matched ? decodeURIComponent(matched[1]) : null;
}

function setMatchUrl(matchId) {
  const nextPath = `/match/${encodeURIComponent(matchId)}`;
  if (window.location.pathname !== nextPath) {
    window.history.pushState({ matchId }, "", nextPath);
  }
}

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error || `请求失败：${response.status}`);
  }
  return response.json();
}

function renderMeta() {
  const sporttery = overview.sporttery || {};
  const predictionsMeta = overview.predictions || {};
  const health = overview.provider_health || {};
  const unified = overview.unified_index || {};
  const stats = overview.dashboard_stats || {};
  const modeNote = overview.unified_mode_note;

  renderHeroDashboard(stats);

  if (health.providers && health.providers.length) {
    const labels = health.providers.map((item) => {
      const short = item.name.replace("-public", "").replace("sporttery", "体彩");
      return `${short}:${item.ok ? "✓" : "✗"}`;
    });
    const cacheHint = health.cached ? "（缓存）" : "";
    document.getElementById("provider-health").textContent =
      `${health.all_ok ? "全部正常" : "部分异常"}${cacheHint} · ${labels.join(" · ")}`;
  } else {
    document.getElementById("provider-health").textContent =
      health.error || "三源检测暂不可用 · 可访问 /api/doctor 排查";
  }

  const modeWrap = document.getElementById("mode-note-wrap");
  const modeNoteEl = document.getElementById("mode-note");
  if (modeNote && modeWrap && modeNoteEl) {
    modeWrap.style.display = "block";
    modeNoteEl.textContent = modeNote;
  } else if (modeWrap) {
    modeWrap.style.display = "none";
  }

  if (!sporttery.success) {
    document.getElementById("sporttery-status").textContent = sporttery.error || "体彩 API 暂不可用";
    return;
  }
  const cacheHint = sporttery.cached || predictionsMeta.cached
    ? `（缓存 ${sporttery.cached_at || predictionsMeta.cached_at || ""}）`
    : "（实时）";
  const unifiedHint = unified.success
    ? ` · 三源索引 ${unified.match_count} 场`
    : unified.error
      ? ` · 三源索引失败`
      : "";
  const modeHint = overview.mode === "unified" ? " · 三源增强" : " · 体彩全量";
  const visible = filterPredictions().length;
  document.getElementById("sporttery-status").textContent =
    `当前 ${visible} 场 · 全部 ${stats.total_upcoming || 0} 场 ${cacheHint}${unifiedHint}${modeHint}`;
}

function renderHeroDashboard(stats) {
  const panel = document.getElementById("hero-dashboard");
  if (!panel || !stats.date_tabs) return;

  const tabs = stats.date_tabs;
  const buckets = stats.date_buckets || {};
  const todayTab = tabs[selectedDateOffset] || tabs[0];
  const bucketKey = todayTab?.date ?? "";
  const bucket = buckets[bucketKey] || buckets[""] || { total: 0, unified: 0, high_confidence: 0 };

  panel.innerHTML = `
    <div class="hero-clock">
      <span class="hero-date">${todayTab?.display || stats.beijing_date}</span>
      <span class="hero-timezone">北京时间 · 按体彩销售日分组 · 共 ${stats.total_upcoming || 0} 场</span>
    </div>
    <div class="hero-kpi-grid">
      <div class="hero-kpi">
        <span class="kpi-value">${bucket.total || 0}</span>
        <span class="kpi-label">当日赛事</span>
      </div>
      <div class="hero-kpi accent">
        <span class="kpi-value">${bucket.unified || 0}</span>
        <span class="kpi-label">三源交叉</span>
      </div>
      <div class="hero-kpi">
        <span class="kpi-value">${bucket.high_confidence || 0}</span>
        <span class="kpi-label">高信心</span>
      </div>
      <div class="hero-kpi">
        <span class="kpi-value">${stats.overlay_matches || 0}</span>
        <span class="kpi-label">overlay 情报</span>
      </div>
    </div>
    <div class="hero-capabilities">
      <span>融合预测</span><span>Polymarket 辅盘</span><span>ttg / hafu</span><span>凯利偏差</span><span>伤停 overlay</span>
    </div>
  `;
}

function getSelectedDate() {
  const tabs = overview?.dashboard_stats?.date_tabs || [];
  return tabs[selectedDateOffset]?.date || tabs[0]?.date || "";
}

function filterPredictions() {
  const selectedDate = getSelectedDate();
  return allPredictions.filter((item) => !selectedDate || item.match_date === selectedDate);
}

function renderDateTabs() {
  const container = document.getElementById("date-tabs");
  const note = document.getElementById("date-filter-note");
  if (!container) return;

  const tabs = overview?.dashboard_stats?.date_tabs || [];
  const buckets = overview?.dashboard_stats?.date_buckets || {};
  container.innerHTML = tabs
    .map((tab, index) => {
      const bucket = buckets[tab.date] || { total: 0, unified: 0 };
      const active = index === selectedDateOffset ? "active" : "";
      const dateLabel = tab.date ? tab.date.slice(5) : "全部";
      return `<button type="button" class="date-tab ${active}" data-offset="${index}" role="tab">
        <span class="date-tab-label">${tab.label}</span>
        <span class="date-tab-sub">${dateLabel} · ${bucket.total}场${bucket.unified ? ` · 三源${bucket.unified}` : ""}</span>
      </button>`;
    })
    .join("");

  container.querySelectorAll(".date-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      selectedDateOffset = Number(btn.dataset.offset);
      localStorage.setItem(DATE_TAB_STORAGE_KEY, String(selectedDateOffset));
      renderMeta();
      renderSportteryCards({ preserveMatch: true });
    });
  });

  if (note) {
    const selectedDate = getSelectedDate();
    const visible = filterPredictions().length;
    const bucket = (overview?.dashboard_stats?.date_buckets || {})[selectedDate] || {};
    const allBucket = (overview?.dashboard_stats?.date_buckets || {})[""] || {};
    if (!selectedDate) {
      note.textContent = `全部 ${visible} 场（体彩官网已公布 ${allBucket.total || visible} 场，其中 ${allBucket.unified || 0} 场三源增强）`;
    } else {
      note.textContent = `已选 ${selectedDate}（体彩销售日）：${visible} 场（其中 ${bucket.unified || 0} 场三源增强）`;
    }
  }
}

function isJsonLikeText(text) {
  const value = String(text || "").trim();
  return value.startsWith("{") || value.startsWith("[") || value.includes('"Stadium"');
}

function clearMatchDetail() {
  activeMatchId = null;
  document.getElementById("prediction-direction").textContent = "—";
  document.getElementById("prediction-confidence").textContent = "比分 —";
  document.getElementById("prediction-confidence").className = "badge-confidence tag low";
  document.getElementById("sporttery-stats").innerHTML = `<p class="empty-note">请选择左侧赛事查看详情</p>`;
  document.getElementById("bet-simulation").innerHTML = "";
  document.getElementById("fusion-analysis").innerHTML = `<li class="empty-note">暂无选中赛事</li>`;
  document.getElementById("foreign-compare").innerHTML = `<li class="empty-note">暂无选中赛事</li>`;
  renderMatchIntelligence(null);
  renderPoolAnalysis(null);
  renderDataSources({});
  Object.values(charts).forEach((chart) => chart?.destroy?.());
  charts = {};
}

function pickDefaultDateTab() {
  const tabs = overview?.dashboard_stats?.date_tabs || [];
  const buckets = overview?.dashboard_stats?.date_buckets || {};
  let fallback = 0;
  for (let i = 0; i < tabs.length; i += 1) {
    const tab = tabs[i];
    if (!tab.date) {
      fallback = i;
      continue;
    }
    const bucket = buckets[tab.date] || {};
    if (bucket.total > 0) {
      if (tab.label === "今天") return i;
      if (fallback === 0) fallback = i;
    }
  }
  return fallback;
}

function sanitizeCardText(text) {
  if (!text || isJsonLikeText(text)) return "";
  return text;
}

function renderDataTags(tags) {
  if (!tags || !tags.length) return "";
  return tags.map((tag) => `<span class="tag mini">${tag}</span>`).join("");
}

function renderPendingMatchDetail(match) {
  document.getElementById("prediction-direction").textContent = "待开售";
  document.getElementById("prediction-confidence").textContent = "比分 —";
  document.getElementById("prediction-confidence").className = "badge-confidence tag low";
  document.getElementById("sporttery-stats").innerHTML = `
    <div class="stat-card">
      <div class="label">赛事状态</div>
      <div class="value">待开售</div>
      <div class="hint">体彩官网已公布赛程，固定奖金暂未开放</div>
    </div>
    <div class="stat-card">
      <div class="label">开赛时间</div>
      <div class="value" style="font-size:18px">${match.countdown_label || "待定"}</div>
      <div class="hint">${match.kickoff_beijing || ""}</div>
    </div>
    <div class="stat-card">
      <div class="label">销售日</div>
      <div class="value" style="font-size:18px">${match.match_date || "—"}</div>
      <div class="hint">${match.match_num || "待编号"}</div>
    </div>
    <div class="stat-card">
      <div class="label">后续补齐</div>
      <div class="value" style="font-size:18px">自动刷新</div>
      <div class="hint">开售后展示预测、SP走势、ttg/hafu 与凯利偏差</div>
    </div>
  `;
  document.getElementById("bet-simulation").innerHTML = "";
  document.getElementById("fusion-analysis").innerHTML =
    `<li>体彩官网已公布 ${match.home} vs ${match.away}，当前为待开售状态。</li><li>固定奖金开放后，本平台会自动补齐完整分析。</li>`;
  document.getElementById("foreign-compare").innerHTML =
    `<li class="empty-note">待开售场次暂无体彩主盘，暂不计算外网差值。</li>`;
  renderMatchIntelligence(null);
  renderPoolAnalysis(null);
  renderDataSources({ data_sources: { sporttery: true, unified: false, foreign: "待开售" } });
  Object.values(charts).forEach((chart) => chart?.destroy?.());
  charts = {};
}

function renderSportteryCards(options = {}) {
  const { preserveMatch = false } = options;
  const grid = document.getElementById("sporttery-grid");
  allPredictions = (overview.predictions && overview.predictions.predictions) || [];
  predictions = filterPredictions();
  renderDateTabs();

  if (!predictions.length) {
    const hint = overview.unified_mode_note || "当前日期没有未开赛赛事，请切换日期或稍后刷新。";
    grid.innerHTML = `<p class="empty-note">${hint}</p>`;
    clearMatchDetail();
    return;
  }

  grid.innerHTML = predictions
    .map((item) => `
      <article class="match-card sporttery-card ${item.unified_linked ? "unified-linked" : ""} ${item.analysis_available === false ? "pending-sale" : ""}" data-match-id="${item.match_id}">
        <div class="card-top">
          <h3>${item.home} vs ${item.away}</h3>
          <span class="countdown-badge">${item.countdown_label || "待定"}</span>
        </div>
        <div class="stage">${sanitizeCardText(item.region_label) || `${item.league || "竞彩"} · ${(item.kickoff_beijing || "待定").slice(0, 16)}`}</div>
        <div class="tag-row">${renderDataTags(item.data_tags)}</div>
        <div class="odds-line">
          <span>${item.match_num || "—"}</span>
          <span>${item.analysis_available === false ? "状态" : "方向"} ${item.direction}</span>
          <span>比分 ${item.predicted_score}</span>
          <span class="tag ${confidenceClass(item.confidence)}">${item.confidence}</span>
        </div>
        <div class="odds-line">
          <span>体彩 ${item.sporttery_had}</span>
        </div>
      </article>
    `)
    .join("");

  grid.querySelectorAll(".sporttery-card").forEach((card) => {
    card.addEventListener("click", () => loadSportteryDetail(card.dataset.matchId));
  });

  const urlMatchId = getMatchIdFromUrl();
  const stillExists = preserveMatch && activeMatchId && predictions.some((item) => item.match_id === activeMatchId);
  const initialId = stillExists
    ? activeMatchId
    : urlMatchId && predictions.some((item) => item.match_id === urlMatchId)
      ? urlMatchId
      : predictions[0].match_id;
  loadSportteryDetail(initialId);
}

function stakeValues() {
  const stakeHad = Number(document.getElementById("stake-had").value) || 100;
  const stakeCrs = Number(document.getElementById("stake-crs").value) || 50;
  return { stakeHad, stakeCrs };
}

async function renderBetSimulation(matchId) {
  const panel = document.getElementById("bet-simulation");
  const { stakeHad, stakeCrs } = stakeValues();
  try {
    const sim = await fetchJson(
      `/api/bet/simulate?match_id=${encodeURIComponent(matchId)}&stake_had=${stakeHad}&stake_crs=${stakeCrs}`
    );
    const had = sim.had;
    const crs = sim.crs;
    panel.innerHTML = `
      <div class="stat-card">
        <div class="label">假设总投入</div>
        <div class="value">${sim.total_stake} 元</div>
        <div class="hint">胜平负 ${stakeHad} + 猜比分 ${stakeCrs}</div>
      </div>
      <div class="stat-card">
        <div class="label">胜平负（${had ? had.pick : "—"}）</div>
        <div class="value">${had ? had.profit_if_win : "—"} 元</div>
        <div class="hint">${had ? `中奖返还 ${had.return_if_win} 元 · 赔率 ${had.odds}` : "无数据"}</div>
      </div>
      <div class="stat-card">
        <div class="label">猜比分（${crs ? crs.pick : "—"}）</div>
        <div class="value">${crs ? crs.profit_if_win : "—"} 元</div>
        <div class="hint">${crs ? `中奖返还 ${crs.return_if_win} 元 · 赔率 ${crs.odds}` : "无数据"}</div>
      </div>
      <div class="stat-card">
        <div class="label">最佳 / 最差</div>
        <div class="value">${sim.best_case_profit} / ${sim.worst_case_loss} 元</div>
        <div class="hint">${sim.disclaimer}</div>
      </div>
    `;
  } catch (error) {
    panel.innerHTML = `<p class="empty-note">模拟投注加载失败：${error.message}</p>`;
  }
}

async function loadSportteryDetail(matchId) {
  activeMatchId = matchId;
  setMatchUrl(matchId);
  const cached = predictions.find((item) => item.match_id === matchId);

  document.querySelectorAll(".sporttery-card").forEach((card) => {
    card.classList.toggle("active", card.dataset.matchId === matchId);
  });

  if (cached) {
    document.getElementById("prediction-direction").textContent = cached.direction;
    document.getElementById("prediction-confidence").textContent =
      `比分 ${cached.predicted_score} · 信心 ${cached.confidence}`;
    document.getElementById("prediction-confidence").className =
      `badge-confidence tag ${confidenceClass(cached.confidence)}`;
  }

  if (cached?.analysis_available === false) {
    renderPendingMatchDetail(cached);
    return;
  }

  const payload = await fetchJson(`/api/sporttery/predict/${encodeURIComponent(matchId)}?foreign=auto`);
  const prediction = payload.prediction;
  const score = payload.score_prediction || cached;

  document.getElementById("sporttery-stats").innerHTML = `
    <div class="stat-card">
      <div class="label">预测方向</div>
      <div class="value">${score.direction}</div>
      <div class="hint">次选 ${score.second}</div>
    </div>
    <div class="stat-card">
      <div class="label">预测比分</div>
      <div class="value">${score.predicted_score}</div>
      <div class="hint">${(score.alt_scores || []).slice(0, 2).join("；") || "无备选"}</div>
    </div>
    <div class="stat-card">
      <div class="label">距开赛</div>
      <div class="value" style="font-size:18px">${cached?.countdown_label || "待定"}</div>
      <div class="hint">${cached?.kickoff_beijing || ""}</div>
    </div>
    <div class="stat-card">
      <div class="label">体彩返还率</div>
      <div class="value">${pct(prediction.return_rate)}</div>
      <div class="hint">${score.sporttery_had}</div>
    </div>
    <div class="stat-card">
      <div class="label">外网辅盘</div>
      <div class="value" style="font-size:16px">${payload.foreign_source_resolved || score.fox_source || "暂无"}</div>
      <div class="hint">${score.fox_moneyline || "优先 Polymarket，失败回退 FOX"}</div>
    </div>
  `;

  const context = payload.context_analysis;
  if (context && context.context_available) {
    document.getElementById("fusion-analysis").innerHTML =
      [`综合信号：${OUTCOME_LABELS[context.context_pick]}（信心 ${context.context_confidence}，边际 ${context.context_edge >= 0 ? "+" : ""}${context.context_edge.toFixed(3)}）`,
        ...(prediction.analysis || []),
        score.direction_note,
      ].filter(Boolean).map((line) => `<li>${line}</li>`).join("");
  } else {
    document.getElementById("fusion-analysis").innerHTML =
      [...(prediction.analysis || []), score.direction_note].filter(Boolean).map((line) => `<li>${line}</li>`).join("");
  }

  renderMatchIntelligence(payload.match_intelligence);
  renderPoolAnalysis(payload.pool_analysis);
  renderDataSources(payload);

  const compare = document.getElementById("foreign-compare");
  const alertKeys = new Set(payload.probability_delta_alerts || []);
  const threshold = payload.probability_delta_threshold_pp || 5;
  if (prediction.foreign.probabilities) {
    const fp = prediction.foreign.probabilities;
    const sp = prediction.probabilities;
    compare.innerHTML = ["home", "draw", "away"]
      .map((key) => {
        const delta = (fp[key] - sp[key]) * 100;
        const sign = delta >= 0 ? "+" : "";
        const alertClass = alertKeys.has(key) ? "delta-alert" : "";
        const alertTag = alertKeys.has(key) ? ` ⚠ ≥${threshold}pp` : "";
        return `<li class="${alertClass}">${OUTCOME_LABELS[key]}：体彩 ${pct(sp[key])}｜外网 ${pct(fp[key])}｜差值 ${sign}${delta.toFixed(1)}pp${alertTag}</li>`;
      })
      .join("");
  } else {
    compare.innerHTML = `<li class="empty-note">外网数据暂不可用，仅展示体彩主盘。</li>`;
  }

  renderSportteryCharts(payload);
  await renderBetSimulation(matchId);
}

function shortTimeLabel(value) {
  return value.replace("T", " ").replace("+08:00", "").replace("+00:00", "").slice(5, 16);
}

function renderSportteryCharts(payload) {
  const prediction = payload.prediction;
  if (charts.sportteryProb) charts.sportteryProb.destroy();
  charts.sportteryProb = new Chart(document.getElementById("sporttery-prob-chart"), {
    type: "doughnut",
    data: {
      labels: ["主胜", "平", "客胜"],
      datasets: [{
        data: [
          prediction.probabilities.home * 100,
          prediction.probabilities.draw * 100,
          prediction.probabilities.away * 100,
        ],
        backgroundColor: [OUTCOME_COLORS.home, OUTCOME_COLORS.draw, OUTCOME_COLORS.away],
        borderWidth: 0,
      }],
    },
    options: { plugins: { legend: { position: "bottom", labels: { color: "#dbe4f0" } } } },
  });

  const series = payload.series;
  const labels = series.had.times.map(shortTimeLabel);

  if (charts.sportteryHad) charts.sportteryHad.destroy();
  charts.sportteryHad = new Chart(document.getElementById("sporttery-had-chart"), {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "主胜", data: series.had.home, borderColor: OUTCOME_COLORS.home, tension: 0.25 },
        { label: "平", data: series.had.draw, borderColor: OUTCOME_COLORS.draw, tension: 0.25 },
        { label: "客胜", data: series.had.away, borderColor: OUTCOME_COLORS.away, tension: 0.25 },
      ],
    },
    options: chartOptions(),
  });

  const hhadCard = document.getElementById("sporttery-hhad-card");
  const hasHhad = series.hhad.home.some((value) => value !== null);
  hhadCard.style.display = hasHhad ? "block" : "none";
  if (!hasHhad) return;

  if (charts.sportteryHhad) charts.sportteryHhad.destroy();
  charts.sportteryHhad = new Chart(document.getElementById("sporttery-hhad-chart"), {
    type: "line",
    data: {
      labels: series.hhad.times.map(shortTimeLabel),
      datasets: [
        { label: "让球主胜", data: series.hhad.home, borderColor: OUTCOME_COLORS.home, tension: 0.25 },
        { label: "让球平", data: series.hhad.draw, borderColor: OUTCOME_COLORS.draw, tension: 0.25 },
        { label: "让球客胜", data: series.hhad.away, borderColor: OUTCOME_COLORS.away, tension: 0.25 },
      ],
    },
    options: chartOptions(),
  });
}

function chartOptions() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: "index", intersect: false },
    plugins: { legend: { labels: { color: "#dbe4f0" } } },
    scales: {
      x: { ticks: { color: "#94a3b8" }, grid: { color: "rgba(255,255,255,0.05)" } },
      y: { ticks: { color: "#94a3b8" }, grid: { color: "rgba(255,255,255,0.05)" } },
    },
  };
}

function renderSettlementSummary() {
  const summary = overview.settlement_summary || {};
  document.getElementById("settlement-summary").innerHTML = `
    <div class="stat-card">
      <div class="label">待结算</div>
      <div class="value">${summary.open_count || 0} 场</div>
    </div>
    <div class="stat-card">
      <div class="label">已结算</div>
      <div class="value">${summary.settled_count || 0} 场</div>
    </div>
    <div class="stat-card">
      <div class="label">累计盈亏</div>
      <div class="value ${(summary.total_pnl || 0) >= 0 ? "positive" : "negative"}">${summary.total_pnl || 0} 元</div>
    </div>
  `;
}

function renderSettlementResults(payload) {
  const container = document.getElementById("settlement-results");
  const rows = payload.results || [];
  if (!rows.length) {
    container.innerHTML = `<p class="empty-note">${payload.message || "暂无结算记录"}</p>`;
    return;
  }
  container.innerHTML = `
    <div class="settlement-table-wrap">
      <table class="settlement-table">
        <thead>
          <tr>
            <th>场次</th>
            <th>状态</th>
            <th>预测</th>
            <th>实际（体彩）</th>
            <th>FIFA 比分</th>
            <th>盈亏</th>
          </tr>
        </thead>
        <tbody>
          ${rows.map((row) => `
            <tr>
              <td>${row.home || ""} vs ${row.away || row.match_id || ""}</td>
              <td>${row.status}</td>
              <td>${row.predicted_had || "—"} / ${row.predicted_score || "—"}</td>
              <td>${row.actual_had || "—"} / ${row.actual_score || "—"}</td>
              <td>${row.fifa_actual ? `${row.fifa_actual.score_label}（${row.fifa_actual.outcome_label}）` : "—"}</td>
              <td class="${(row.total_pnl || 0) >= 0 ? "positive" : "negative"}">${row.total_pnl ?? "—"}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

async function refreshOverview(options = {}) {
  const { preserveMatch = true, resetDateTab = false } = options;
  const mode = document.getElementById("data-mode-select").value;
  overview = await fetchJson(`/api/overview?mode=${encodeURIComponent(mode)}`);
  if (resetDateTab || !preserveMatch) {
    selectedDateOffset = pickDefaultDateTab();
    localStorage.setItem(DATE_TAB_STORAGE_KEY, String(selectedDateOffset));
  }
  renderMeta();
  renderSettlementSummary();
  renderSportteryCards({ preserveMatch });
  if (preserveMatch && activeMatchId) {
    const stillExists = predictions.some((item) => item.match_id === activeMatchId);
    if (stillExists) {
      await loadSportteryDetail(activeMatchId);
    }
  }
  document.getElementById("refresh-hint").textContent =
    `上次刷新 ${new Date().toLocaleTimeString("zh-CN")}`;
}

function setupAutoRefresh(seconds) {
  if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
  localStorage.setItem(REFRESH_STORAGE_KEY, String(seconds));
  if (seconds > 0) {
    refreshTimer = setInterval(() => {
      refreshOverview({ preserveMatch: true }).catch(console.error);
    }, seconds * 1000);
  }
}

function setupCountdownTicker() {
  if (countdownTimer) clearInterval(countdownTimer);
  countdownTimer = setInterval(() => {
    document.querySelectorAll(".sporttery-card").forEach((card) => {
      const item = predictions.find((entry) => entry.match_id === card.dataset.matchId);
      if (!item || item.hours_until_kickoff == null) return;
      const nextHours = Math.max(0, item.hours_until_kickoff - 1 / 3600);
      item.hours_until_kickoff = nextHours;
      const badge = card.querySelector(".countdown-badge");
      if (badge) {
        badge.textContent = nextHours <= 0
          ? "已开赛"
          : nextHours < 1
            ? `距开赛 ${Math.max(1, Math.round(nextHours * 60))} 分钟`
            : nextHours < 24
              ? `距开赛 ${nextHours.toFixed(1)} 小时`
              : `距开赛 ${(nextHours / 24).toFixed(1)} 天`;
      }
    });
  }, 60000);
}

async function init() {
  const select = document.getElementById("auto-refresh-select");
  const modeSelect = document.getElementById("data-mode-select");
  const saved = localStorage.getItem(REFRESH_STORAGE_KEY);
  const savedMode = localStorage.getItem(DATA_MODE_STORAGE_KEY);
  const savedDateTab = localStorage.getItem(DATE_TAB_STORAGE_KEY);
  if (saved !== null) select.value = saved;
  if (savedMode) modeSelect.value = savedMode;
  if (savedDateTab !== null) selectedDateOffset = Number(savedDateTab) || 0;
  setupAutoRefresh(Number(select.value));

  select.addEventListener("change", () => setupAutoRefresh(Number(select.value)));
  modeSelect.addEventListener("change", () => {
    localStorage.setItem(DATA_MODE_STORAGE_KEY, modeSelect.value);
    selectedDateOffset = 0;
    refreshOverview({ preserveMatch: false, resetDateTab: true });
  });

  document.getElementById("refresh-btn").addEventListener("click", () => {
    refreshOverview({ preserveMatch: true });
  });

  document.getElementById("share-match-btn").addEventListener("click", async () => {
    if (!activeMatchId) return;
    const url = `${window.location.origin}/match/${encodeURIComponent(activeMatchId)}`;
    try {
      await navigator.clipboard.writeText(url);
      document.getElementById("share-match-btn").textContent = "已复制";
      setTimeout(() => {
        document.getElementById("share-match-btn").textContent = "分享链接";
      }, 1500);
    } catch {
      window.prompt("复制单场链接", url);
    }
  });

  ["stake-had", "stake-crs"].forEach((id) => {
    document.getElementById(id).addEventListener("change", () => {
      if (activeMatchId) renderBetSimulation(activeMatchId);
    });
  });

  document.getElementById("settle-btn").addEventListener("click", async () => {
    const payload = await fetchJson("/api/settlement/run");
    renderSettlementResults(payload);
    overview = await fetchJson("/api/overview");
    renderSettlementSummary();
  });

  window.addEventListener("popstate", (event) => {
    const matchId = event.state?.matchId || getMatchIdFromUrl();
    if (matchId) loadSportteryDetail(matchId).catch(console.error);
  });

  await refreshOverview({ preserveMatch: false, resetDateTab: true });
  setupCountdownTicker();
}

init().catch((error) => {
  document.body.innerHTML = `<pre style="color:#fff;padding:24px">加载失败：${error.message}</pre>`;
});
