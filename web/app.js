const OUTCOME_LABELS = { home: "主胜", draw: "平", away: "客胜" };
const OUTCOME_COLORS = {
  home: "#22c55e",
  draw: "#f59e0b",
  away: "#ef4444",
};
const REFRESH_STORAGE_KEY = "dashboardAutoRefreshSeconds";
const DATA_MODE_STORAGE_KEY = "dashboardDataMode";
const DATE_TAB_STORAGE_KEY = "dashboardDateTabDate";
const AI_FLOAT_MINIMIZED_KEY = "dashboardAiFloatMinimized";

let overview = null;
let allPredictions = [];
let predictions = [];
let activeMatchId = null;
let selectedDate = "";
let dateTabsExpanded = false;
let charts = {};
let refreshTimer = null;
let countdownTimer = null;
let aiAnalyzeStatus = null;
let aiChatByMatch = {};
let aiReviewCache = {};
let aiAutoReviewPending = new Set();
let aiSending = false;

function confidenceClass(level) {
  if (level === "高") return "high";
  if (level === "中") return "mid";
  return "low";
}

function renderShiftBadge(shift) {
  if (!shift?.available || shift.severity === "none") return "";
  if (shift.direction_flipped) {
    return `<span class="shift-badge shift-high">⚠ 方向转向</span>`;
  }
  if (shift.severity === "high" || shift.severity === "medium") {
    return `<span class="shift-badge shift-${shift.severity}">⚠ 冷门信号</span>`;
  }
  if (shift.severity === "low") {
    return `<span class="shift-badge shift-low">SP 变动</span>`;
  }
  return "";
}

function renderShiftPrediction(shiftPrediction) {
  if (!shiftPrediction?.active) return "";
  const initial = shiftPrediction.initial || {};
  const adjusted = shiftPrediction.adjusted || {};
  return `
    <div class="shift-prediction-box">
      <div class="shift-prediction-head">变盘策略对照 <span class="hint-inline">告警为主 · 临场参考</span></div>
      <div class="shift-prediction-grid">
        <div class="shift-prediction-col">
          <div class="label">${initial.label || "初盘/首次"}</div>
          <div class="value">${initial.direction || "—"}</div>
          <div class="hint">比分 ${initial.predicted_score || "—"}</div>
        </div>
        <div class="shift-prediction-arrow">→</div>
        <div class="shift-prediction-col adjusted">
          <div class="label">${adjusted.label || "变盘后"}</div>
          <div class="value">${adjusted.direction || "—"}</div>
          <div class="hint">比分 ${adjusted.predicted_score || "—"}</div>
        </div>
      </div>
      ${shiftPrediction.note ? `<p class="hint shift-prediction-note">${shiftPrediction.note}</p>` : ""}
    </div>
  `;
}

function renderDirectionShift(shift, shiftPrediction) {
  const list = document.getElementById("direction-shift-analysis");
  const card = document.getElementById("direction-shift-card");
  if (!list || !card) return;

  if (!shift?.available) {
    list.innerHTML = `<li class="empty-note">SP 历史不足 2 个节点，暂无法判断方向转向。</li>`;
    return;
  }

  const severityClass =
    shift.severity === "high" ? "delta-alert" : shift.severity === "medium" ? "shift-medium-text" : "";
  const headline = shift.direction_flipped
    ? `初盘 ${shift.opening_label} → 现盘 ${shift.current_label}`
    : `当前 SP 首选：${shift.current_label}`;

  const meta = [
    `<li><strong>${headline}</strong></li>`,
    `<li>历史节点 ${shift.history_points} 个 · 严重度 ${shift.severity}</li>`,
  ];
  if (shift.upset_candidates?.length) {
    meta.push(`<li>冷门受热：${shift.upset_candidates.join("、")}</li>`);
  }
  const bullets = (shift.alerts?.length ? shift.alerts : shift.summary_bullets || [])
    .map((line) => `<li class="${severityClass}">${line}</li>`)
    .join("");
  list.innerHTML = meta.join("") + bullets + (shiftPrediction?.active
    ? `<li class="shift-prediction-wrap">${renderShiftPrediction(shiftPrediction)}</li>`
    : "");
}

function pct(value) {
  return `${(value * 100).toFixed(1)}%`;
}

function renderProbabilityBars(probabilities) {
  if (!probabilities) return "";
  return ["home", "draw", "away"].map((key) => {
    const value = Number(probabilities[key] || 0);
    return `<div class="prob-row">
      <span>${OUTCOME_LABELS[key]}</span>
      <div class="prob-track"><i style="width:${Math.max(2, value).toFixed(1)}%"></i></div>
      <strong>${value.toFixed(1)}%</strong>
    </div>`;
  }).join("");
}

