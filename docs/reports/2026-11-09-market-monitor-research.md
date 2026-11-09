# 亚洲盘观察覆盖和阈值研究闭环

实现：`observation_market_research.py`，复现：`scripts/research_observation_market.py`。仅输出研究候选，不修改生产模型、体彩让球或任何预测归档。

## 可复现的数据口径

- 按比赛、公司、市场ID、比赛时段和盘口线隔离；不同公司、不同线不拼成降水。
- 真实抓取时间晚于研究截止的数据不可用。两次来源确认递增且本地间隔不超过180秒才贡献观察覆盖；重复缓存、停盘、陈旧、缺字段和超长断档不贡献覆盖。
- 多公司/多线覆盖区间取并集，得到fixture-seconds；同场不会因为公司多重复扩大分母。按24h–6h、6h–3h、3h–90m、90m–30m、30m–5m、5m–0六个赛前窗口裁剪，事件率=单边价格变化数量/实际观察小时。窗口宽度不是观察时长。
- 覆盖仅为有连续采样确认的区间，不声称掌握中间每笔tick。两个采样之间的反转时刻表示为区间，连续维持时间仍未知。输出幅度、方向、相对上次变化是否反转、已采样确认的持续下界。
- 断档或终止观察前未到标签时点，标为censored，不补成成功或失败。

## 训练与验证

目标明确为“价格变化在其后300秒内的采样确认中保持该方向”，不是球队赢球、投注收益或诱盘真实性。要验证足球预测增益必须另做赛果实验，不能直接沿用这里的指标。

固定协议网格0.03/0.05/0.10/0.15/0.20十进制赔率幅度，只在训练集挑阈值；至少30场训练、20场验证和10场入选。每场只取第一个合格降水事件，避免双边、多公司和反复采样膨胀样本量。按完整比赛分时间段，训练标签必须在训练截止前，验证起点必须在训练截止后，切分边界场排除。

验证阶段只评价冻结阈值，不重新选优。要求入选事件持续比例相对同阶段全部合格事件至少增加5个百分点，且入选比例Wilson下界超过基线Wilson上界；不足/无增益均保持停用。即使通过也只是`validated_monitoring_candidate`，`production_enabled=false`。`apply_rule`只允许该联赛、验证期之后的事件生成观察标记。

报告含协议、数据哈希、时点、训练候选、所选阈值、验证指标、场次ID、排除原因和内容哈希版本；文件按联赛与哈希存储，禁止覆盖或写选用指针。数据与协议相同的重放应逐字一致。

真实复现命令：

```powershell
.venv/Scripts/python.exe scripts/research_observation_market.py --db data/console/football.sqlite3 --train-end 2026-11-08T00:00:00Z --validation-end 2026-11-09T12:18:49Z --output data/console/market_research
```

本次五联赛快照版本29/28/26/27/26个，已确认亚洲盘口覆盖秒数和事件均0。五份不可变报告均为`insufficient_training`，阈值null、事件率null，没有任何规则被启用。汇总回执 `.ai/tmp/free-ah-probes/research_summary.json`。这证明空数据流程能完整执行、正确拒绝启用，不代表已经完成真实市场规律验证。

## 免费源复核与第二适配器

2026-11-09 12:18:49Z再次请求MoviOdds group938，五联赛仍有96场赛程、该组报价0；全平台258条该组报价。原始receipt：`.ai/tmp/free-ah-probes/latest_938.json`。

