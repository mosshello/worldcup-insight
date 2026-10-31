export const demoDate = "2026-10-28";
const recentA = [
  ["10/24", "英超", "埃弗顿", "主", "2-0"],
  ["10/20", "欧冠", "本菲卡", "客", "1-2"],
  ["10/17", "英超", "富勒姆", "客", "3-1"],
  ["10/13", "英超", "布伦特福德", "主", "2-1"],
  ["10/09", "英超", "伯恩茅斯", "主", "2-0"],
];
const recentB = [
  ["10/25", "意甲", "都灵", "主", "2-0"],
  ["10/21", "欧冠", "波尔图", "客", "1-1"],
  ["10/18", "意甲", "罗马", "客", "2-0"],
  ["10/14", "意甲", "那不勒斯", "主", "0-1"],
  ["10/10", "意甲", "拉齐奥", "主", "3-0"],
];
const teams = [
  ["阿森纳", "国际米兰", "欧冠", "04:00", 2.05, 3.25, 3.5],
  ["曼城", "多特蒙德", "欧冠", "04:00", 1.55, 4, 5.2],
  ["切尔西", "纽卡斯尔", "英超", "03:45", 2.2, 3.4, 2.85],
  ["巴塞罗那", "瓦伦西亚", "西甲", "04:00", 1.35, 4.8, 7],
  ["利物浦", "马德里竞技", "欧冠", "04:00", 1.92, 3.3, 3.75],
  ["拜仁慕尼黑", "巴黎圣日耳曼", "欧冠", "04:00", 1.88, 3.65, 3.6],
  ["罗马", "AC米兰", "意甲", "03:45", 2.6, 3.1, 2.4],
  ["皇家马德里", "比利亚雷亚尔", "西甲", "04:00", 1.42, 4.4, 6.3],
];
export const demoMatches = teams.map((t, i) => ({
  id: `demo-${i + 1}`,
  num: `周三${String(i + 1).padStart(3, "0")}`,
  home: t[0],
  away: t[1],
  league: t[2],
  time: t[3],
  date: i > 5 ? "2026-10-29" : demoDate,
  kickoff: `${i > 5 ? "2026-10-30" : "2026-10-29"}T${t[3]}:00+08:00`,
  sale: i === 7 ? "pending" : "selling",
  single: i % 3 !== 1,
  odds: t.slice(4),
  markets:
    i === 7
      ? []
      : i % 2
        ? ["had", "hhad", "crs"]
        : ["had", "hhad", "ttg", "crs", "hafu"],
  analysisState: i === 0 ? "已分析" : i % 2 ? "未分析" : "待更新",
  isDemo: true,
  stats: [
    ["近期战绩", "4胜0平1负", "3胜1平1负"],
    ["场均进球", "2.0", "1.6"],
    ["场均失球", "0.8", "0.6"],
    ["场均 xG", "1.82", "1.55"],
    ["场均 xGA", "0.91", "0.88"],
    ["场均射正", "5.2", "4.6"],
  ],
  form: [recentA, recentB],
  lineups: {
    confirmed: false,
    formations: ["4-3-3", "3-5-2"],
    names: [
      [
        "门将",
        "左后卫",
        "中后卫",
        "中后卫",
        "右后卫",
        "中场",
        "中场",
        "中场",
        "左边锋",
        "前锋",
        "右边锋",
      ],
      [
        "门将",
        "中后卫",
        "中后卫",
        "中后卫",
        "左翼卫",
        "中场",
        "中场",
        "中场",
        "右翼卫",
        "前锋",
        "前锋",
      ],
    ],
    injuries: [
      [
        { player: "主力边锋", issue: "伤情待评估", status: "待确认" },
        { player: "轮换后卫", issue: "停赛", status: "已确认" },
        { player: "主力中场", issue: "恢复训练", status: "待确认" },
      ],
      [
        { player: "主力前锋", issue: "随队训练", status: "待确认" },
        { player: "轮换中场", issue: "停赛", status: "已确认" },
      ],
    ],
  },
  history: [
    { time: "12:00", h: t[4] + 0.1, d: t[5] - 0.05, a: t[6] - 0.2 },
    { time: "14:00", h: t[4] + 0.08, d: t[5] - 0.05, a: t[6] - 0.15 },
    { time: "15:40", h: t[4] + 0.05, d: t[5] - 0.03, a: t[6] - 0.1 },
    { time: "17:15", h: t[4] + 0.03, d: t[5], a: t[6] - 0.05 },
    { time: "18:32", h: t[4], d: t[5], a: t[6] },
  ],
  foreign: [
    {
      company: "外盘 A（示例）",
      home: t[4] + 0.12,
      draw: t[5] + 0.2,
      away: t[6] + 0.25,
      time: "18:31",
    },
    {
      company: "外盘 B（示例）",
      home: t[4] + 0.15,
      draw: t[5] + 0.15,
      away: t[6] + 0.3,
      time: "18:32",
    },
  ],
}));
export function demoAnalysis(m, version = 1) {
  return {
    id: crypto.randomUUID(),
    version,
    created: new Date().toISOString(),
    matchId: m.id,
    title: `${m.home}主场略占优，平局仍需防范。`,
    summary: "主队机会创造更好，客队防守表现接近。确认首发是下一次复核的重点。",
    probabilities: [49, 28, 23],
    probLabel: "模型预测（示例）",
    goals: "2–3 球",
    scores: "1-0 / 1-1 / 2-1",
    evidence: [
      {
        title: "机会创造",
        text: "近5场场均 xG 1.82 对 1.55，主队占优。",
        tab: "basic",
      },
      {
        title: "防守表现",
        text: "场均 xGA 0.91 对 0.88，客队略好。",
        tab: "basic",
      },
      {
        title: "市场变化",
        text: `主胜 SP 从 ${(m.odds[0] + 0.1).toFixed(2)} 降至 ${m.odds[0].toFixed(2)}，价格变化需要结合阵容复核。`,
        tab: "odds",
      },
    ],
    risks: [
      "主队边锋出场情况尚未确认。",
      "客队若限制边路推进，比赛可能转为低比分。",
    ],
    tactics:
      "观察主队边路推进与客队翼卫回收的对位。当前为预计阵容版，具体战术组合需在首发公布后重新确认。",
    isDemo: true,
  };
}
export const demoReviews = demoMatches
  .slice(0, 4)
  .map((m, i) => ({
    id: `review-${i}`,
    matchId: m.id,
    date: i < 2 ? "2026-10-27" : "2026-10-26",
    league: m.league,
    home: m.home,
    away: m.away,
    pick: i === 2 ? "平局" : "主胜",
    score: ["1-1", "2-0", "1-2", "3-1"][i],
    consistent: i % 2 === 1,
    status: i === 0 ? "待复盘" : "已复盘",
    note: "",
    probabilities: [49, 28, 23],
  }));