function renderSportteryFallbackCompare(payload) {
  const prediction = payload.prediction || {};
  const score = payload.score_prediction || {};
  const pool = payload.pool_analysis || {};
  const probs = prediction.probabilities || {};
  const ranking = ["home", "draw", "away"].sort((a, b) => (probs[b] || 0) - (probs[a] || 0));
  const pick = ranking[0];
  const second = ranking[1];
  const gap = ((probs[pick] || 0) - (probs[second] || 0)) * 100;
  const hhad = prediction.hhad || {};
  const hhadDirection = hhad.direction || score.hhad_direction;
  const hhadLine = hhad.goal_line != null ? `让球 ${Number(hhad.goal_line) > 0 ? "+" : ""}${hhad.goal_line}` : "让球盘";
  const ttgFavorite = pool.ttg?.favorite;
  const hafuFavorite = pool.hafu?.favorite;
  const signals = [];

  signals.push(`体彩主盘去水首选 ${OUTCOME_LABELS[pick] || score.direction || "—"}，领先次选 ${gap.toFixed(1)}pp。`);
  if (hhadDirection) {
    signals.push(
      hhadDirection === prediction.direction
        ? `${hhadLine} 与主盘同向，方向一致性较好。`
        : `${hhadLine} 指向 ${hhadDirection}，与主盘 ${prediction.direction || score.direction} 存在结构分歧。`
    );
  } else {
    signals.push("暂无让球胜平负有效拆分，净胜球结构需结合比分盘观察。");
  }
  if (ttgFavorite) {
    signals.push(`总进球最热 ${ttgFavorite.label}（SP ${Number(ttgFavorite.odds || 0).toFixed(2)}），用于校验比分大小方向。`);
  }
  if (hafuFavorite) {
    signals.push(`半全场最热 ${hafuFavorite.label}（SP ${Number(hafuFavorite.odds || 0).toFixed(2)}），用于观察节奏是否支持主方向。`);
  }

  const risk =
    gap < 4
      ? "三项概率接近，主方向容错较低。"
      : gap < 8
        ? "优势存在但不算厚，建议继续观察临场 SP。"
        : "主盘优势较清晰，但仍缺外网交叉验证。";

  return `
    <li class="compare-fallback-card">
      <div class="compare-title">外网暂不可用，启用体彩内部比对</div>
      <div class="prob-bars">${renderProbabilityBars({
        home: (probs.home || 0) * 100,
        draw: (probs.draw || 0) * 100,
        away: (probs.away || 0) * 100,
      })}</div>
      <ul>
        ${signals.map((line) => `<li>${line}</li>`).join("")}
        <li class="${gap < 4 ? "delta-alert" : ""}">${risk}</li>
      </ul>
    </li>`;
}

