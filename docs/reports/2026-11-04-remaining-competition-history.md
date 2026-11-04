# 欧冠、欧联、英冠真实历史补齐

2026-11-04。范围：当日剩余 10 场对应的三个赛事。采集完成，下面是可审查的研究数据；本任务未自行导入、训练或选用。

## 采集结果

| 赛事 | ESPN 命名空间 / ID | 请求日历年 | 纳入样本 | 事件赛季 | 隔离 |
|---|---|---|---:|---|---:|
| 欧冠正赛 | uefa.champions / 775 | 2021–2026 | 639 | 2021–2026 | 251 |
| 欧冠资格赛，独立研究 | uefa.champions_qual / 19874 | 2026 | 80 | 2026 | 10 |
| 欧联 | uefa.europa / 776 | 2021–2026 | 630 | 2021–2026 | 340 |
| 英冠 | eng.2 / 3914 | 2024–2026 | 1,521 | 2023–2026 | 138 |

请求范围内的全部适用常规完场都拉取了 summary 核验：639 + 80 + 630 + 1,521 = 2,870 场，无请求失败。原始响应和实际采集时间留在各目录 raw 中，候选 manifest 逐条保存 URL、文件路径和 SHA256。已预先下载的年表标为文件落盘时间证据，没有补造 HTTP 状态。

“请求范围完成”不代表每个历史赛季完整：英冠 2023 赛季在这批数据中只有 2024 日历年的 252 场，2026 赛季仍在进行；本次没有对所有来源年份取得独立官方场次数对账。不得以这批结果宣称全球比赛资料全部齐全。

| 正赛数据 | 各赛季纳入 |
|---|---|
| 欧冠 | 2021：96；2022：96；2023：96；2024：144；2025：144；2026：63 |
| 欧联 | 2021：96；2022：96；2023：96；2024：144；2025：144；2026：54 |
| 英冠 | 2023：252；2024：552；2025：552；2026：165 |

## 当日目标球队

| 比赛 | 主队本赛事样本 | 客队本赛事样本 | 尚需处理 |
|---|---:|---:|---|
| 费内巴切—利物浦 | 9（正赛3＋资格赛6） | 31 | 资格赛政策及分阶段评估 |
| 莱比锡—曼城 | 29 | 37 | 训练、评估及选用 |
| 波尔图—那不勒斯 | 21 | 23 | 训练、评估及选用 |
| 埃因霍温—布鲁日 | 25 | 31 | 训练、评估及选用 |
| 多特蒙德—贝蒂斯 | 37 | 3 | 贝蒂斯不足既有5场门槛 |
| 维京—萨巴赫 | 5（正赛3＋资格赛2） | 10（正赛3＋资格赛7） | 资格赛政策及分阶段评估 |
| 朗斯—科莫 | 9 | 3 | 科莫不足既有5场门槛 |
| 布拉格斯拉维亚—阿森纳 | 11 | 25 | 训练、评估及选用 |
| 雷恩—奥林匹亚科斯 | 15 | 29 | 训练、评估及选用 |
| 布里斯托尔城—狼队 | 126 | 13 | 训练、评估及选用 |

以上是数据样本数，不是成功预测或模型有效性的声明。所有模型仍需按自己的时间切分检查训练期真实可用样本，不能拿完整数据集的场数代替每个回测折的场数。

额外核查了贝蒂斯 2005 年欧冠：找到 6 场小组赛，4 场通过当前严格 90 分钟证据检查，2 场不能证明常规时间口径。该组比赛距今 21 年，未并入训练候选，不为凑最低样本数而忽略时效。科莫在本次 2026 欧冠资格赛中没有比赛，不能借意甲或欧联结果冒充欧冠历史。

## 阶段、规则和隔离

