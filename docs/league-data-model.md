# 日职与韩职：当前实现和验收边界

更新：2026-10-31。用户选择：现有公开数据先运行，付费数据源预留配置。

## 当前运行链路

- `dashboard.py --console-only` 启动每60秒体彩场次采集，浏览器关闭后继续工作。停止服务器后采集也停止；尚未注册系统开机服务。
- 完整体彩五种玩法和报价变更保存在 SQLite。单场详情、预测输入、情报来源另行留档。
- 实际采集时间与上游报价变化时间分开。180秒核验时效用于阻止过期数据生成预测，不代表上游端到端延迟保证。
- 历史接口的 `s-1sh/s-1sd/s-1sa` 转成统一比分桶。较旧、部分或未来时间的历史记录不能覆盖当前完整报价。
- 暂停/关闭玩法不进入新预测。每次预测前强制核验场次与报价，开赛后禁止补写赛前记录。
- ESPN 基本面必须匹配赛事、两队及开赛时间，详情再次检查比赛和球队 ID。已核验日职柏太阳神 KAH/KRE 跨源别名。
- 韩职不再重复请求已失败的 ESPN 路径，保留明确的结构化基本面缺口。官方俱乐部消息可通过“阵容情报”人工录入，事实与观点分开、保留来源和首次录入时间，不作为训练标签。
- 第一版生产输出仍是市场基线；未经验证的固定80%/20%权重已停用。近期赛果是事实对照，不冒充校准后的统计模型。
- AI 解读仅选择已保存的依据，显示文本由确定性模板生成；不允许模型自由补充首发、伤停、球员位置或新概率。尚未提供经过验证的战术推理模型。
- 情报发布时间支持 `published_precision: date/datetime`，原始来源只有日期时不展示虚构的小时。首次录入时间独立保存，防止事后情报混入赛前输入。

## 即时比分

- 新增“实时比分”页；比赛列表、详情顶部及复盘页显示独立比分组件。复盘的“实际比分”仍只用于体彩官方结算，不将正在进行的比分计为命中。
- 韩职取 K League 官网公开赛程接口 `POST /getScheduleList.do`；日职及已映射联赛取 ESPN scoreboard。逐场核对联赛、主客队身份、开球时间（最多15分钟差异），不按单支球队猜测匹配。
- 比分后台独立每30秒轮询，按联赛/日期批量请求，采集开球前后24小时内的已收录比赛。公共源无秒级延迟保证；“核验时间”是成功接收时间，不是上游事件发布时间。
- 显示源返回的比赛阶段及分钟，不用本地时钟虚构计时；韩职分钟按官网半场口径显示。未知比分为“—”，未开球不假设0–0。
- 断线保留上次比分及接收时间并标记延迟。未结束的比分90秒未成功核验即过期；结束比分保留最后状态。变化及VAR式回调均追加留档，比分源不参与赛前训练标签或官方结算。
- `GET /api/v1/scores` 读取缓存，`?refresh=1`触发批量核验（最短间隔10秒）。`live_scores`保存当前状态，`live_score_snapshots`保存变化；付费推送源尚未接入。

## 赛事配置

`worldcup_mvp/console/catalog.py` 管理稳定赛事 ID、地区、国家、供应商映射、赛季和阶段。

- `jp.j1`：2026 百年构想特别赛事与2026/27常规赛季分开。
- `kr.k1`：2026支持常规、Final A、Final B。缺少官方阶段映射时保持未知，不根据日期或体彩的组内排名猜测阶段。
- 日期区间是赛季归属辅助；有官方赛事 ID 时应继续升级为赛事 ID 映射，不能将特别赛事点球胜负当90分钟胜负。
- 前台赛事范围由体彩当前开售清单决定。历史训练样本必须包含目标联赛全部比赛，不能只用竞彩选中的场次。

## 历史数据与离线评估

本轮没有采购或导入商业历史数据，也没有真实日职/韩职训练成绩。

数据放置：`data/training/club_history.json`（Git 忽略）。结构如下，仅为字段示意：

```json
{
  "manifest": {
    "training_permitted": true,
    "license_reference": "实际授权合同或条款记录",
    "source_url": "https://provider.example/dataset"
  },
  "matches": [{
    "id": "provider:actual-id",
    "competition_id": "jp.j1",
    "season": "2025",
    "stage": "regular",
    "home_id": "provider:home",
    "away_id": "provider:away",
    "kickoff": "2025-01-01T10:00:00Z",
    "result_available_at": "2025-01-01T12:30:00Z",
    "period": "regular_time",
    "home_goals": 1,
    "away_goals": 1,
    "source_url": "https://provider.example/actual-result"
  }]
}
```

可选赔率：`odds: {h,d,a}`、`odds_observed_at`、`odds_source_url`。必须是实际历史记录，不允许填收盘值或伪造历史采集时间。来源声明需要人工核实授权，软件无法代替合同审查。

执行：

```powershell
.venv\Scripts\python.exe -m worldcup_mvp.console.training
.venv\Scripts\python.exe -m worldcup_mvp.console.training --evaluate
```

也可在“数据与模型 → 训练与评估”执行。当前比较独立联赛、合并、联赛参数收缩三种正则化攻防泊松模型；半衰期在验证集选择。

约束：每个联赛至少3个赛季、300场，时间切分后的验证和测试每联赛至少30场；这些是工具最低运行条件，不是统计显著性保证。按唯一比赛日期60/20/20切分，最后20%不参与调参。按周向前更新，只用周初之前已公布的结果。预测时点为赛前60分钟；同一场不拆分到不同数据集。

报告包括 Log loss、Brier、校准分箱、分联赛/阶段结果、同样本市场对照和逐场预测。不包含收益承诺。赔率缺失或在预测时点过旧的场次不进入市场比较。报告保存在 `data/training/club_evaluation.json`。

评估不会自动切换生产模型。上线前仍需独立前瞻样本、误差区间、漂移检查、逐玩法验证及人工发布；尚未实现训练模型的生产发布/回退和半全场联合模型。

## 后续接入前的验收

- `API_FOOTBALL_KEY`：基本面候选。逐赛季检查阵容、伤停、统计字段，不能作为分钟级赛前赔率唯一来源。
- `ODDS_API_KEY`：外盘及历史候选。确认具体公司、玩法、样本覆盖、存储与展示/训练权限。
- `KLEAGUE_API_KEY`：官方结构化记录候选，须核验授权和历史覆盖。
- 配置页只报告是否配置，不回传密钥，不自动购买、订阅或宣称已经接入。
- 连续多比赛日测量失败率、漏更新、暂停恢复和可测延迟。公开接口没有可验证的上游发布时刻时，只能报告接收/核验时间。
- 尚未提供外盘自动采集、多供应商故障切换、自动情报抓取、全部赛季历史库及商业发布能力。

## 核验资料

- J.League DataSite：https://data.j-league.or.jp/SFTP01/
- J.League 赛事定义：https://aboutj.jleague.jp/corporate/terminology/
- K League 规则：https://www.kleague.com/about/competition.do
- K League API：https://api.kleague.com/docs/api_match_player_record.jsp
- The Odds API 历史：https://the-odds-api.com/historical-odds-data/
- The Odds API 频率：https://the-odds-api.com/sports-odds-data/update-intervals.html
- API-Football：https://www.api-football.com/coverage
- Opta 方法：https://theanalyst.com/articles/opta-football-predictions
- 时间验证研究：https://arxiv.org/abs/1807.01623

两场实际赛前分析在本地 SQLite 的版本记录中；`data/console/*-pregame.json`为导出的真实输入及输出，不是页面示例数据。
