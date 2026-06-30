const OUTCOME_LABELS = { home: "主胜", draw: "平", away: "客胜" };
const OUTCOME_COLORS = {
  home: "#22c55e",
  draw: "#f59e0b",
  away: "#ef4444",
};
const REFRESH_STORAGE_KEY = "dashboardAutoRefreshSeconds";

let overview = null;
let predictions = [];
let activeMatchId = null;
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
  if (!sporttery.success) {
    document.getElementById("sporttery-status").textContent = sporttery.error || "体彩 API 暂不可用";
    return;
  }
  const cacheHint = sporttery.cached || predictionsMeta.cached
    ? `（缓存 ${sporttery.cached_at || predictionsMeta.cached_at || ""}）`
    : "（实时）";
  document.getElementById("sporttery-status").textContent =
    `${sporttery.match_count} 场未开赛 ${cacheHint}`;
}

function renderSportteryCards() {
  const grid = document.getElementById("sporttery-grid");
  predictions = (overview.predictions && overview.predictions.predictions) || [];

  if (!predictions.length) {
    grid.innerHTML = `<p class="empty-note">当前没有未开赛的体彩足球赛事，或 API 暂时不可用。请稍后刷新。</p>`;
    return;
  }

  grid.innerHTML = predictions
    .map((item) => `
      <article class="match-card sporttery-card" data-match-id="${item.match_id}">
        <div class="card-top">
          <h3>${item.home} vs ${item.away}</h3>
          <span class="countdown-badge">${item.countdown_label || "待定"}</span>
        </div>
        <div class="stage">${item.league || "竞彩"} · ${item.kickoff_beijing || "待定"}</div>
        <div class="odds-line">
          <span>方向 ${item.direction}</span>
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
  const initialId = urlMatchId && predictions.some((item) => item.match_id === urlMatchId)
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

  const payload = await fetchJson(`/api/sporttery/predict/${encodeURIComponent(matchId)}?foreign=fox`);
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
      <div class="value" style="font-size:16px">${score.fox_source || "暂无"}</div>
      <div class="hint">${score.fox_moneyline}</div>
    </div>
  `;

  document.getElementById("fusion-analysis").innerHTML =
    [...(prediction.analysis || []), score.direction_note].filter(Boolean).map((line) => `<li>${line}</li>`).join("");

  const compare = document.getElementById("foreign-compare");
  if (prediction.foreign.probabilities) {
    const fp = prediction.foreign.probabilities;
    const sp = prediction.probabilities;
    compare.innerHTML = ["home", "draw", "away"]
      .map((key) => {
        const delta = (fp[key] - sp[key]) * 100;
        const sign = delta >= 0 ? "+" : "";
        return `<li>${OUTCOME_LABELS[key]}：体彩 ${pct(sp[key])}｜外网 ${pct(fp[key])}｜差值 ${sign}${delta.toFixed(1)}pp</li>`;
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
            <th>实际</th>
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
              <td class="${(row.total_pnl || 0) >= 0 ? "positive" : "negative"}">${row.total_pnl ?? "—"}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

async function refreshOverview(options = {}) {
  const { preserveMatch = true } = options;
  overview = await fetchJson("/api/overview");
  renderMeta();
  renderSettlementSummary();
  renderSportteryCards();
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
  const saved = localStorage.getItem(REFRESH_STORAGE_KEY);
  if (saved !== null) select.value = saved;
  setupAutoRefresh(Number(select.value));

  select.addEventListener("change", () => setupAutoRefresh(Number(select.value)));

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

  await refreshOverview({ preserveMatch: false });
  setupCountdownTicker();
}

init().catch((error) => {
  document.body.innerHTML = `<pre style="color:#fff;padding:24px">加载失败：${error.message}</pre>`;
});
