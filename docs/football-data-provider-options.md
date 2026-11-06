# 五大联赛免费与低价数据源

核查日期：2026-11-05。价格按官网原币种展示，税费、附加包和结账价格另计。本次没有开户、购买或订阅；有账户的接口尚未实测，不能把网页覆盖说明写成实际接入成功。

## 建议

先使用本次已保存的五大联赛基础研究数据完成 F0/F1 数据验收。若需要低成本持续补充首发、统计和伤停，优先测试 API-Football 免费账户的实际赛季权限和逐字段覆盖，再决定是否按月开通 $19 Pro；不要先买年费。

如果最关键的是连续历史 xG，不能仅凭“有 Statistics 接口”付款。应先让账户样本证明五个联赛、目标六个赛季、双方 xG 的实际覆盖。Sportmonks 的 xG 附加包是明确候选，但基础套餐价格不含全部附加项。完整多年赔率另算，不能指望足球 API 的七天赔率窗口回填五年。

## 可核查选项

| 来源 | 官网价格/额度 | 五大联赛与适用范围 | 对本项目的限制 |
|---|---|---|---|
| [Openfootball](https://github.com/openfootball/football.json) | 免费、公共领域数据 | 历史赛程、赛果，适合独立对照和补缺 | 不提供完整 xG、伤停、盘口；仍须核对比赛身份、时区、完场口径 |
| [football-data.org](https://www.football-data.org/pricing) | 免费：10 次/分钟；ML Pack Light €29/月，标明 10 个赛季历史 | [免费覆盖](https://www.football-data.org/coverage)包含五大联赛；免费适合基础赛程和比分对照 | 免费比分/赛程有延迟；不能把十年历史视为免费权限；€29 深度数据包和 €29 ML 包是不同方案，xG 没有在该价目表承诺 |
| [API-Football](https://www.api-football.com/?language=en) | 免费 100 次/天；Pro $19/月、7,500 次/天；Ultra $29/月、75,000 次/天 | 付费方案列出所有赛事和端点，含统计、首发、伤停、赔率；免费有赛季限制 | 所有端点可用不代表每项历史字段齐全；xG 必须验样。赛前赔率仅保留七天历史，赛中赔率没有长期归档 |
| [Sportmonks](https://www.sportmonks.com/football-api/) | Starter €29/月、任选 5 联赛、每实体每小时 2,000 次；官网 xG+Pressure 组合显示另加 €24/月；历史附加包 €29 起一次性 | 五大联赛可占满 Starter 的 5 个名额；明确有 xG 产品 | 基础+xG 组合按页面估算约 €53/月，另有历史包与税费；需核对实际结账及历史 xG 起始年；免费永久档只有丹麦和苏格兰，不含五大联赛 |
| [The Odds API](https://the-odds-api.com/) | 免费 500 credits/月；付费 $30/月起、20,000 credits | 专门补市场参考及历史时点报价；付费历史接口回溯至各运动实际加入覆盖的日期 | 免费无历史赔率；不是球队统计源；历史接口按地区和玩法计费，不能把 credits 当作调用次数 |
| [StatsBomb Open Data](https://github.com/hudl/open-data) | 指定比赛免费研究数据 | 射门和事件级研究，适合方法验证 | 不是五大联赛近五年完整样本；公开研究要求署名，不能默认商业再分发权限 |

Sportmonks 的基础包写有“全数据功能”，但同页仍单列 xG 和历史附加包；预算以上述附加项目为准，不按宣传语推断全含。超过最近三个赛季的历史需另核查权限，见[历史赛季说明](https://www.sportmonks.com/glossary/seasons/)；免费范围见[官方 FAQ](https://www.sportmonks.com/faq/)。

## 免费来源实测，而非只看宣传

最新进展：2026-11-05继续从[Pitch Radar的公开展示资料](https://pitchradar.live/en/how/)补入82场xG，覆盖现为9,343/9,358，剩余15场。343场同源存档对照通过；5条`bsd`备用来源记录全部隔离，不冒称Understat。已有球队与球员xG汇总差异另作诊断，不能直接混成已验收训练输入。详见[本轮覆盖与质量验收](reports/2026-11-05-big-five-depth-and-quality.md)。

[The Odds Gap](https://theoddsgap.com/data)说明有2026-09-15至21日免费样本，历史订阅页面标示首月$10、之后$25/月，档案起于2026年6月；这不是五年历史方案。本次样本下载截断，随后请求被拒绝，未得到可计入覆盖的赔率。该来源明确区分研究使用和重包装供数，未开户、付费或取得商业使用许可。

此前阶段：从[Ayush Pawar公开比赛存档](https://github.com/Spyyy004/iyush.dev/tree/3a6f1469364adc98ce3ed204bc9001f5f36030cc/football/gallery/match)补入658场明确标为Understat的xG展示值（两位小数），当时总覆盖9,238/9,358。逐场核对来源ID、精确开球、主客、比分；25场重叠样本符合舍入误差。该存档不是官方接口，发布时间与实际数据截止不同；不能承诺及时更新。没有导入该项目的娱乐评分、爆冷标签或赔率。详情见[第三阶段验收](reports/2026-11-05-big-five-gallery-and-refresh.md)。

后续补采更新：找到公开的 [pannadata Understat存档](https://github.com/peteowen1/pannadata/releases/tag/understat-latest)，校验发布资产SHA256后取得8,580场可关联xG，五联赛基础样本9,358场已分别入库。该存档冻结于2026-04-18，并非持续更新的官方Understat接口，不能覆盖全部当前赛季，也未核实商业许可。水晶宫官方延迟开球公告解决了下述15分钟冲突，FotMob关联数增至730；两源重叠不重复计数。详见[最新验收](reports/2026-11-05-big-five-supplement-and-import.md)。下面各项保留初次探测记录。

- ESPN：已取得五联赛历史结果及多数半场、首发和统计。本轮仅作本地研究；没有把公开可访问接口冒称有正式供数协议或稳定服务承诺。
- FotMob：本轮取得 730 场英超 xG 原始详情后遭遇 HTTP 403，已停止联网采集。严格身份、开球时间、比分一致检查后 729 场可关联；1 场开球时间相差 15 分钟，保留冲突。不能作为完整稳定免费供数。
- Understat：本轮联赛页面和数据端点返回维护页，未获得可入库资料。
- StatsBomb：直接读取当前官方仓库索引与比赛清单。在本轮 2021/22 及以后的范围内，德甲 2023/24 只有勒沃库森 34 场，法甲 2021/22 有巴黎 26 场、2022/23 有巴黎 32 场。把这些样本混成全联赛训练会引入明显的球队选择偏差。本次没有将其混入五联赛完整研究快照。[德甲清单](https://github.com/hudl/open-data/blob/master/data/matches/9/281.json)、[法甲 2021/22](https://github.com/hudl/open-data/blob/master/data/matches/7/108.json)、[法甲 2022/23](https://github.com/hudl/open-data/blob/master/data/matches/7/235.json)。
- [Football-Data.co.uk](https://football-data.co.uk/data.php) 与 football-data.org 是不同网站。前者当前说明限制把自动抓取数据用于训练产品等用途，本轮只探测样本，没有批量用于训练。

## 真实费用与采集量

本轮约 9,359 场记录。假设每场分别请求统计、首发、事件三类资料，约需 28,077 次请求，加赛程、分页、重试另算。100 次/天即使全部赛季有权限，也至少约 281 天；7,500 次/天的理论下限约 4 天。支持批量或 include 的接口会减少调用，实际以样本测量为准。免费额度适合验样，不能当成五年完整回填能力。

[API-Football 赔率文档](https://www.api-football.com/documentation_v3)说明赛前七天历史和赛中无历史保存；[Sportmonks Premium Odds 文档](https://docs.sportmonks.com/v3/endpoints-and-entities/endpoints/premium-odds-feed/premium-pre-match-odds/get-all-historical-odds)也只开放开球后七天内的历史变动。购买上述套餐不等于买到五年前的盘路。

[The Odds API 历史接口](https://the-odds-api.com/historical-odds-data/)提供时间快照，只有付费权限可用；各联赛、庄家、玩法起始时间不同。[计费文档](https://the-odds-api.com/liveapi/guides/v4/index.html#historical-odds)规定每次历史查询为 10 × 玩法数 × 地区数。若取胜平负/让分/大小球三类和一个地区，20,000 credits 约够 666 次查询；一场一个时点粗算五联赛 9,359 场要 280,770 credits。批量同开球组可复用，详细时点多则费用更高。

## 账户试验验收

每联赛每季预先抽 10 场，覆盖不同月份及球队，共 300 场，另加入本轮发现的异常比赛。保存权限错误与字段覆盖，核对双方统计、半场、xG、首发、历史伤停时间、报价时间和规则口径。样本通过后才扩到整季；不能因为接口 HTTP 200 就宣称数据齐全。

真实首发发布时间和伤停首次观测时间若供应商不保留，从接入当天开始积累。回填的赛后资料不能伪装成多年前赛前已知。购买数据也不会自动解决这一问题。
