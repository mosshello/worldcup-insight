export const marketNames = {
  had: "胜平负",
  hhad: "让球胜平负",
  ttg: "总进球",
  crs: "比分",
  hafu: "半全场",
};
export const outcomeNames = ["主胜", "平局", "客胜"];
export function price(v) {
  return Number.isFinite(Number(v)) && Number(v) > 0
    ? Number(v).toFixed(2)
    : "—";
}
export function normalizeLiveMatch(m) {
  const raw = m.pools || {};
  const had = raw.had;
  const odds = had
    ? [had.home ?? had.h, had.draw ?? had.d, had.away ?? had.a].map(
        (x) => Number(x) || null,
      )
    : [];
  const markets = Object.keys(marketNames).filter((k) => raw[k]);
  return {
    id: String(m.match_id),
    num: m.match_num || "未编号",
    league: m.league || "未分类",
    home: m.home || "未知主队",
    away: m.away || "未知客队",
    date: m.business_date || m.match_date || "",
    kickoff: m.kickoff_beijing || m.kickoff || "",
    time: m.match_time || m.kickoff_beijing?.slice(11, 16) || "—",
    sale: m.sale_status === "selling" ? "selling" : "pending",
    single:
      m.betting_single === true ||
      m.betting_single === 1 ||
      m.betting_single === "1",
    odds,
    markets,
    raw,
    analysisState: "未分析",
    lineups: null,
    stats: null,
    form: null,
    history: [],
    foreign: [],
    isDemo: false,
  };
}
export function filterMatches(
  matches,
  {
    date = "",
    league = "",
    market = "",
    query = "",
    single = false,
    followed = false,
    sale = "selling",
  },
  favorites = [],
) {
  return matches.filter(
    (m) =>
      (!date || m.date === date) &&
      (!league || m.league === league) &&
      (!market || m.markets.includes(market)) &&
      (!single || m.single) &&
      (!followed || favorites.includes(m.id)) &&
      (!sale || m.sale === sale) &&
      `${m.home}${m.away}${m.num}`
        .toLowerCase()
        .includes(query.trim().toLowerCase()),
  );
}
export function csvCell(value) {
  const s = String(value ?? "");
  return (
    '"' + (/^[=+@\-\t\r]/.test(s) ? "'" : "") + s.replaceAll('"', '""') + '"'
  );
}
export function toCsv(rows, cols) {
  return (
    "\uFEFF" +
    [
      cols.map((c) => csvCell(c.title)).join(","),
      ...rows.map((r) => cols.map((c) => csvCell(r[c.key])).join(",")),
    ].join("\r\n")
  );
}
export function validTrainingRange(a, b, c) {
  const valid = (v) =>
    /^\d{4}-\d{2}-\d{2}$/.test(v) &&
    !Number.isNaN(Date.parse(v)) &&
    new Date(v).toISOString().slice(0, 10) === v;
  return valid(a) && valid(b) && valid(c) && a < b && b < c;
}
export async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
    signal: options.signal || AbortSignal.timeout(90000),
  });
  const type = response.headers.get("content-type") || "";
  if (!type.includes("application/json"))
    throw new Error(
      "当前地址未连接数据服务。请通过 Python 服务打开，或切换示例数据。",
    );
  const data = await response.json();
  if (!response.ok || data.success === false || data.error)
    throw new Error(data.error || "请求失败，请稍后重试");
  return data;
}
