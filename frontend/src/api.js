import { request } from "./domain.js";
export const api = {
  matches: () => request("/api/v1/matches"),
  scores: (refresh = false) =>
    request("/api/v1/scores" + (refresh ? "?refresh=1" : "")),
  sync: () => request("/api/v1/sync", { method: "POST", body: "{}" }),
  state: () => request("/api/v1/state"),
  capabilities: () => request("/api/v1/capabilities"),
  evaluate: (competition_id, target = "full_time") =>
    request("/api/v1/training/evaluate", {
      method: "POST",
      body: JSON.stringify({ competition_id, target }),
    }),
  selectModel: (competition_id, run_id) =>
    request("/api/v1/models/select", {
      method: "POST",
      body: JSON.stringify({ competition_id, run_id }),
    }),
  importDataset: (competition_id, dataset) =>
    request("/api/v1/datasets/import", {
      method: "POST",
      body: JSON.stringify({ competition_id, dataset }),
    }),
  evidence: (id, body) =>
    request(`/api/v1/matches/${encodeURIComponent(id)}/evidence`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  detail: (id, refresh = false) =>
    request(
      `/api/v1/matches/${encodeURIComponent(id)}${refresh ? "?refresh=1" : ""}`,
    ),
  predict: (id, narrative = false) =>
    request(`/api/v1/matches/${encodeURIComponent(id)}/predict`, {
      method: "POST",
      body: JSON.stringify({ narrative }),
    }),
  save: (kind, key, value) =>
    request("/api/v1/state", {
      method: "POST",
      body: JSON.stringify({ kind, key, value }),
    }),
  reviews: () => request("/api/v1/reviews"),
  reviewUpdates: (after = null) =>
    request(
      "/api/v1/review-updates" + (after === null ? "" : `?after=${after}`),
    ),
  generateReview: (match_id) =>
    request("/api/v1/reviews/generate", {
      method: "POST",
      body: JSON.stringify({ match_id }),
    }),
  refreshReviews: () =>
    request("/api/v1/reviews/sync", { method: "POST", body: "{}" }),
};
export function adaptMatch(m) {
  const had = m.markets?.had;
  return {
    ...m,
    num: m.number,
    date: m.sale_date,
    time: m.kickoff.slice(11, 16),
    sale: m.sale_status,
    odds: ["h", "d", "a"].map(
      (k) => had?.options.find((o) => o.key === k)?.odds,
    ),
    pools: m.markets,
    markets: Object.keys(m.markets || {}).filter(
      (k) => m.markets[k].options.length,
    ),
    single: Object.values(m.markets || {}).some((p) => p.single),
    analysisState: m.analysis_count ? "已分析" : "未分析",
  };
}
export function timeLabel(v) {
  return v
    ? new Date(v).toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai",
        hour12: false,
      })
    : "未知";
}
export const lifecycleNames = {
  upcoming: "未开赛",
  started: "已开赛 · 待核实",
  live: "进行中",
  completed: "已结束",
  postponed: "延期",
  cancelled: "取消",
  suspended: "比赛暂停",
};
export const saleNames = {
  selling: "在售",
  pending: "待开售",
  closed: "已停售",
  unknown: "待核实",
};
