export const marketNames = {
  had: "胜平负",
  hhad: "让球胜平负",
  ttg: "总进球",
  crs: "比分",
  hafu: "半全场",
};
export function price(v) {
  return Number.isFinite(Number(v)) && Number(v) > 0
    ? Number(v).toFixed(2)
    : "—";
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
export async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
    signal: options.signal || AbortSignal.timeout(90000),
  });
  const type = response.headers.get("content-type") || "";
  if (!type.includes("application/json"))
    throw new Error("当前地址未连接数据服务，请启动足球工作台服务后重试。");
  const data = await response.json();
  if (!response.ok || data.success === false || data.error)
    throw new Error(data.error || "请求失败，请稍后重试");
  return data;
}
