# 五大联赛：官方日期核查与统计补采

截至2026-11-05，本轮完成2场法甲xG关联及5场西甲官方比赛统计补采，并导入西甲、法甲各一个新数据版本。没有训练或选用模型；英超、德甲、意甲当前数据摘要保持原值。

## 实际覆盖

| 联赛 | 有效比赛 | 双方xG | xG缺口 |
|---|---:|---:|---:|
| 英超 | 1,990 | 1,990 | 0 |
| 西甲 | 2,009 | 2,006 | 3 |
| 德甲 | 1,601 | 1,600 | 1 |
| 意甲 | 2,000 | 1,996 | 4 |
| 法甲 | 1,758 | 1,753 | 5 |
| 合计 | 9,358 | 9,345 | 13 |

双方xG覆盖99.86%，只表示本批样本至少有一个已关联的赛后来源，不表示历史赛前可用、指标定义统一或质量问题全部消除。

全场/半场结果、双方首发、总射门、射正沿用此前9,358场覆盖。角球、控球率、犯规、总传球、黄牌从9,353场补至9,358场。红牌双方完整值仍为9,353场：新官方响应对多数零值省略字段，不能把缺键直接解释为零。

历史赛前伤停/资格核验、首发发布时间、多年带时点赔率仍未齐全，本轮没有增加这些资料的已核验覆盖。统计字段有值不等于已经纳入训练。

## 两场日期冲突的明确例外

| 本地比赛 | 原展示日期（时区未知） | 官方核查 | 补入的主/客xG |
|---|---|---|---|
| espn:401876423 图卢兹—特鲁瓦 | 2026-10-24 15:15 | 官方明确从周六改到10月25日17:15当地时间 | 0.52 / 0.34 |
| espn:401876422 巴黎圣日耳曼—里昂 | 2026-10-25 19:45 | 官方完场页面正文明确10月26日，比分2–0 | 1.03 / 0.47 |

证据分别为[图卢兹官方周末安排](https://www.toulousefc.com/play/blog/2026/10/le-programme-du-week-end-24-et-25-octobre-2026)与[巴黎官方比赛页](https://www.psg.fr/matchs/football-masculin/20262027/paris-vs-lyon-2026-10-25)。巴黎链接中的日期仍是25日；核验依据是正文，不是URL。

两份官方HTML均完整获取，保存HTTP状态、传输退出码、观察时间和SHA256。解析去掉脚本和样式后检查可见日期/比赛内容。再核对已冻结的PitchRadar原始文件及双方俱乐部历史：Understat比赛ID分别32010、32011，联赛、赛季、主客、全场比分和xG一致，同赛季同主客场次唯一。

这是只适用这两场的显式例外，不是通用日期容差。原始展示时间、未知时区和日期冲突保留；本地开球时间及旧预测不改。官方日期证据只核实比赛身份，不独立证明xG准确度或上游采集时间。

## 五场西甲官方统计

| 本地比赛 | 比赛 | 官方来源 |
|---|---|---|
| espn:643979 | 阿尔梅里亚—贝蒂斯，2023-02-11 | [阿尔梅里亚官网](https://www.udalmeriasad.com/partidos/temporada-2022-2023-laliga-santander-21-ud-almeria-vs-real-betis) |
| espn:643973 | 塞维利亚—马略卡，2023-02-11 | [马略卡官网](https://www.rcdmallorca.es/partidos/temporada-2022-2023-laliga-santander-21-sevilla-fc-vs-rcd-mallorca) |
| espn:643974 | 瓦伦西亚—毕尔巴鄂，2023-02-11 | [阿尔梅里亚官网联赛比赛页](https://www.udalmeriasad.com/partidos/temporada-2022-2023-laliga-santander-21-valencia-cf-vs-athletic-club) |
| espn:643976 | 西班牙人—皇家社会，2023-02-13 | [阿尔梅里亚官网联赛比赛页](https://www.udalmeriasad.com/partidos/temporada-2022-2023-laliga-santander-21-rcd-espanyol-de-barcelona-vs-real-sociedad) |
| espn:643971 | 皇家马德里—埃尔切，2023-02-15 | [阿尔梅里亚官网联赛比赛页](https://www.udalmeriasad.com/partidos/temporada-2022-2023-laliga-santander-21-real-madrid-vs-elche-cf) |

阿尔梅里亚官方页面的联赛第21轮结构化赛程包含其他球队比赛的真实slug；按这些标识取得对应比赛页。所有响应完整，按官方赛事/赛季/比赛/球队ID、主客方向、精确UTC开球、全场结果及已返回半场统计核验。

新增`additional_postmatch_statistics.club_official_llt`，与原ESPN及Understat资料分开。保存原团队统计、显式字段映射、来源回执、身份映射及缺失语义，`approved_as_training_feature=false`。这是官网展示数据，不宣称已取得某供应商商业直连或统一指标定义。

两项需要保留的质量信息：

- 阿尔梅里亚主队总射门：原Understat存档16次，官网17次。保存两个值与`diagnostic_only`差异记录，不凭“官方”标签覆盖原值，也不把它混成同一条训练特征。
- 西班牙人主队`totalRedCard=1`、`secondYellow=1`独立保留，不相加为两张红牌；其他缺失的红牌字段不补零。黄牌同样保持源口径，不用文字报道的名单人数覆盖统计值。

## 未采用的页面

TNT Sports返回HTTP 200，但正文是地区不可用提示，两场响应摘要相同；没有将其当作成功的比赛响应，也没有尝试绕过地区限制。TotalOdds样页及条款传输截断，条款可见部分限制商业使用及批量提取；没有继续扩采或将样页值入库。失败回执保留，公开页面不能替代完整传输和使用条件核查。

## 数据版本与验收

新版本：

- 西甲：`540a41879c3b313043f89d02858ee82cb2e70084c2627caeac7b147fd1e6df93`，2,009场。
- 法甲：`995d2a666bfd865ce66e740699e68e90b7bcf0890b2b655e0ee499c1e8f01dfc`，1,758场。

`scripts/reconcile_big_five.py --root data/research/big-five/2026-11-05`离线重放已冻结父快照与完整回执，生成新内容寻址研究快照。生产准入使用`big_five_dataset.prepare`、`validate_scope`及`model_registry.import_competition`，先验证两个赛事再分别导入，不替换其他三个赛事的数据指针。

93项测试通过，覆盖严格赛事/球队/时间/比分匹配、逐场日期例外、两侧历史核验、截断及篡改响应、隐藏脚本不能充当正文证据、缺失值不能补零、两黄变红不能重复计数、赛后增强字段不能自动启用，以及已有预测和结算回归。

导入后核对：1,280个既有训练目录JSON摘要未变；全部9,358场旧字段及既有xG来源事实未变；6个用户前端文件未改；英超/德甲/意甲的数据摘要未变。真实8891服务已读取西甲和法甲新摘要及正确场数，模型仍为空。没有发布前端或改写历史预测。

逐项证据位于`data/research/big-five/2026-11-05/reconciliation/`：`parents.json`、原始HTML及回执、`reconciliation-audit.json`、`feature-coverage.json`、`missing-xg.json`、`import-receipt.json`和`verification.json`。剩余13场的来源缺失、日期/主客冲突及指标定义问题继续保留，不按默认值凑齐。
