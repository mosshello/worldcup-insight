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