function renderMatchIntelligence(intel) {
  const list = document.getElementById("match-intelligence");
  const detail = document.getElementById("intelligence-detail");
  const coverageEl = document.getElementById("intelligence-coverage");
  const card = document.getElementById("intelligence-card");
  if (!list || !card) return;

  if (!intel || (!intel.summary_bullets?.length && !intel.detail_sections)) {
    list.innerHTML = `<li class="empty-note">暂无赛前情报。世界杯场次接入 FIFA 三源后可自动带出；联赛场次展示 SP 定价与联赛背景。</li>`;
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
        const tags = (profile.style_tags || []).slice(0, 3).join(" · ") || "以 SP 与当期状态为主";
        let statsText = "";
        if (gs.points != null) {
          statsText = `${gs.points} 分 · 进 ${gs.goals_for || 0} 失 ${gs.goals_against || 0}`;
        } else if (team.market_hint) {
          statsText = team.market_hint;
        } else {
          const region = profile.region && profile.region !== "未知" ? profile.region : (team.league_label || intel.league || "联赛");
          const conf = profile.confederation && profile.confederation !== "未知" ? profile.confederation : "俱乐部赛事";
          statsText = `${region} · ${conf}`;
        }
        return `<div class="intel-team-card">
          <div class="intel-team-head">${team.name}</div>
          <div class="intel-team-meta">${statsText}</div>
          <div class="intel-team-tags">${tags}</div>
        </div>`;
      })
      .join("");

    const absenceBlock = (side, title) => {
      const lines = (ds.absences || {})[side] || [];
      const friendly = lines.filter((line) => !/overlay|公开 API|结构化数据/i.test(line));
      const displayLines = friendly.length ? friendly : lines;
      return `<div class="intel-absence-card">
        <div class="label">${title}</div>
        ${displayLines.length
          ? `<ul>${displayLines.map((line) => `<li>${line}</li>`).join("")}</ul>`
          : `<p class="hint">暂无官方伤停名单</p>`}
      </div>`;
    };

    const limitedBanner = intel.limited
      ? `<div class="intel-limited-banner">未接入 FIFA 三源，当前以联赛背景、体彩 SP 定价和人工情报为主；伤停名单未核实时不参与模型强结论。</div>`
      : "";
    const market = ds.market_snapshot || intel.market_snapshot;
    const officialUpdates = (ds.official_updates || intel.official_updates || [])
      .map((item) => {
        if (typeof item === "string") {
          return `<li>${item}</li>`;
        }
        const title = item.title || "官方更新";
        const source = item.source || "官方";
        const summary = item.summary ? `<p>${item.summary}</p>` : "";
        const link = item.url
          ? `<a href="${item.url}" target="_blank" rel="noopener noreferrer">打开来源</a>`
          : "";
        return `<li><strong>${title}</strong><span>${source}</span>${summary}${link}</li>`;
      })
      .join("");

    detail.innerHTML = `
      ${limitedBanner}
      ${market ? `<div class="intel-market-card">
        <div class="intel-market-head">
          <span>体彩 SP 定价</span>
          <strong>首选 ${market.favorite}</strong>
        </div>
        <div class="prob-bars">${renderProbabilityBars(market.probabilities)}</div>
        <p class="hint">SP ${market.had_line} · 作为联赛场次缺少三源时的主要量化参考</p>
      </div>` : ""}
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
      ${officialUpdates ? `<div class="intel-official-card"><div class="label">官方消息</div><ul>${officialUpdates}</ul></div>` : ""}
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
    cov.official_news_available ? "官方消息" : null,
    cov.overlay_used ? "人工情报" : null,
    cov.market_snapshot ? "SP 定价" : null,
    cov.injury_api ? "API 伤停" : "伤停待核实",
    intel.limited ? "联赛简版" : null,
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

async function postJson(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `请求失败：${response.status}`);
  }
  return payload;
}

function getAiChatHistory(matchId) {
  if (!aiChatByMatch[matchId]) {
    aiChatByMatch[matchId] = [];
  }
  return aiChatByMatch[matchId];
}

function renderAiStatusBadge() {
  const badge = document.getElementById("ai-status-badge");
  const hint = document.getElementById("ai-analysis-hint");
  const sendBtn = document.getElementById("ai-send-btn");
  if (!badge) return;

  if (!aiAnalyzeStatus) {
    badge.textContent = "检测中…";
    badge.className = "ai-status-badge";
    return;
  }

  if (aiAnalyzeStatus.configured) {
    badge.textContent = `DeepSeek · ${aiAnalyzeStatus.model || "deepseek-chat"}`;
    badge.className = "ai-status-badge ready";
    if (hint) {
      hint.textContent = "已连接 DeepSeek。选中左侧赛事后，可提问验证冷门假设（分析基于当前场次数据，非投注建议）。";
    }
    if (sendBtn) sendBtn.disabled = aiSending;
  } else {
    badge.textContent = "未配置 API Key";
    badge.className = "ai-status-badge missing";
    if (hint) {
      hint.textContent = "服务端未配置 DEEPSEEK_API_KEY：本地请在 .env 设置后重启 dashboard；线上请在 GitHub Secrets 或服务器环境变量配置（勿提交到公开仓库）。已生成的复盘仍可从缓存读取。";
    }
    if (sendBtn) sendBtn.disabled = aiSending;
  }
}

function escapeHtml(text) {
  return String(text || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderInlineMarkdown(text) {
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/`(.+?)`/g, "<code>$1</code>");
}

function renderMarkdown(text) {
  const lines = String(text || "").split("\n");
  const html = [];
  let inList = false;

  const closeList = () => {
    if (inList) {
      html.push("</ul>");
      inList = false;
    }
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    const trimmed = line.trim();

    if (/^[-*] /.test(trimmed)) {
      if (!inList) {
        html.push("<ul>");
        inList = true;
      }
      html.push(`<li>${renderInlineMarkdown(trimmed.replace(/^[-*] /, ""))}</li>`);
      continue;
    }

    closeList();

    if (!trimmed) {
      continue;
    }

    if (/^#{1,3}\s/.test(trimmed)) {
      const level = trimmed.match(/^#+/)[0].length;
      const tag = level === 1 ? "h4" : level === 2 ? "h5" : "h6";
      html.push(`<${tag}>${renderInlineMarkdown(trimmed.replace(/^#+\s*/, ""))}</${tag}>`);
      continue;
    }

    html.push(`<p>${renderInlineMarkdown(trimmed)}</p>`);
  }

  closeList();
  return html.join("") || `<p>${renderInlineMarkdown(text)}</p>`;
}

function formatAiChatContent(item) {
  if (item.role === "assistant loading") {
    return escapeHtml(item.content);
  }
  if (item.role === "assistant") {
    return renderMarkdown(item.content);
  }
  return escapeHtml(item.content).replace(/\n/g, "<br>");
}

function renderAiChatLog(matchId) {
  const log = document.getElementById("ai-chat-log");
  if (!log) return;
  const history = getAiChatHistory(matchId);
  if (!history.length) {
    log.innerHTML = `<div class="ai-chat-bubble assistant">选择快捷问题，或在下方输入你的冷门假设，AI 将基于当前场次的 SP、外网、多玩法与情报数据作答。</div>`;
    return;
  }
  log.innerHTML = history
    .map((item) => {
      const roleClass = item.role === "assistant loading" ? "assistant loading" : item.role;
      return `<div class="ai-chat-bubble ${roleClass}">${formatAiChatContent(item)}</div>`;
    })
    .join("");
  log.scrollTop = log.scrollHeight;
}

function isAiInputAllowed(match) {
  if (!match) return Boolean(activeMatchId);
  if (match.analysis_available === false) {
    return Boolean(match.ai_context_available || match.match_intelligence?.available);
  }
  return true;
}

function syncAiInputState(match) {
  const input = document.getElementById("ai-question-input");
  const sendBtn = document.getElementById("ai-send-btn");
  const matchId = match?.match_id || activeMatchId;
  const allowed = isAiInputAllowed(match);
  const ready = Boolean(aiAnalyzeStatus?.configured) && !aiSending && allowed;
  if (input) {
    input.disabled = !ready;
    input.readOnly = false;
    input.placeholder = allowed
      ? "例如：我感觉客队会爆冷，盘口和数据是否支持？"
      : "请先选择左侧未开售/未开赛赛事";
  }
  if (sendBtn) sendBtn.disabled = !ready || !matchId;
}