继续核对[InferSports官方OpenAPI](https://raw.githubusercontent.com/infersports-dev/infersports/main/openapi.json)，使用metadata返回的真实联赛ID逐联赛请求：英超ENPL/`lg_T9268DXK`，西甲SPA/`lg_PYBWSDEA`，德甲GER/`lg_PGYWSDEA`，意甲ITA/`lg_QXCSBRME`，法甲FRA/`lg_CNSP3BPA`。分别有20/20/18/20/18场未来赛程。每联赛抽第一场赔率，均取得真实双边亚盘（英11、西14、意10、法7条，德甲也含多条）。这修正了此前只抽伊拉克/女足空盘所能支持的范围；此前空盘不能代表该源五联赛无盘。

具体：曼城–富勒姆Crown -1.75，2.47/1.56；毕尔巴鄂–西班牙人Crown -1.5，2.35/1.62；拜仁–科隆Crown -3.25，2.35/1.62；拉齐奥–莱切Crown -1.75，2.29/1.65；特鲁瓦–勒阿弗尔Crown -0.75，2.38/1.60。这里是来源样本，绝非推荐。Quarterline附有handicap_components，币制decimal、双边home/away、状态open和逐条as_of。

新增`fetch_infer_odds(event_id)`、`adapt_infer(event,packet,fixtures,...)`，沿用严格联赛、双方、UTC时间唯一绑定，所有替代盘口分别存market_id，避免把同一次响应内的多条线误判为盘口跳变。

关键限制：本次响应顶层stale=false，但逐条as_of多为数十分钟前甚至数小时前；其文档没有明确证明as_of是最后变价还是最后轮询，因此不会伪装成最近核验。适配器严格拒绝超过180秒的逐条as_of；保留source_reported_at，不写source_checked_at，不把它加入已确认来源轮询覆盖分母。若要观测长期稳定价格，需要上游明确新鲜度语义或提供新的轮询确认字段，不能简单放宽超时冒认可用。

回执：`infer_five_league_probe.json`、`infer_germany_events.json`、`infer_germany_odds.json`及其余四联赛原始响应，均在`.ai/tmp/free-ah-probes/`。本次没有注册、付费或绕过鉴权。

## 验证

研究测试覆盖采样断档与缓存去重、同公司同线隔离、停盘、截止后数据、反转区间、标签删失、训练/验证隔离、验证不能选阈值、跨联赛排除、场次去重、版本哈希和拒绝篡改、未验证停用。第二适配器测试真实schema、quarterline拆分、身份与陈旧时间。合成单测验证分支逻辑，不计入任何真实准确率或市场样本量。

## 已接入后台运行

`ObservationRuntime.sync_handicap`启动独立后台线程，`observation_handicap_collector.HandicapCollector`执行双源采集与研究刷新；没有改为在GET或打开页面时采集，也不阻塞原有赛前预测队列。

持久化表`observation_handicap_cache`记录成功和失败的采集尝试，`observation_handicap_receipts`以内容哈希保存原始响应，陈旧或不匹配记录也保留证据。数据库原子领取一分钟采集周期，重启不会绕过限频。每周期InferSports只发现一个联赛（各联赛15分钟缓存，轮转）、最多请求3场盘口，每场至少5分钟；优先从未采集/最久未采集场次，再按开赛时间。所有请求都有超时及响应大小限制。MoviOdds只请求group938。

研究任务每15分钟从数据库真实报价刷新一次，按联赛生成不可变阈值报告；接口读取`market-research`状态缓存，不运行拟合。状态键：`asian-handicap`（MoviOdds）、`asian-handicap:infersports`（精确匹配/未匹配数、本轮请求/拒绝数、累计拒绝记录和回执哈希）、`market-research`（asof及五联赛reports）。不存在的coverage不会填成100%。

2026-11-09 12:34:53Z实际运行：首轮英超发现20场，严格匹配8场，未匹配12场；请求3场真实盘口，33条因来源时间陈旧而拒绝，保存3份盘口原始回执。MoviOdds本轮有效绑定0。五联赛研究报告均成功刷新，真实覆盖0、样本不足，规则未启用。下一周期继续轮转其余联赛，不通过手动绕过频率限制刷接口。

专属运行测试覆盖每周期最多3场、持久缓存跨实例重启、原始回执留存、陈旧报价不入库、研究报告自动生成、runtime只启动一个后台线程。
