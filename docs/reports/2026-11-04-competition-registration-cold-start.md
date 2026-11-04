# 2026-11-04 新赛事登记与冷启动实验

## 已核实的来源与赛事边界

| 赛事 | canonical ID | 体彩代码 | ESPN slug / league ID | 今日核验 |
|---|---|---|---|---|
| 欧冠 | uefa.ucl | UCL | uefa.champions / 775 | 8场 |
| 欧联 | uefa.uel | UEC | uefa.europa / 776 | 1场 |
| 英冠 | eng.championship | ED1 | eng.2 / 3914 | 1场 |

真实欧联代码是 UEC，原 UEL 兼容运输映射不等于已登记赛事。精确缩写补充：英冠 BRT→BRC、WLV→WOL；欧联 OLP→OLY。来源板与详情必须保持同赛事ID、比赛ID、主客球队ID、赛季、精确开球时刻。实际10场逐场证据位于 `.ai/tmp/new-competitions-identity/verified-fixtures.json`。未把中文名称模糊匹配作为身份。

`league_for` 优先读当前赛事登记，基本面、比分和同主客赛程共用这一映射；登记赛事不再要求修改运输代码常量。比分板必须匹配登记的 ESPN league ID，每个事件UID同时绑定该赛事。注册仍不代表有历史数据或模型。

## 规则来源及范围

- [UEFA欧冠2026/27 Article17](https://documents.uefa.com/r/Regulations-of-the-UEFA-Champions-League-2026/27/Article-17-Match-system-league-phase-Online) 和 [欧联Article17](https://documents.uefa.com/r/Regulations-of-the-UEFA-Europa-League-2026/27/Article-17-Match-system-league-phase-Online)：当前league phase；旧group与新league阶段在数据中保留不同rules era，不能称同赛制已证明可迁移。
- [欧冠2026/27 Article15](https://documents.uefa.com/r/Regulations-of-the-UEFA-Champions-League-2026/27/Article-15-Match-system-qualifying-phase-and-play-offs-Online)：资格赛属同一欧冠赛事，但两回合晋级不是单场90分钟标签。只显式纳入2026、来源`uefa.champions_qual`/19874的80个已验证常规完场、非中立回合；保存`qualifying:`阶段前缀和leg。加时、点球或未知口径仍隔离。预测政策仍只允许league-phase。
- 英冠只启用`regular-season`。已取得[EFL官方2025/26手册](https://images.gc.eflservices.co.uk/6a32d710-5b0b-11f0-ad0f-97502a343283.pdf)，其关于随IFAB规则更新的约定及[IFAB现行Law7](https://www.theifab.com/laws/latest/the-duration-of-the-match/)支持常规90分钟口径。2026/27完整EFL手册未取得，**不宣称核实该季附加赛制度，附加赛未启用**。当前源明确阶段、period=2、完场且非中立才入训。
- 未合入Betis2005旧赛季来凑5场。Betis、Como均为当前冻结数据中3场有效欧冠历史；Viking5、Sabah10、Fenerbahce9来自显式2026资格赛补充。

规则通过 `competition_rules.save` 留存不可变修订，不覆盖既有观察模型、数据版本、预测归档。新导入保留 source_competitions、qualifying_enabled、采集状态及隔离记录。

## 独立冷启动协议 gamma_cold_start_v1

默认既有模型仍要求双方至少5场，不改变任何旧run。新任务必须显式提交 `kind=experiment, algorithm=dynamic_gamma, half_life=180, cold_start_policy=gamma_cold_start_v1`。该任务固定一个算法和超参数，不执行标准9配置选择。

在本赛事每个周起点冻结历史，仅使用当时结果已可用且开球更早的样本；每折达到300场、3赛季后，评估当前适用预测阶段中的全部目标。UCL仅league-phase目标，不拿资格赛冷样本冒充主阶段验证。双方历史数较小值为0–4定义冷组，分为0、1–2、3–4。每个同场对照为该折同赛事历史得到的联赛进球均值先验，使用相同目标及结果。

准入条件预声明：冷组整体、要准入的各分层、常规5+组都至少30场，log loss与Brier都不劣于同场先验。任何不通过分层仍阻断。失败保存可核查报告，不发布冷启动候选。成功只生成新候选，不自动选择。新产物绑定不可变报告和数据摘要，证据缺失或损坏不能加载。

冷启动推断只用精确来源ID，无其他赛事实力、姓名猜测或大模型。归档保留真实样本数、政策/证据标识及Gamma参数90%区间。这个区间是预期进球率的不确定性，不是比分区间或命中置信度。

## 实际719场欧冠实验

- 任务：`493bbf6688844620b00d30d678395e11`
- 数据：`0f0f51100e95bdd91cc4613a1c62c9187d08d0888c324f4508b3af4b3ec1d1da`
- 报告：`1399862b0e424722934011136fbf8e67`
- 候选：`f36b60526011453e9ef3f593066ba1a9`

| 同场分组 | 样本 | Gamma log loss / Brier | 联赛先验 log loss / Brier | 准入 |
|---|---:|---|---|---|
| 冷组整体 | 84 | 0.96338 / 0.57008 | 1.04256 / 0.62948 | 通过 |
| 0场 | 9 | 1.16886 / 0.72219 | 1.20876 / 0.74752 | 样本不足，阻断 |
| 1–2场 | 39 | 0.94131 / 0.55242 | 1.01238 / 0.60773 | 通过 |
| 3–4场 | 36 | 0.93592 / 0.55120 | 1.03370 / 0.62353 | 通过 |
| 常规5+ | 249 | 0.92180 / 0.54296 | 0.99742 / 0.59898 | 通过 |

84冷样本分布于2024/2025/2026，分别30/35/19。历史结果已经参与本项目探索，不能称为未查看过的独立留出测试；上述只支持本赛事该阶段的有限前瞻观察。最终固定产物无前瞻成绩、没有盈利结论，0场新队仍未获准入。规则和数据齐全不意味着每个新赛事或球队自动有充分模型证据。

验证：57项任务/冷启动/身份/比分/预测回归通过。实际逐折重放结果及两场3历史球队的无写入推断检查见 `.ai/tmp/cold-start-ucl/replay.json`；真实任务原始结果见 `.ai/tmp/cold-start-ucl/result.json`。选用和新预测由主执行流程单独处理。
