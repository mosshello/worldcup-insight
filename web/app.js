const OUTCOME_LABELS = { home: "主胜", draw: "平", away: "客胜" };
const OUTCOME_COLORS = {
  home: "#22c55e",
  draw: "#f59e0b",
  away: "#ef4444",
};

let overview = null;
let predictions = [];
let activeMatchId = null;
let charts = {};

function confidenceClass(level) {
  if (level === "高") return "high";
  if (level === "中") return "mid";
  return "low";
}

function pct(value) {
  return `${(value * 100).toFixed(1)}%`;
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
        <h3>${item.home} vs ${item.away}</h3>
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

  loadSportteryDetail(predictions[0].match_id);
}

async function loadSportteryDetail(matchId) {
  activeMatchId = matchId;
  const cached = predictions.find((item) => item.match_id === matchId);

  document.querySelectorAll(".sporttery-card").forEach((card) => {
    card.classList.toggle("active", card.dataset.matchId === matchId);
  });

  if (cached) {
    document.getElementById("prediction-direction").textContent = cached.direction;
    document.getElementById("prediction-confidence").textContent = `比分 ${cached.predicted_score} · 信心 ${cached.confidence}`;
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

async function init() {
  overview = await fetchJson("/api/overview");
  renderMeta();
  renderSportteryCards();
  document.getElementById("refresh-btn").addEventListener("click", async () => {
    overview = await fetchJson("/api/overview");
    renderMeta();
    renderSportteryCards();
  });
}

init().catch((error) => {
  document.body.innerHTML = `<pre style="color:#fff;padding:24px">加载失败：${error.message}</pre>`;
});
