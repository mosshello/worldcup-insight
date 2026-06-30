# 世界杯胜平负控制台 MVP

读取本地 JSON 中的十进制欧赔，输出去除市场水位后的 90 分钟胜平负概率、市场排序、信心等级和中文规则分析。项目仅依赖 Python 3 标准库。

## 运行

```powershell
cd C:\Users\mr_zh\Documents\双摄APP\worldcup-console-mvp
python main.py
```

机器可读输出：

```powershell
python main.py --json
```

分析其他数据文件：

```powershell
python main.py --file data\your_matches.json
```

运行测试：

```powershell
python -m unittest discover -s tests -v
```

## 体彩主盘 + 外网辅盘（dev/jun-odds 分支）

**主盘**：中国体育彩票竞彩网官方 API（[sporttery.cn/jc/zqszsc](https://www.sporttery.cn/jc/zqszsc/)）  
**辅盘**：FOX Sports / The Odds API，用于交叉验证走势

| 数据源 | 参数 | 说明 |
|--------|------|------|
| **体彩官方** | `--source sporttery` | 胜平负 + 让球胜平负固定奖金及历史走势（默认主盘） |
| **FOX 爬虫** | `--source fox` | 外网 FanDuel Moneyline，辅助对比 |
| **The Odds API** | `--source api` | 外网 h2h + spreads，需 API Key |
| **本地 feed** | `--source file` | 手动写入 JSON |

### 比分预测（未开赛赛事）

自动拉取体彩竞彩网**未开赛**足球赛事，不再使用硬编码场次：

```powershell
python scripts/today_score_predict.py list
python scripts/today_score_predict.py predict
python scripts/today_score_predict.py predict --json
```

```powershell
python predict.py list
python predict.py predict --home 巴西 --away 日本
python predict.py predict --match-id 2040337 --json
python predict.py predict --home 巴西 --away 日本 --foreign none
```

### 体彩快照采集

```powershell
python watch_odds.py record --source sporttery --home 巴西 --away 日本
python watch_odds.py watch --source sporttery --interval 300
```

### 可视化仪表盘（含体彩模块）

```powershell
python dashboard.py
```

打开 http://127.0.0.1:8765 可查看：
- 体彩当前可售赛事与固定奖金
- 胜平负 / 让球胜平负历史走势
- 体彩 + 外网融合预测方向与信心
- 本地外网采集快照（辅助）

### 外网辅盘（可选）

```powershell
python watch_odds.py record --source fox --home 巴西 --away 日本
python watch_odds.py analyze
python watch_odds.py watch --source fox --interval 600
```

### 2. The Odds API（官方，含亚盘）

1. 在 [the-odds-api.com](https://the-odds-api.com) 免费注册获取 API Key（约 500 次/月）
2. 复制配置：`copy .env.example .env`，填入 `ODDS_API_KEY=你的密钥`
3. 查看可用赛事：

```powershell
python watch_odds.py list-events
python watch_odds.py list-events --json
```

4. 采集与分析：

```powershell
python watch_odds.py record --source api --home 巴西 --away 日本
python watch_odds.py record --source api --event-id <赛事ID>
python watch_odds.py watch --source api --interval 300
```

世界杯 sport_key 默认为 `soccer_fifa_world_cup`；赛季未开始时 API 可能无赛事，可改用 `soccer_epl` 等联赛测试。

### 3. 本地 feed（自建爬虫对接）

```powershell
copy data\odds_live_feed.example.json data\odds_live_feed.json
python watch_odds.py record --source file --feed data\odds_live_feed.json
```

feed 格式：

```json
{
  "match_id": "wc2026-r32-bra-jpn",
  "source": "your-odds-provider",
  "european": {"home": 1.67, "draw": 3.75, "away": 5.35},
  "asian_handicap": {"line": -1.0, "home": 0.88, "away": 1.02}
}
```

### 分析输出

`data/odds_history_bra-jpn.json` 为示例时间序列，分析会输出：

- 欧赔三项变动方向与幅度
- 亚盘让球线、主客队水位变动
- 欧赔与亚盘是否同步指向同一方向
- 最新去水概率与信心等级

## 可视化仪表盘

启动本地 Web 界面，直观查看赛程、概率分布、欧赔/亚盘走势与变动分析：

```powershell
python dashboard.py
python dashboard.py --port 8765
```

浏览器打开：**http://127.0.0.1:8765**

功能：

- 赛程卡片：静态快照的去水概率与信心等级
- 概率环形图：主胜 / 平 / 客胜占比
- 欧赔折线图：历史快照走势
- 亚盘折线图：让球线与主客队水位（有历史数据时）
- 市场分析与盘口变动文字解读

采集新快照后刷新页面即可看到更新。

## 输入格式

```json
{
  "data_as_of": "赔率快照时间",
  "source": "数据来源",
  "matches": [
    {
      "home": "主队",
      "away": "客队",
      "stage": "淘汰赛",
      "kickoff_beijing": "2026-06-30T01:00:00+08:00",
      "odds": {"home": 1.69, "draw": 3.80, "away": 5.20}
    }
  ]
}
```

## 数据口径与限制

- 示例赛程按赛事页面的 2026-06-29 场次整理；北京时间均为 2026-06-30。
- 示例赔率来自 [FOX Sports 的世界杯 32 强赔率页面](https://www.foxsports.com/stories/soccer/2026-world-cup-round-32-odds)，页面标注为 FanDuel 2026-06-28 快照；美式赔率已换算为十进制赔率。
- “胜、平、负”只判断常规 90 分钟（含伤停补时），不判断加时赛或点球大战后的晋级方。
- 概率是赔率去水后的市场定价，不是训练模型，也不包含阵容、伤停、天气或临场变化。
- 输出仅用于数据分析演示，不构成投注建议或赛果保证。
