# 国家队 Elo + 双 Poisson 完整训练链路规格书

## 目标
- 导入公开男子国家队赛果，抽取 2022 世界杯与世界杯前近两年比赛作为基础训练数据。
- 将 2026 世界杯全部已结束比赛严格隔离为按时间顺序的封存/在线验证集。
- 建立 Elo、滚动攻防强度和双 Poisson 比分矩阵，统一导出胜平负、总进球、双方进球和比分概率。
- 生成带数据哈希、时间边界、参数、球队状态和验证指标的可复现模型产物。
- 通过 CLI 完成数据刷新、训练、报告和单场预测，并以影子字段接入现有融合预测。

## 影响范围
| 文件 | 原因 |
|---|---|
| `.ai/specs/国家队Elo双Poisson完整训练链路-规格书.md` | 固化范围与验收标准 |
| `worldcup_mvp/international_data.py` | 下载、校验、规范化并时间切分公开国家队赛果 |
| `worldcup_mvp/statistical_model.py` | Elo、滚动攻防、双 Poisson、评估与模型读写 |
| `worldcup_mvp/model_training.py` | 聚合旧预测语料评估与统计模型状态 |
| `worldcup_mvp/fusion_predictor.py` | 影子模式附加统计模型结果，不覆盖当前市场主方向 |
| `worldcup_mvp/dashboard_data.py` | 概览暴露新模型训练状态 |
| `predict.py` | 增加 `train-worldcup`、`model-report`、`model-predict` |
| `docs/model-training.md` | 更新真实训练命令、数据边界和上线规则 |
| `tests/test_international_data.py` | 数据清洗和时间隔离测试 |
| `tests/test_statistical_model.py` | 概率矩阵、训练、评估和无泄漏测试 |
| `tests/test_model_training.py` | 统一训练状态契约测试 |
| `.ai/project.md` | 项目状态和变更日志 |

## 实施步骤
1. 使用 CC0 的 `martj42/international_results` 数据源，缓存原始 CSV 并记录 SHA-256。
2. 丢弃比分为 `NA` 的未来赛事；规范化日期、球队、赛事、90分钟比分与中立场标记。
3. 基础集限定为 2022 世界杯全部比赛，加 2024-06-11 至 2026-06-10 的男子国家队比赛。
4. 2026-06-11 起的 2026 世界杯比赛只进入封存测试；测试按日期逐场预测后再更新状态，禁止未来信息泄漏。
5. 在基础集的连续时间验证段选择 Elo K、进球 Elo 系数和主场系数；不使用 2026 测试集调参。
6. 保存模型 JSON；单场预测从同一比分矩阵派生所有玩法概率。
7. 现有融合预测只附加 `statistical_model` 影子结果，不改变体彩主盘方向。

## 验证方式
- `python -m unittest tests.test_international_data tests.test_statistical_model tests.test_model_training`
- `python predict.py train-worldcup --refresh --json`
- `python predict.py model-report --json`
- `python predict.py model-predict --home Argentina --away Egypt --neutral --json`
- `python -m unittest discover -s tests`
- `python -m py_compile worldcup_mvp/international_data.py worldcup_mvp/statistical_model.py predict.py`

## 回滚方案
- 删除生成的 `data/training/international_results.csv` 与 `data/training/statistical_model.json` 即可恢复未训练状态。
- 影子字段不参与现有方向计算，移除融合模块中的附加调用即可完全回滚。

## 目标编辑文件清单
- `.ai/specs/国家队Elo双Poisson完整训练链路-规格书.md`
- `worldcup_mvp/international_data.py`
- `worldcup_mvp/statistical_model.py`
- `worldcup_mvp/model_training.py`
- `worldcup_mvp/fusion_predictor.py`
- `worldcup_mvp/dashboard_data.py`
- `predict.py`
- `docs/model-training.md`
- `tests/test_international_data.py`
- `tests/test_statistical_model.py`
- `tests/test_model_training.py`
- `.ai/project.md`