function renderAiAnalysisPanel(match, options = {}) {
  const { disabled = false, reason = "" } = options;
  renderAiStatusBadge();
  const input = document.getElementById("ai-question-input");
  const sendBtn = document.getElementById("ai-send-btn");
  const hint = document.getElementById("ai-analysis-hint");
  if (!document.getElementById("ai-float-panel")) return;

  if (disabled || !isAiInputAllowed(match)) {
    renderAiChatLog("");
    if (input) {
      input.value = "";
      input.disabled = true;
    }
    if (sendBtn) sendBtn.disabled = true;
    if (hint && reason) hint.textContent = reason;
    const log = document.getElementById("ai-chat-log");
    if (log) {
      log.innerHTML = `<div class="ai-chat-bubble assistant">${reason || "当前场次不支持 AI 分析。"}</div>`;
    }
    return;
  }

  if (hint) {
    hint.textContent = match?.card_type === "finished"
      ? "已完场复盘模式：可追问预测偏差原因、是否具备冷门信号等（基于赛前快照 + 实际赛果）。"
      : "已连接 DeepSeek。选中左侧赛事后，可提问验证冷门假设（分析基于当前场次数据，非投注建议）。";
  }
  syncAiInputState(match);
  renderAiChatLog(match?.match_id || activeMatchId || "");
}

async function sendAiQuestion(question) {
  const trimmed = (question || "").trim();
  const input = document.getElementById("ai-question-input");
  const sendBtn = document.getElementById("ai-send-btn");
  if (!trimmed || !activeMatchId || aiSending || !aiAnalyzeStatus?.configured) return;

  aiSending = true;
  if (sendBtn) sendBtn.disabled = true;
  if (input) input.disabled = true;

  const history = getAiChatHistory(activeMatchId);
  const priorHistory = history.slice();
  history.push({ role: "user", content: trimmed });
  history.push({ role: "assistant loading", content: "分析中，正在汇总 SP / 外网 / 多玩法上下文…" });
  renderAiChatLog(activeMatchId);

  try {
    const payload = await postJson("/api/analyze/chat", {
      match_id: activeMatchId,
      question: trimmed,
      history: priorHistory,
      foreign: "auto",
    });
    history.pop();
    if (payload.success) {
      history.push({ role: "assistant", content: payload.reply });
    } else {
      history.push({ role: "assistant", content: `分析失败：${payload.error || "未知错误"}` });
    }
  } catch (error) {
    history.pop();
    history.push({ role: "assistant", content: `分析失败：${error.message}` });
  } finally {
    aiSending = false;
    if (input) input.value = "";
    const cached = predictions.find((item) => item.match_id === activeMatchId);
    syncAiInputState(cached || { match_id: activeMatchId });
    renderAiChatLog(activeMatchId);
  }
}

async function loadAiReviewCache() {
  try {
    const payload = await fetchJson("/api/analyze/reviews");
    aiReviewCache = payload.reviews || {};
  } catch {
    aiReviewCache = {};
  }
}

function finishedNeedsAutoReview(match) {
  if (!match || match.card_type !== "finished") return false;
  if (match.settlement_status !== "settled") return false;
  return match.had_won === false || match.crs_won === false;
}

function applyAutoReviewToChat(matchId, review) {
  if (!review?.reply) return;
  const history = getAiChatHistory(matchId);
  const marker = "【自动复盘】";
  if (history.some((item) => item.role === "assistant" && String(item.content).includes(marker))) {
    return;
  }
  history.length = 0;
  history.push({ role: "user", content: review.question || "请复盘本场预测偏差。" });
  history.push({ role: "assistant", content: `${marker}\n\n${review.reply}` });
  renderAiChatLog(matchId);
}

async function maybeAutoReviewFinished(match) {
  if (!match?.match_id || !finishedNeedsAutoReview(match)) return;

  const matchId = match.match_id;
  let review = aiReviewCache[matchId];
  if (review?.reply) {
    applyAutoReviewToChat(matchId, review);
    return;
  }

  if (!aiAnalyzeStatus?.configured || aiAutoReviewPending.has(matchId)) return;

  aiAutoReviewPending.add(matchId);
  const history = getAiChatHistory(matchId);
  history.length = 0;
  history.push({ role: "assistant loading", content: "正在自动生成完场偏差复盘…" });
  renderAiChatLog(matchId);

  try {
    const payload = await postJson("/api/analyze/auto-review", { match_id: matchId });
    if (payload.success && payload.reply) {
      aiReviewCache[matchId] = payload;
      applyAutoReviewToChat(matchId, payload);
    } else if (payload.cached && payload.reply) {
      aiReviewCache[matchId] = payload;
      applyAutoReviewToChat(matchId, payload);
    } else {
      history.length = 0;
      history.push({
        role: "assistant",
        content: payload.error
          ? `自动复盘未生成：${payload.error}`
          : "自动复盘未生成，可手动提问或等待后台刷新。",
      });
      renderAiChatLog(matchId);
    }
  } catch (error) {
    history.length = 0;
    history.push({ role: "assistant", content: `自动复盘失败：${error.message}` });
    renderAiChatLog(matchId);
  } finally {
    aiAutoReviewPending.delete(matchId);
    syncAiInputState(match);
  }
}

