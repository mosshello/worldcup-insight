# Jun 风格每日单场与二串一技术规格书

## 目标
- 将每日 1000 元模拟预算自动分配为 600 元稳健单场与 400 元二串一，并把 GitHub Pages 首页改造成参考 `dev/jun-odds` 的深色竞彩控制台。

## 影响范围
| 文件 | 原因 |
|---|---|
| `worldcup_mvp/daily_bet.py` | 选择前两名稳定方向、生成单场与二串一组合及预算数据 |
| `tests/test_daily_bet.py` | 验证组合、预算、赔率与幂等记录 |
| `data/daily_bets.json` | 升级并保存当日组合账本 |
| `scripts/generate_pages.py` | 生成 Jun 风格控制台页面 |
| `docs/index.html` | 新页面产物 |
| `docs/daily_bets.json` | 新账本静态快照 |
| `README.md` | 说明自动化与真实投注边界 |
| `.ai/project.md` | 更新项目状态与变更日志 |

## 实施步骤
1. 按销售日筛选高信心且内外盘同向场次，按胜平负去水概率排序。
2. 第一名作为 600 元单场；前两名组成 400 元二串一，组合赔率为两腿 SP 乘积、组合概率为两腿去水概率乘积。
3. 每日总模拟预算严格为 1000 元；不足两场时只记录单场并清晰标记暂无二串一。
4. 账本升级为 v2，同一天刷新仍只保留一条。
5. 页面采用 Jun 风格的 hero、状态面板、策略卡、统计卡、组合腿列表与自动化说明。

## 验证方式
- `python -m unittest tests.test_daily_bet`
- `python -m unittest discover -s tests`
- `python scripts/generate_pages.py`
- 人工核对 600 + 400 = 1000、二串一赔率与返还。

## 回滚方案
- 恢复 `daily_bet.py` v1 单场结构与旧页面生成器，重新生成 `data/` 和 `docs/` 快照。

## 目标编辑文件清单
- `worldcup_mvp/daily_bet.py`
- `tests/test_daily_bet.py`
- `data/daily_bets.json`
- `scripts/generate_pages.py`
- `docs/index.html`
- `docs/daily_bets.json`
- `README.md`
- `.ai/project.md`