欧冠、欧联正赛仅收 `group-stage`（截至 2023 赛季）和 `league-phase`（从 2024 赛季起）；英冠只收 `regular-season`，排除升级附加赛。UEFA [新赛制官方说明](https://www.uefa.com/uefachampionsleague/news/0268-12157d69ce2d-9f011c70f6fa-1000--champions-league-format-explained/)及[2026/27 第17条](https://documents.uefa.com/r/Regulations-of-the-UEFA-Champions-League-2026/27/Article-17-Match-system-league-phase-Online?contentId=z80npkZwtB5MkmwVqnk6XA)说明联赛阶段的36队及八个对手；[UEFA 欧战新赛制说明](https://www.uefa.com/news-media/news/0268-12157d69ce2d-9f011c70f6fa-1000--how-clubs-qualify-for-europe/)同时区分欧联和欧协的比赛数。不能将它们合为一个欧洲比赛训练集。英冠参考 [EFL 官方赛事页](https://www.efl.com/competitions/efl-championship)，常规赛和升级附加赛分别保留。

欧冠资格赛是同一 UEFA 比赛的独立阶段，但 ESPN 使用单独来源 ID。依据[UEFA 2026/27 资格赛官方说明](https://www.uefa.com/uefachampionsleague/news/02a6-20e5a8be4e63-ae971c582f8c-1000--champions-league-qualifying-results-how-it-worked/)，仅制作显式启用的 2026 资格赛候选，不自动推广到其他年份或另一赛事。

2026 资格赛逐场核验了 scoreboard 与 summary 的赛事 UID、开球、双方球队、赛季、90分钟比分、非中立标记和第1/2回合一致性。纳入第一轮27、第二轮24、第三轮18、附加轮11；10场加时、点球或无法确认为普通完场的记录隔离。萨巴赫附加赛次回合为加时赛，未取最终比分冒充90分钟。

合并候选保留 `qualifying:first-round`、`qualifying:second-round`、`qualifying:third-round`、`qualifying:playoff-round`，以及来源阶段、回合、`rules_era`。这些样本的对手强弱和两回合比赛动机有差异，应分阶段报告覆盖与误差；本次没有证明混入资格赛能提高正赛预测效果。

## 产物与复现

- `worldcup_mvp/console/espn_competition_history.py`：独立新适配器，不修改亚冠行为。
- `scripts/collect_espn_competitions.py`：有界并发研究采集，最多8个工作线程/调用；本次正赛每源4个，资格赛3个。
- `.ai/tmp/remaining-coverage/uefa.champions/research.json`：639场原正赛候选，保持原文件。
- `.ai/tmp/remaining-coverage/uefa.champions/research-main-plus-2026-qualifying.json`：719场新合并候选，原正赛和资格赛文件摘要、全部来源回执均保留；没有加入2005年样本。
- `.ai/tmp/remaining-coverage/uefa.europa/research.json`、`eng.2/research.json`：其余两赛事候选。

运行时接口为 `collect_competition(competition_id, years, directory, progress=None, fetcher=None, allow_qualifying=False, qualifying_years=None)`。资格赛必须明确指定年份且属于主请求年份范围；没有该列表则拒绝。`fetcher(source_slug, endpoint, raw_dir)` 返回原始 JSON 和回执，便于统一任务服务复用经过核验的缓存。失败、空响应、身份/比分冲突、未来采集时间及达到1000条上限的潜在截断响应不会标成功。

新下载使用不可变请求尝试文件；成功缓存仅在摘要、URL、路径范围与时间有效时复用。当前年表最多180秒，其他响应24小时。未来不明数据不做默认值。

```powershell
.venv/Scripts/python.exe -X utf8 scripts/collect_espn_competitions.py --source uefa.champions --years 2021 2022 2023 2024 2025 2026 --workers 4
.venv/Scripts/python.exe -X utf8 scripts/collect_espn_competitions.py --source uefa.europa --years 2021 2022 2023 2024 2025 2026 --workers 4
.venv/Scripts/python.exe -X utf8 scripts/collect_espn_competitions.py --source eng.2 --years 2024 2025 2026 --workers 4
.venv/Scripts/python.exe -X utf8 -m unittest tests.test_espn_competition_history -v
```

5项专项测试通过：来源/赛事与赛制隔离、资格赛显式政策和两回合证据、运行时来源回执及空响应、不可变下载与损坏缓存、显式资格赛年份/未来时间/潜在截断阻断。

所有数据仍仅按用户批准的本地研究范围处理，商业授权状态未核实；没有本次适配器提供的半场、历史盘口、完整人员或 xG 输入。历史可用时间沿用明确标注的赛后7天/实际采集较早值假设，不伪称当年的实时观测记录。
