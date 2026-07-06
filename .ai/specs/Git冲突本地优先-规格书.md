# Git冲突本地优先规格书

## 目标
- 解决当前合并中的3个冲突文件，全部采用当前本地分支版本。

## 影响范围
| 文件 | 原因 |
|---|---|
| `dashboard.py` | 双方修改冲突 |
| `tests/test_score_predictor.py` | 双方修改冲突 |
| `worldcup_mvp/match_intelligence.py` | 双方修改冲突 |

## 实施步骤
1. 对目标文件选择 Git `ours` 版本。
2. 暂存目标文件，确认不存在未合并路径和冲突标记。
3. 运行相关测试。

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