async function loadAiAnalyzeStatus() {
  try {
    aiAnalyzeStatus = await fetchJson("/api/analyze/status");
  } catch (error) {
    aiAnalyzeStatus = { configured: false, provider: "deepseek" };
    console.error(error);
  }
  renderAiStatusBadge();
  const cached = predictions.find((item) => item.match_id === activeMatchId);
  syncAiInputState(cached || (activeMatchId ? { match_id: activeMatchId } : null));
}

function renderMeta() {
  const sporttery = overview.sporttery || {};
  const predictionsMeta = overview.predictions || {};
  const health = overview.provider_health || {};
  const unified = overview.unified_index || {};
  const stats = overview.dashboard_stats || {};
  const modeNote = overview.unified_mode_note;

  renderHeroDashboard(stats);
  renderTournamentForecast();

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

function renderTournamentForecast() {
  const forecast = overview?.tournament_forecast || {};
  const training = overview?.training_report || {};
  const ranking = document.getElementById("tournament-ranking");
  const pairs = document.getElementById("final-pairs");
  const meta = document.getElementById("tournament-meta");
  const state = document.getElementById("training-state");
  if (!ranking || !pairs) return;

  const rows = (forecast.rankings || []).slice(0, 8);
  ranking.innerHTML = rows.map((item, index) => `
    <div class="tournament-row">
      <span class="rank-number">${index + 1}</span>
      <strong>${item.team}</strong>
      <span>冠军 ${(item.champion_probability * 100).toFixed(1)}%</span>
      <span>决赛 ${(item.final_probability * 100).toFixed(1)}%</span>
      <span>亚军 ${(item.runner_up_probability * 100).toFixed(1)}%</span>
    </div>
  `).join("");
  pairs.innerHTML = `<h3>最可能冠亚军对阵</h3>${(forecast.final_pairs || []).slice(0, 5).map((item) => `
    <div class="final-pair-row"><span>${item.pair}</span><strong>${(item.probability * 100).toFixed(1)}%</strong></div>
  `).join("")}`;
  if (meta) meta.textContent = `${forecast.method || "固定对阵树推演"} · 截止 ${forecast.as_of || "—"}`;
  if (state) {
    state.textContent = training.activated
      ? `校准模型 · ${training.valid_samples}场`
      : `基线模型 · ${training.valid_samples || 0}/${training.minimum_samples || 500}场`;
  }
}

function shiftIsoDate(isoDate, deltaDays) {
  const [year, month, day] = isoDate.split("-").map(Number);
  const dt = new Date(Date.UTC(year, month - 1, day + deltaDays));
  return dt.toISOString().slice(0, 10);
}

function getAllDateTabs() {
  const stats = overview?.dashboard_stats || {};
  if (!dateTabsExpanded && Array.isArray(stats.date_tabs_default) && stats.date_tabs_default.length) {
    return stats.date_tabs_default;
  }
  return stats.date_tabs || [];
}

function getOlderDateTabs() {
  const stats = overview?.dashboard_stats || {};
  if (typeof stats.older_date_count === "number" && stats.older_date_count > 0 && Array.isArray(stats.date_tabs)) {
    const yesterday = getYesterdayDate();
    if (yesterday) {
      return stats.date_tabs.filter((tab) => tab.date && tab.date < yesterday);
    }
  }
  const yesterday = getYesterdayDate();
  if (!yesterday) return [];
  return (overview?.dashboard_stats?.date_tabs || []).filter((tab) => tab.date && tab.date < yesterday);
}

function getVisibleDateTabs() {
  return getAllDateTabs();
}

function getYesterdayDate() {
  const stats = overview?.dashboard_stats || {};
  if (stats.yesterday_date) return stats.yesterday_date;
  const yesterdayTab = (stats.date_tabs || []).find((tab) => tab.label === "昨日");
  if (yesterdayTab?.date) return yesterdayTab.date;
  if (stats.beijing_date) return shiftIsoDate(stats.beijing_date, -1);
  return "";
}

function findDateTab(dateValue) {
  return (overview?.dashboard_stats?.date_tabs || []).find((tab) => tab.date === dateValue)
    || getAllDateTabs()[0];
}

function sanitizeDateTabState() {
  const yesterday = getYesterdayDate();
  if (!yesterday) return;
  if (!dateTabsExpanded && selectedDate && selectedDate < yesterday) {
    selectedDate = yesterday;
    localStorage.setItem(DATE_TAB_STORAGE_KEY, selectedDate);
  }
}

function normalizeSelectedDate() {
  sanitizeDateTabState();
  const visible = getVisibleDateTabs();
  const exists = visible.some((tab) => tab.date === selectedDate);
  if (exists || (!selectedDate && visible.some((tab) => !tab.date))) {
    return;
  }
  selectedDate = pickDefaultDate();
  localStorage.setItem(DATE_TAB_STORAGE_KEY, selectedDate);
}

function setAiFloatMinimized(minimized) {
  const widget = document.getElementById("ai-float-widget");
  if (!widget) return;
  widget.classList.toggle("minimized", minimized);
  localStorage.setItem(AI_FLOAT_MINIMIZED_KEY, minimized ? "1" : "0");
}

function setupAiFloatWidget() {
  document.getElementById("ai-float-fab")?.addEventListener("click", () => {
    setAiFloatMinimized(false);
  });
  document.getElementById("ai-float-minimize")?.addEventListener("click", () => {
    setAiFloatMinimized(true);
  });
  const minimized = localStorage.getItem(AI_FLOAT_MINIMIZED_KEY) === "1";
  setAiFloatMinimized(minimized);
}

function renderHeroDashboard(stats) {
  const panel = document.getElementById("hero-dashboard");
  if (!panel || !stats.date_tabs) return;

  const tabs = stats.date_tabs;
  const buckets = stats.date_buckets || {};
  const todayTab = findDateTab(selectedDate) || tabs[0];
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
  return selectedDate;
}

function filterPredictions() {
  const selected = getSelectedDate();
  return allPredictions.filter((item) => !selected || item.match_date === selected);
}

function renderDateTabs() {
  const container = document.getElementById("date-tabs");
  const note = document.getElementById("date-filter-note");
  if (!container) return;

  normalizeSelectedDate();
  const tabs = getVisibleDateTabs();
  const olderTabs = getOlderDateTabs();
  const olderCount = overview?.dashboard_stats?.older_date_count ?? olderTabs.length;
  const buckets = overview?.dashboard_stats?.date_buckets || {};
  const tabButtons = tabs
    .map((tab) => {
      const bucket = buckets[tab.date] || { total: 0, unified: 0 };
      const active = tab.date === selectedDate ? "active" : "";
      const dateLabel = tab.date ? tab.date.slice(5) : "全部";
      return `<button type="button" class="date-tab ${active}" data-date="${tab.date}" role="tab">
        <span class="date-tab-label">${tab.label}</span>
        <span class="date-tab-sub">${dateLabel} · ${bucket.total}场${bucket.unified ? ` · 三源${bucket.unified}` : ""}</span>
      </button>`;
    })
    .join("");

  const expandButton = olderCount
    ? dateTabsExpanded
      ? `<button type="button" class="date-tab date-tab-expand" data-action="collapse">收起更早</button>`
      : `<button type="button" class="date-tab date-tab-expand" data-action="expand">展开更早 · ${olderCount}天</button>`
    : "";

  container.innerHTML = tabButtons + expandButton;

  container.querySelectorAll(".date-tab[data-date]").forEach((btn) => {
    btn.addEventListener("click", () => {
      selectedDate = btn.dataset.date || "";
      localStorage.setItem(DATE_TAB_STORAGE_KEY, selectedDate);
      renderMeta();
      renderHeroDashboard(overview.dashboard_stats || {});
      renderSportteryCards({ preserveMatch: true });
    });
  });

  container.querySelectorAll(".date-tab-expand").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      dateTabsExpanded = btn.dataset.action === "expand";
      if (!dateTabsExpanded) {
        sanitizeDateTabState();
      }
      renderMeta();
      renderHeroDashboard(overview.dashboard_stats || {});
      renderDateTabs();
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
  renderDirectionShift(null);
  renderDataSources({});
  renderAiAnalysisPanel(null, { disabled: true, reason: "请选择左侧赛事后再进行 AI 分析。" });
  Object.values(charts).forEach((chart) => chart?.destroy?.());
  charts = {};
}

function pickDefaultDate() {
  const tabs = getAllDateTabs();
  const buckets = overview?.dashboard_stats?.date_buckets || {};
  const preds = overview?.predictions?.predictions || [];
  const yesterday = getYesterdayDate();

  for (const tab of tabs) {
    if (!tab.date) continue;
    if (yesterday && tab.date < yesterday && !dateTabsExpanded) continue;
    const hasFinishedReview = preds.some(
      (item) => item.match_date === tab.date && item.card_type === "finished"
    );
    if (hasFinishedReview) return tab.date;
  }

  for (const tab of tabs) {
    if (!tab.date) continue;
    if (yesterday && tab.date < yesterday && !dateTabsExpanded) continue;
    const hasFinished = preds.some(
      (item) =>
        item.match_date === tab.date &&
        (item.card_type === "finished" || tab.label === "昨日")
    );
    if (hasFinished) return tab.date;
  }

  for (const tab of tabs) {
    if (!tab.date) continue;
    if (yesterday && tab.date < yesterday && !dateTabsExpanded) continue;
    const needsAttention = preds.some(
      (item) =>
        item.match_date === tab.date &&
        (item.lifecycle_phase === "live" ||
          item.lifecycle_phase === "awaiting_result" ||
          item.track_source === "journal")
    );
    if (needsAttention) return tab.date;
  }

  for (const tab of tabs) {
    if (!tab.date) continue;
    if (yesterday && tab.date < yesterday && !dateTabsExpanded) continue;
    const bucket = buckets[tab.date] || {};
    if (bucket.total > 0) {
      if (tab.label === "今天") return tab.date;
      if (tab.label === "昨日") return tab.date;
    }
  }

  const visible = getVisibleDateTabs();
  const firstDated = visible.find((tab) => tab.date);
  return firstDated?.date || "";
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
  const partialSelling = match.sale_status === "selling_partial" || match.sale_status === "selling";
  const statusLabel = partialSelling || match.direction === "已开售" ? "已开售" : "待开售";
  document.getElementById("prediction-direction").textContent = match.direction || statusLabel;
  document.getElementById("prediction-confidence").textContent =
    `比分 ${match.predicted_score || "—"} · ${match.confidence || statusLabel}`;
  document.getElementById("prediction-confidence").className = "badge-confidence tag low";
  document.getElementById("sporttery-stats").innerHTML = `
    <div class="stat-card">
      <div class="label">赛事状态</div>
      <div class="value">${statusLabel}</div>
      <div class="hint">${partialSelling ? "官网已开售；胜平负 HAD 暂未同步，让球/猜比分已可用" : "体彩官网已公布赛程，固定奖金暂未开放"}</div>
    </div>
    <div class="stat-card">
      <div class="label">${partialSelling ? "比分盘" : "预测比分"}</div>
      <div class="value" style="font-size:18px">${match.predicted_score || "—"}</div>
      <div class="hint">${(match.alt_scores || []).slice(0, 2).join("；") || "等待固定奖金明细"}</div>
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
      <div class="hint">HAD 返回后展示方向预测、SP走势、ttg/hafu 与凯利偏差</div>
    </div>
  `;
  document.getElementById("bet-simulation").innerHTML = "";
  document.getElementById("fusion-analysis").innerHTML =
    [
      match.direction_note || `体彩官网已公布 ${match.home} vs ${match.away}，当前为${statusLabel}状态。`,
      partialSelling ? "当前可使用 FIFA 官方消息、赛前情报和猜比分固定奖金做辅助判断；不做胜平负方向强结论。" : "固定奖金开放后，本平台会自动补齐完整分析。",
    ].map((line) => `<li>${line}</li>`).join("");
  document.getElementById("foreign-compare").innerHTML =
    `<li class="empty-note">${partialSelling ? "胜平负 HAD 暂未返回" : "待开售场次暂无体彩主盘"}，暂不计算外网差值。</li>`;
  renderMatchIntelligence(match.match_intelligence || null);
  renderPoolAnalysis(null);
  renderDirectionShift(null);
  renderDataSources({ data_sources: match.data_sources || { sporttery: true, unified: false, foreign: "待 SP" } });
  renderAiAnalysisPanel(match, {
    disabled: !isAiInputAllowed(match),
    reason: isAiInputAllowed(match)
      ? ""
      : "待开售场次暂无 SP 走势，请在固定奖金开放后再使用 AI 分析。",
  });
  Object.values(charts).forEach((chart) => chart?.destroy?.());
  charts = {};
}

function renderFinishedMatchDetail(match) {
  const hadHit = match.had_won === true ? "方向命中" : match.had_won === false ? "方向偏差" : "待赛果";
  const scoreHit = match.crs_won === true ? "比分命中" : match.crs_won === false ? "比分偏差" : "待赛果";
  document.getElementById("prediction-direction").textContent = match.actual_had || match.direction || "已完场";
  document.getElementById("prediction-confidence").textContent =
    `${hadHit} · ${scoreHit}`;
  document.getElementById("prediction-confidence").className =
    `badge-confidence tag ${match.had_won && match.crs_won ? "high" : match.had_won ? "mid" : "low"}`;

  document.getElementById("sporttery-stats").innerHTML = `
    <div class="stat-card">
      <div class="label">预测方向</div>
      <div class="value">${match.direction || "—"}</div>
      <div class="hint">${match.predicted_score ? `预测比分 ${match.predicted_score}` : "赛前预测快照"}</div>
    </div>
    <div class="stat-card">
      <div class="label">实际赛果</div>
      <div class="value">${match.actual_had || "待出"}</div>
      <div class="hint">${match.actual_score ? `实际比分 ${match.actual_score}` : "等待体彩官方赛果"}</div>
    </div>
    <div class="stat-card">
      <div class="label">结算结果</div>
      <div class="value">${match.settlement_status === "settled" ? `${match.total_pnl || 0} 元` : "待结算"}</div>
      <div class="hint">${hadHit} · ${scoreHit}</div>
    </div>
    <div class="stat-card">
      <div class="label">训练样本</div>
      <div class="value">${match.had_won === false || match.crs_won === false ? "已入库" : "无需纠偏"}</div>
      <div class="hint">方向或比分不一致时进入训练语料</div>
    </div>
  `;
  document.getElementById("bet-simulation").innerHTML = "";
  document.getElementById("fusion-analysis").innerHTML =
    [match.direction_note || "已完场复盘。"].map((line) => `<li>${line}</li>`).join("");
  document.getElementById("foreign-compare").innerHTML =
    `<li class="empty-note">已完场场次展示预测复盘，不再计算实时外网差值。</li>`;
  renderMatchIntelligence(null);
  renderPoolAnalysis(null);
  renderDirectionShift(null);
  renderDataSources({ data_sources: { sporttery: true, unified: Boolean(match.unified_linked), foreign: "复盘" } });
  renderAiAnalysisPanel(match);
  maybeAutoReviewFinished(match).catch(console.error);
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
      <article class="match-card sporttery-card ${item.unified_linked ? "unified-linked" : ""} ${item.analysis_available === false ? "pending-sale" : ""} ${item.card_type === "finished" ? "finished-card" : ""} ${item.lifecycle_phase === "live" ? "lifecycle-live" : ""} ${item.lifecycle_phase === "awaiting_result" ? "lifecycle-awaiting" : ""}" data-match-id="${item.match_id}">
        <div class="card-top">
          <h3>${item.home} vs ${item.away}</h3>
          <div class="card-badges">
            ${item.card_type === "finished" ? `<span class="shift-badge shift-medium">${item.date_tab_label || "已完场"}</span>` : renderShiftBadge(item.direction_shift)}
            <span class="countdown-badge">${item.countdown_label || "待定"}</span>
          </div>
        </div>
        <div class="stage">${sanitizeCardText(item.region_label) || `${item.league || "竞彩"} · ${(item.kickoff_beijing || "待定").slice(0, 16)}`}</div>
        <div class="tag-row">${renderDataTags(item.data_tags)}</div>
        <div class="odds-line">
          <span>${item.match_num || "—"}</span>
          <span>${item.card_type === "finished" ? "预测" : item.analysis_available === false ? "状态" : "方向"} ${item.direction}</span>
          <span>${item.card_type === "finished" && item.actual_had ? `实际 ${item.actual_had}` : `比分 ${item.predicted_score}`}</span>
          <span class="tag ${confidenceClass(item.confidence)}">${item.card_type === "finished" ? (item.settlement_status === "settled" ? (item.had_won && item.crs_won ? "命中" : "偏差") : "待赛果") : item.confidence}</span>
        </div>
        ${item.shift_prediction?.active && item.shift_prediction.changed ? `<div class="odds-line shift-adjusted-line"><span class="shift-adjusted-hint">变盘后 ${item.shift_prediction.adjusted.direction} · ${item.shift_prediction.adjusted.predicted_score}</span></div>` : ""}
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

  if (cached?.card_type === "finished") {
    renderFinishedMatchDetail(cached);
    return;
  }

  renderAiAnalysisPanel(cached);
  renderDirectionShift(cached?.direction_shift, cached?.shift_prediction);

  const payload = await fetchJson(`/api/sporttery/predict/${encodeURIComponent(matchId)}?foreign=auto`);
  const prediction = payload.prediction;
  const score = payload.score_prediction || cached;
  const shiftPrediction = payload.shift_prediction || score.shift_prediction || cached?.shift_prediction;

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
    ${shiftPrediction?.active ? `
    <div class="stat-card shift-stat-card">
      <div class="label">${shiftPrediction.initial?.label || "初盘/首次"}</div>
      <div class="value">${shiftPrediction.initial?.direction || "—"}</div>
      <div class="hint">比分 ${shiftPrediction.initial?.predicted_score || "—"}</div>
    </div>
    <div class="stat-card shift-stat-card adjusted">
      <div class="label">${shiftPrediction.adjusted?.label || "变盘后"}</div>
      <div class="value">${shiftPrediction.adjusted?.direction || "—"}</div>
      <div class="hint">比分 ${shiftPrediction.adjusted?.predicted_score || "—"}</div>
    </div>` : ""}
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
  renderDirectionShift(payload.direction_shift || prediction.direction_shift, shiftPrediction);
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
    compare.innerHTML = renderSportteryFallbackCompare(payload);
  }

  renderSportteryCharts(payload);
  await renderBetSimulation(matchId);
  renderAiAnalysisPanel(cached);
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
  const epoch = summary.settlement_epoch || "2026-06-30";
  document.getElementById("settlement-summary").innerHTML = `
    <div class="stat-card">
      <div class="label">待结算</div>
      <div class="value">${summary.open_count || 0} 场</div>
      <div class="hint">实盘预测日志</div>
    </div>
    <div class="stat-card">
      <div class="label">实盘已结算</div>
      <div class="value">${summary.settled_count || 0} 场</div>
      <div class="hint">自 ${epoch} 起累计</div>
    </div>
    <div class="stat-card">
      <div class="label">训练样本</div>
      <div class="value">${summary.training_samples || summary.training_miss_count || 0} 条</div>
      <div class="hint">预测偏差 ${summary.training_miss_count || 0} · 语料 ${summary.training_count || 0}</div>
    </div>
    <div class="stat-card">
      <div class="label">累计盈亏</div>
      <div class="value ${(summary.total_pnl || 0) >= 0 ? "positive" : "negative"}">${summary.total_pnl || 0} 元</div>
      <div class="hint">仅统计实盘已结算场次</div>
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
    dateTabsExpanded = false;
    selectedDate = pickDefaultDate();
    localStorage.setItem(DATE_TAB_STORAGE_KEY, selectedDate);
  } else {
    sanitizeDateTabState();
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
      if (item.lifecycle_phase && item.lifecycle_phase !== "upcoming") return;
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
  dateTabsExpanded = false;
  if (savedDateTab !== null) {
    selectedDate = savedDateTab;
  }
  setupAiFloatWidget();
  if (saved !== null) select.value = saved;
  if (savedMode) modeSelect.value = savedMode;
  setupAutoRefresh(Number(select.value));

  select.addEventListener("change", () => setupAutoRefresh(Number(select.value)));
  modeSelect.addEventListener("change", () => {
    localStorage.setItem(DATA_MODE_STORAGE_KEY, modeSelect.value);
    selectedDate = "";
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

  document.getElementById("ai-send-btn")?.addEventListener("click", () => {
    const input = document.getElementById("ai-question-input");
    sendAiQuestion(input?.value || "").catch(console.error);
  });
  document.getElementById("ai-question-input")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendAiQuestion(event.target.value).catch(console.error);
    }
  });
  document.querySelectorAll(".ai-prompt-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const prompt = btn.dataset.prompt || "";
      const input = document.getElementById("ai-question-input");
      if (input) input.value = prompt;
      sendAiQuestion(prompt).catch(console.error);
    });
  });

  await loadAiAnalyzeStatus();
  await loadAiReviewCache();

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
