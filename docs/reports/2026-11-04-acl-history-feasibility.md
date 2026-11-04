# 亚冠历史接入与真实资格核验

核验时间：2026-11-04 17:09（北京时间）。本报告只记录采集与研究样本，不代表已经训练、选用或预测。

## 结论

已形成 485 场、7 个赛季的单赛事研究候选，满足现有总量 300 场 / 3 赛季要求。四支目标球队都有至少 5 场历史，但大阪钢巴和鹿岛鹿角近 24 个月各只有 3 场；其他样本分别追溯到 2016–2017 和 2019。不能把满足累计场数解释为当前球队实力得到充分验证。

来源归属始终为 ESPN `afc.champions` / league ID `3902`，没有混入国内联赛或亚冠二级。旧 ACL 与 ACL Elite 保留 `rules_era`；仅提出小组/联赛阶段、常规完场、非中立场的连续研究政策。该政策仍需按年代和当前阶段评估，不能从来源共用一个 slug 推断模型效果稳定。

## 实际取得的数据

| 赛季（来源 event.season.year） | 研究候选 |
|---|---:|
| 2016 | 18 |
| 2017 | 6 |
| 2019 | 96 |
| 2023 | 119 |
| 2024 | 89 |
| 2025 | 96 |
| 2026 | 61 |
| 合计 | 485 |

2015、2016、2017、2019–2026 共 11 个日历年请求返回 1,292 条唯一比赛。日历年不是赛季，2024 年响应同时含 2023/24 和 2024/25；解析仅用事件自己的赛季，不以请求年份覆盖。

807 场隔离：321 场集中赛会制版本待逐场场地验证；196 场淘汰赛规则/模型适用范围未开放；113 场非明确常规完场（未开赛、取消、延期、加时或点球等）；177 场未取得精确详情核验，主要是早期赛季非目标队比赛。以上逐条保留原因，不能称全量采集完成。ESPN 年查询有截断/历史覆盖不全的可能，本次没有取得官方赛季应有总场次对账，页面不得显示“全部历史已齐”。

| 球队 | ESPN ID | 纳入总数 | 近 24 个月 | 最新完场 | 更早的主要样本 |
|---|---|---:|---:|---|---|
| 浦项制铁 | 9693 | 22 | 7 | 2026-10-28 | 2016、2023、2024 |
| 大阪钢巴 | 7102 | 15 | 3 | 2026-10-28 | 2016、2017 |
| 布里兰 | 7138 | 37 | 16 | 2026-10-28 | 2016、2019、2023–2025 |
| 鹿岛鹿角 | 7115 | 9 | 3 | 2026-10-28 | 2019 |

近 24 个月按 2024-11-04 至本次采集计算。既有方法有时间衰减，陈旧样本对当前参数的实际贡献会进一步减小。本适配器未修改最低场数或衰减规则。

## 两场当前比赛

ESPN scoreboard 与 summary 身份、主客队及开球时间核验：

| 比赛 | ESPN 比赛 ID | 北京时间 | 阶段 | 中立场 |
|---|---|---|---|---|
| 浦项制铁—大阪钢巴 | 401912614 | 2026-11-04 18:00 | 2026 联赛阶段 | 明确 false |
| 布里兰—鹿岛鹿角 | 401912613 | 2026-11-04 20:15 | 2026 联赛阶段 | 明确 false |

对应 [ESPN 浦项—大阪详情](https://site.api.espn.com/apis/site/v2/sports/soccer/afc.champions/summary?event=401912614) 和 [布里兰—鹿岛详情](https://site.api.espn.com/apis/site/v2/sports/soccer/afc.champions/summary?event=401912613)。业务体彩 ID 关联必须由主链路再核验，不能只凭上表队名建立映射。

## 发现并修正的真实来源陷阱

1. Scoreboard 不给 `neutralSite`，不能默认 false。新增 summary 校验，要求同赛事 UID、同比赛 ID、同赛季、同开球、同主客队、同比分。
2. 即使 summary 明示 false，也不能忽略赛制。ESPN 2021/2022 的集中赛会制比赛仍可返回 false。AFC 明确 [2021 小组赛采用集中场地](https://www.the-afc.com/en/club/afc_champions_league/news/afc_club_competitions%E2%80%99_group_stages_to_be_played_in_centralised_venues.html)，[2022 官方媒体指南](https://assets.the-afc.com/ACL_2022/Downloads/AFC-Champions-League%E2%84%A2-2022-Official-Media-Guide.pdf)同样标明集中小组赛。因此当前策略隔离 2020–2022 全部样本；2020 可能包含正常主客场比赛，但没有逐场证明之前不恢复。
3. 当前规则不能照搬旧 24 队版本。[2026/27 AFC 官方规程](https://assets.the-afc.com/downloads/tournament-regulations/Amended-ACL-Elite-26-27-Competition-Regulations-Apr28upd.pdf)第 8 条规定 32 队分东西两区，每队四主四客；第 9 条区分两回合十六强和集中决赛阶段。联赛排名极端同分情形还可用点球决胜。保留 90 分钟、点球及晋级边界，不能把点球获胜当常规主胜。[J 联赛官方赛事介绍](https://www.jleague.jp/en/outline/acle/)可交叉核查。
4. Summary 常规完场没有 period 字段；要求它的明确 FULL_TIME + completed，并依赖 scoreboard 的 period=2 / regulation.periods=2，不能凭缺失 period 推断或把全部有效数据误拒绝。

## 产物与使用边界

- 适配器：`worldcup_mvp/console/espn_history.py`。`parse_event` 做逐场验证；`prepare` 做去重、冲突检测与样本视图；`fetch_receipt` 保存成功、成功空响应、HTTP/传输失败和不可变原始响应；`prepare_cached` 只生成候选文件，不导入或选用。
- 本次真实原始数据：`.ai/tmp/acl-history/`。候选：`research-afc.champions.json`。源文件路径、完整 SHA256 与落盘时间保存在 manifest.source_receipts；这批研究下载的时间为文件落盘证据，不伪称原请求 HTTP 日志。后续适配器直接 fetch 可保留请求状态。
- 半场比分、历史盘口、完整人员、xG 均未由本适配器取得。半场保持 null，不能生成半全场能力声明；没有历史盘口不能给策略收益率。
- 历史可用时间沿用已明确的“实际采集或赛后 7 天较早值”模拟政策；它不是当年真实观测时间。保留 `availability_basis`，评估须标历史模拟。
- 权利状态 unverified、commercial_use_permitted=false；仅依据用户本轮批准的本地研究使用，不宣称商业授权。
- 研究适配器没有改动生产注册表、旧归档、日韩模型或选用指针。整体联调、实际训练报告、选择与赛前归档由主任务单独验收。

复现：

```powershell
.venv/Scripts/python.exe -X utf8 -m worldcup_mvp.console.espn_history --cache .ai/tmp/acl-history --output .ai/tmp/acl-history/research-afc.champions.json --request-reference "User approved v2 T01 T10 local research 2026-11-04"
.venv/Scripts/python.exe -X utf8 -m unittest tests.test_espn_history -v
```

6 项测试通过：赛季与可用时间、身份/中立场/比分/90分钟/阶段/时间拒绝、精确重复与冲突重复、失败回执、集中赛制覆盖供应商默认值、summary 缺 period 与 scoreboard 缺 period 的差异。
