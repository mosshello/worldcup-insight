# 每日稳健模拟投注与 Pages 发布技术规格书

## 目标
- 从 2026-07-01 起，每个北京自然日从当日可售场次中只选择一个市场隐含概率最高的胜平负方向，以 1000 元固定日预算进行模拟记账，并生成可部署到 GitHub Pages 的静态页面。

## 影响范围
| 文件 | 原因 |
|---|---|
| `worldcup_mvp/daily_bet.py` | 新增每日单选、幂等保存与读取逻辑 |
| `worldcup_mvp/score_predictor.py` | 每次预测刷新后触发当日模拟投注记录 |
| `data/daily_bets.json` | 保存从 2026-07-01 起的公开模拟账本 |
| `scripts/generate_pages.py` | 将账本生成静态站点 |
| `docs/index.html` | GitHub Pages 首页 |
| `docs/daily_bets.json` | 页面读取的公开数据快照 |
| `docs/CNAME` | 绑定 `dreamv.top` |
| `.github/workflows/daily-bet-pages.yml` | 定时刷新、生成并部署 Pages |
| `tests/test_daily_bet.py` | 覆盖选择、预算与幂等保存 |
| `README.md` | 说明数据位置、策略口径和部署方式 |
| `.ai/project.md` | 更新项目状态和变更日志 |

## 实施步骤
1. 仅选择 `business_date` 为北京当日且尚未开赛的场次；按胜平负去水概率降序选择一场，概率相同时优先更早开赛。
2. 每日固定模拟本金 1000 元，仅记录胜平负单关，不分配猜比分；同一天重复刷新只更新未开赛记录，不重复追加。
3. 账本记录选择依据、赔率、概率、潜在返还、状态和免责声明。
4. 生成纯静态 Pages 页面和 JSON 快照；GitHub Actions 每日北京时间 20:05 及手动触发时刷新、提交账本并部署。
5. 配置 `docs/CNAME` 为 `dreamv.top`。

## 验证方式
- `python -m unittest tests.test_daily_bet`
- `python -m unittest discover -s tests`
- `python scripts/generate_pages.py`
- 检查 `docs/index.html`、`docs/daily_bets.json` 与 `docs/CNAME`。

## 回滚方案
- 删除新增模块、工作流和 `docs` 产物，并移除 `score_predictor.py` 中的调用即可恢复原有逐场预测日志行为。

## 目标编辑文件清单
- `worldcup_mvp/daily_bet.py`
- `worldcup_mvp/score_predictor.py`
- `data/daily_bets.json`
- `scripts/generate_pages.py`
- `docs/index.html`
- `docs/daily_bets.json`
- `docs/CNAME`
- `.github/workflows/daily-bet-pages.yml`
- `tests/test_daily_bet.py`
- `README.md`
- `.ai/project.md`
