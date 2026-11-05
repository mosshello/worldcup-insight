export function validPrice(value) {
  return typeof value === "number" && Number.isFinite(value) && value > 1;
}
export function movement(current, previous) {
  if (!Number.isFinite(current) || !Number.isFinite(previous))
    return { text: "—", direction: "flat" };
  const delta = current - previous;
  return {
    text:
      Math.abs(delta) < 0.0001
        ? "持平"
        : `${delta > 0 ? "↑ +" : "↓ "}${delta.toFixed(2)}`,
    direction: delta > 0.0001 ? "up" : delta < -0.0001 ? "down" : "flat",
  };
}
export function optionValue(pool, key, mode = "odds") {
  const options = pool?.options || [],
    option = options.find((o) => o.key === key);
  if (!option || !validPrice(option.odds)) return null;
  if (mode === "odds") return option.odds;
  if (!options.length || options.some((o) => !validPrice(o.odds))) return null;
  return 100 / option.odds / options.reduce((n, o) => n + 1 / o.odds, 0);
}
export function trendRows(
  history,
  pool,
  code,
  hours = "all",
  now = Date.now(),
) {
  const seen = new Set();
  return (history || [])
    .filter((p) => {
      const time = Date.parse(p.updated_at);
      if (
        !Number.isFinite(time) ||
        time > now ||
        (hours !== "all" && time < now - Number(hours) * 3600000)
      )
        return false;
      // Handicap changes are distinct markets; never draw across different lines.
      if (code === "hhad" && (pool?.line == null || p.line !== pool.line))
        return false;
      const digest = JSON.stringify([p.updated_at, p.line, p.options]);
      if (seen.has(digest)) return false;
      seen.add(digest);
      return true;
    })
    .sort((a, b) => Date.parse(a.updated_at) - Date.parse(b.updated_at));
}

const beijingTime = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});
export function quoteTime(value) {
  return Number.isFinite(Date.parse(value))
    ? beijingTime.format(new Date(value))
    : "时间未知";
}
export function quoteInterval(rows, current) {
  const index = rows.findIndex((r) => r.updated_at === current?.updated_at);
  const previous = index > 0 ? rows[index - 1] : null;
  return {
    previous,
    label: previous
      ? `${quoteTime(previous.updated_at)} → ${quoteTime(current.updated_at)}`
      : `${quoteTime(current?.updated_at)} · 最早取得`,
  };
}
export function relativeMovement(current, previous, mode = "odds") {
  if (!Number.isFinite(current) || !Number.isFinite(previous) || previous <= 0)
    return "无可比记录";
  const delta =
    mode === "probability"
      ? current - previous
      : ((current - previous) / previous) * 100;
  return `${delta > 0 ? "+" : ""}${delta.toFixed(2)}${mode === "probability" ? "个百分点" : "%"}`;
}
// This is time remaining AT the recorded quote, never a live countdown on old quotes.
export function kickoffDistance(kickoff, recordedAt) {
  const distance = Date.parse(kickoff) - Date.parse(recordedAt);
  if (!Number.isFinite(distance)) return "开赛时间待确认";
  if (distance <= 0) return "已到开赛时间";
  if (distance < 60000) return "当时距开赛不足1分钟";
  const minutes = Math.floor(distance / 60000),
    days = Math.floor(minutes / 1440);
  return `当时距开赛 ${days ? days + "天" : ""}${Math.floor((minutes % 1440) / 60)}小时${minutes % 60}分`;
}
