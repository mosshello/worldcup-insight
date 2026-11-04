export const tabs = [
  ["overview", "赛事概览"],
  ["teams", "球队与参赛"],
  ["sources", "数据接入"],
  ["datasets", "数据集与质量"],
  ["training", "训练与评估"],
  ["models", "模型与使用"],
];
export const statusNames = {
  available: "请求成功",
  partial: "部分覆盖",
  error: "请求失败",
  failed: "失败",
  not_configured: "未配置",
  pending_validation: "待验收",
  collected: "已采集",
  collecting: "采集中",
  queued: "排队中",
  running: "运行中",
  completed: "已完成",
  succeeded: "已完成",
  ready: "就绪",
  blocked: "阻断",
  unknown: "待核实",
  active: "有效",
  verified: "已核实",
  pending: "待处理",
  open: "待处理",
  interrupted: "已中断",
  fit: "拟合",
  evaluate: "固定模型评估",
  experiment: "滚动实验",
};
export const methods = [
  { label: "攻防泊松", value: "poisson" },
  { label: "两阶段低比分修正", value: "dc" },
  { label: "联合低比分修正", value: "joint_dc" },
  { label: "动态 Gamma–Poisson", value: "dynamic_gamma" },
  { label: "Elo 逻辑回归", value: "elo_logistic" },
  { label: "CatBoost 胜平负", value: "catboost" },
  { label: "LightGBM 进球", value: "lightgbm_goals" },
];
export const shortVersion = (value) =>
  value ? String(value).slice(0, 8) : "—";
export function workspaceData(payload, id) {
  if (!payload) return null;
  const workspace = payload.workspace || payload.competition_workspaces?.[id];
  return workspace ? { ...payload, ...workspace } : payload;
}
export function taskBody(form, datasetDigest) {
  if (!datasetDigest) throw new Error("请先选择数据版本");
  const body = {
    kind: form.kind,
    target: form.target,
    dataset_digest: datasetDigest,
  };
  if (form.kind === "evaluate") {
    if (!form.run_id) throw new Error("请选择评估模型");
    if (!form.date_from || !form.date_to || form.date_from > form.date_to)
      throw new Error("请填写有效评估区间");
    Object.assign(body, {
      run_id: form.run_id,
      date_from: form.date_from,
      date_to: form.date_to,
    });
  } else if (form.kind === "fit") {
    if (
      !form.algorithm ||
      !Number.isFinite(Number(form.half_life)) ||
      Number(form.half_life) <= 0
    )
      throw new Error("请选择算法与有效衰减天数");
    Object.assign(body, {
      algorithm: form.algorithm,
      half_life: Number(form.half_life),
    });
  }
  return body;
}
export function reportFields(response, runId) {
  const fields = [["运行版本", runId]];
  const report =
    response?.report ??
    response?.evaluation ??
    (response?.run ? null : response);
  if (!report) return [...fields, ["评估状态", "暂无报告"]];
  const names = {
    fixed_model: "固定模型评估",
    rolling_strategy: "滚动策略评估",
    unassessed_artifact: "产物尚未评估",
    research_only: "研究评估",
    not_evaluated: "尚未评估",
  };
  for (const [key, label] of [
    ["evaluation_type", "评估类型"],
    ["report_type", "报告类型"],
    ["competition_id", "赛事"],
    ["dataset_digest", "数据版本"],
    ["created_at", "生成时间"],
    ["target", "目标"],
    ["evaluation_protocol", "评估协议"],
    ["date_from", "开始时间"],
    ["date_to", "结束时间"],
    ["trained_before", "训练截止"],
    ["status", "评估状态"],
    ["limitations", "限制"],
  ])
    if (report[key] != null)
      fields.push([label, names[report[key]] || report[key]]);
  for (const [key, label] of [
    ["test", "测试成绩"],
    ["metrics", "评估成绩"],
    ["validation", "验证成绩"],
  ])
    if (report[key]) fields.push([label, report[key]]);
  if (Array.isArray(report.records))
    fields.push(["逐场记录数", report.records.length]);
  for (const evaluation of response?.evaluations || [])
    fields.push([
      "独立评估 " + (evaluation.id || evaluation.report_id || ""),
      {
        类型: names[evaluation.evaluation_type] || evaluation.evaluation_type,
        模型: evaluation.run_id,
        开始: evaluation.date_from,
        结束: evaluation.date_to,
        成绩: evaluation.test,
      },
    ]);
  return fields;
}
