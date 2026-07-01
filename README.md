# 世界杯综合分析控制台

无需注册、无需API密钥。默认从 FIFA、Polymarket 和中国体彩网三个匿名公开JSON API获取赛前数据，经统一数据中心交叉校验后输出90分钟胜平负分析。

## 公开数据来源

| 数据 | 接口 | 信任口径 |
|---|---|---|
| 赛程、球队、开赛时间、比分 | `api.fifa.com/api/v3/calendar/matches` | FIFA官方主数据 |
| 球员进球、球员ID、乌龙球 | `api.fifa.com/api/v3/timelines/{matchId}` | FIFA官方比赛时间线 |
| 欧赔参考（90分钟主胜/平/客胜） | `gamma-api.polymarket.com/events` | Polymarket官方公开Gamma API |
| 体彩开售状态、HAD与HHAD固定奖金SP | `webapi.sporttery.cn/gateway/uniform/football/getMatchCalculatorV1.qry?channel=c&poolCode=hhad,had` | 中国体彩网官方公开接口 |
| 淘汰赛规则 | [FIFA官方说明](https://www.fifa.com/es/tournaments/mens/worldcup/canadamexicousa2026/articles/en-caso-de-empate-en-la-copa-mundial-de-la-fifa-como-se-definen-los-partidos-de-eliminatorias) | 版本化权威配置 |

Polymarket的Gamma API和公开只读市场数据无需身份验证，参见[官方API说明](https://docs.polymarket.com/api-reference/introduction)。

## 运行

检查三个匿名公开数据源及FIFA的赛程/时间线端点：

```powershell
python main.py --doctor
```

分析当天开售且尚未开赛的世界杯比赛：

```powershell
python main.py
python main.py --json
```

指定赛事日期并保存可审计快照：

```powershell
python main.py --date 2026-06-29 --save-snapshot data\snapshots\public-2026-06-29.json
```

赛事日期按 `America/New_York` 划分，输出时间转为北京时间。可选配置见 [.env.example](.env.example)。

## 数据处理

1. 从FIFA官方接口取得目标日期的比赛。
2. 再读取本届赛事已经结束的小组赛，按官方比分自行计算每队场次、积分、进球和失球。
3. 根据FIFA官方比赛时间线聚合普通进球和乌龙球；球员事件数必须与官方球队总进球完全一致，否则停止。
4. 根据球队代码、比赛标题和开赛时间唯一匹配Polymarket事件，保留为欧赔参考。
5. 调用中国体彩网官方 `hhad,had` 接口，按业务日期、球队代码和北京时间唯一匹配，只保留 `Selling` 的可购买比赛。
6. 体彩HAD和HHAD分别保存，HHAD同时保存官方让球数及更新时间，不与Polymarket价格混算。
7. Polymarket三项Yes价格之和必须在0.90至1.10之间，随后归一化并转换为十进制赔率。
8. 任一步骤缺字段、冲突或无法唯一匹配都会报错，不读取旧JSON兜底。

## 已知边界

- 经实测的匿名公开接口没有可靠、结构化的世界杯伤停数据，因此当前快照明确记录 `injury_coverage=false`，不会把空列表解释成“确定无伤”。
- Polymarket实际是预测市场价格，在项目中仅作为“欧赔参考”；中国体彩网HAD/HHAD才是当前可购买的官方固定奖金SP。
- Gamma响应没有逐价格的可靠更新时间字段；快照记录本程序获取时间，并通过事件开放状态、开赛时间和流动性控制质量。
- 外部公开接口仍可能延迟或出错。本项目保证失败显式、来源可追溯和跨源冲突停止，不能保证外部数据绝对零错误。

## 快照与回测

本地JSON仅用于重放和回测，不作为实时数据的静默备份：

```powershell
python main.py --file data\matches_2026-06-29.json
python main.py --file data\backtest_2026-06-28.json --backtest
```

## 模型

- 市场概率：公开市场三项价格归一化。
- 综合概率：每场积分差35%、每场净胜球差30%、每场进球差20%、缺阵负担差15%，对市场概率做有限启发式修正。
- 当前匿名接口无伤停覆盖，因此缺阵信号为中性，并在质量报告中明确标记。

综合概率尚未经过大样本校准，不构成赛果或收益保证。

## 测试

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest discover -s tests -v
```

单元测试使用内存假响应；验收还会实际执行 `--doctor` 和指定日期的公开接口分析。

---

## 体彩主盘 + 外网辅盘 + 可视化（dev/jun-odds 分支）

**主盘**：中国体育彩票竞彩网官方 API（[sporttery.cn/jc/zqszsc](https://www.sporttery.cn/jc/zqszsc/)）  
**辅盘**：FOX Sports / The Odds API，用于交叉验证走势

| 数据源 | 参数 | 说明 |
|--------|------|------|
| **体彩官方** | `--source sporttery` | 胜平负 + 让球胜平负固定奖金及历史走势（默认主盘） |
| **FOX 爬虫** | `--source fox` | 外网 FanDuel Moneyline，辅助对比 |
| **The Odds API** | `--source api` | 外网 h2h + spreads，需 API Key |
| **本地 feed** | `--source file` | 手动写入 JSON |

### 比分预测（未开赛赛事）

```powershell
python scripts/today_score_predict.py list
python scripts/today_score_predict.py predict
python scripts/today_score_predict.py predict --json

python predict.py list
python predict.py predict --home 巴西 --away 日本
python predict.py predict --match-id 2040337 --json
```

### 体彩快照采集

```powershell
python watch_odds.py record --source sporttery --home 巴西 --away 日本
python watch_odds.py watch --source sporttery --interval 300
```

### 可视化仪表盘

```powershell
python dashboard.py
python dashboard.py --port 8765
```

打开 http://127.0.0.1:8765 可查看：

- 体彩当前可售赛事与固定奖金
- 胜平负 / 让球胜平负历史走势
- 体彩 + 外网融合预测方向与信心
- 假设投注模拟与赛后结算
- 每日自动从高信心且内外盘同向场次中只选一个去水概率最高方向，固定模拟 1000 元
- 自动刷新、导出 JSON/CSV、单场分享链接 `/match/{id}`

### 外网辅盘（可选）

```powershell
python watch_odds.py record --source fox --home 巴西 --away 日本
python watch_odds.py analyze
```

The Odds API 配置见 `.env.example` 中的 `ODDS_API_KEY`。

### 本地 feed

```powershell
copy data\odds_live_feed.example.json data\odds_live_feed.json
python watch_odds.py record --source file --feed data\odds_live_feed.json
```

## 数据口径与限制

- “胜、平、负”只判断常规 90 分钟（含伤停补时），不判断加时赛或点球大战后的晋级方。
- 概率是赔率去水后的市场定价，不是训练模型，也不包含阵容、伤停、天气或临场变化。
- 输出仅用于数据分析演示，不构成投注建议或赛果保证。

### 每日稳健模拟账本与 GitHub Pages

- 公开账本保存于 `data/daily_bets.json`，从 2026-07-01 起按北京销售日每天最多一笔。
- 每日固定 1000 元模拟预算：默认 600 元用于概率最高的稳健单场、400 元用于模型候选中“组合 SP ≥ 2.00 且联合去水概率最高”的二串一；无合格组合时全部用于单场。
- 页面汇总累计总投入、已结算盈利和未结算潜在盈利；待结算方案不会提前计入已实现盈利，并保留每日历史方案。
- 每次自动刷新都会重算当天方案并幂等覆盖，不需要人工改 JSON；不会连接购彩账户或执行真实下注。
- `python scripts/generate_pages.py` 生成 `docs/` 静态站点；Actions 每天北京时间 20:05 自动刷新并部署。
- 自定义域名由 `docs/CNAME` 指向 `dreamv.top`，域名侧仍需按 GitHub Pages 要求配置 DNS。
