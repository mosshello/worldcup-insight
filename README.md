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
