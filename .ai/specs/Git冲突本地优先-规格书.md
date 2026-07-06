# Git冲突本地优先规格书

## 目标
- 解决当前合并冲突，并将本次远端自动合并涉及的相关文件全部恢复为合并前本地版本。

## 影响范围
| 文件 | 原因 |
|---|---|
| `dashboard.py` | 双方修改冲突 |
| `tests/test_score_predictor.py` | 双方修改冲突 |
| `worldcup_mvp/match_intelligence.py` | 双方修改冲突 |
| `data/intelligence_overlay.json` | 远端自动合并与本地接口不兼容 |
| `tests/test_dashboard.py` | 远端自动合并与本地接口不兼容 |
| `tests/test_finished_review.py` | 远端自动合并与本地接口不兼容 |
| `tests/test_match_intelligence.py` | 远端自动合并与本地接口不兼容 |
| `web/app.js` | 远端自动合并改动 |
| `web/index.html` | 远端自动合并改动 |
| `web/styles.css` | 远端自动合并改动 |
| `worldcup_mvp/ai_analyst.py` | 远端自动合并改动 |
| `worldcup_mvp/cache_refresher.py` | 远端自动合并改动 |
| `worldcup_mvp/dashboard_data.py` | 远端自动合并改动 |
| `worldcup_mvp/finished_review.py` | 远端自动合并改动 |
| `worldcup_mvp/sporttery_api.py` | 远端自动合并改动 |

## 实施步骤
1. 对冲突文件选择 Git `ours` 版本。
2. 对远端自动合并文件恢复合并第一父提交（本地分支）版本。
3. 暂存目标文件，确认不存在未合并路径和冲突标记。
4. 运行相关测试。

## 验证方式
- `git diff --name-only --diff-filter=U`
- `git diff --check`
- `python -m unittest tests.test_score_predictor tests.test_match_intelligence tests.test_dashboard`

## 回滚方案
- 在提交前可使用 `git merge --abort` 回到合并前状态。

## 目标编辑文件清单
- `.ai/specs/Git冲突本地优先-规格书.md`
- `dashboard.py`
- `tests/test_score_predictor.py`
- `worldcup_mvp/match_intelligence.py`
- `data/intelligence_overlay.json`
- `tests/test_dashboard.py`
- `tests/test_finished_review.py`
- `tests/test_match_intelligence.py`
- `web/app.js`
- `web/index.html`
- `web/styles.css`
- `worldcup_mvp/ai_analyst.py`
- `worldcup_mvp/cache_refresher.py`
- `worldcup_mvp/dashboard_data.py`
- `worldcup_mvp/finished_review.py`
- `worldcup_mvp/sporttery_api.py`
