# 五大联赛开源预测方案与赔率实验

## 读过代码的三种做法

| 项目 | 训练或计算 | 预测与验证 |
|---|---|---|
| FootWork | 把主、平、客赔率的倒数归一化，与赛前 Elo 等特征一起训练胜平负分类器。核过一次实际运行配置，原始赔率排除，隐含概率保留。 | 有日期时使用 2020 年前训练、2020–2021 年校准、2022 年起测试。我们不采用它的跨联赛合并，也不采用缺日期时随机切分并用测试集做校准的后备路径。[特征源码](https://github.com/lrivals/FootWork/blob/73f5af5643561e30bc226b9c836f22c8a39d8c28/src/Data_Processing/Multi-Season_Match_Data_Processor.py#L78-L113) · [训练入口](https://github.com/lrivals/FootWork/blob/73f5af5643561e30bc226b9c836f22c8a39d8c28/src/Models/Multiclass_Target/Football_Match_Prediction_Pipeline.py#L81-L137) · [运行配置](https://github.com/lrivals/FootWork/blob/73f5af5643561e30bc226b9c836f22c8a39d8c28/results/All_Leagues/Multiclass_Target/multiclass_prediction2026-02-28_02-12-38/config_used.yaml) |
| EPL Match Prediction | 用历史 xG（预期进球）、球队攻防和短休息特征估计双方进球，再做 Dixon–Coles 低比分修正。每隔 30 天用此前比赛重新拟合。 | 作者公开逐场预测，赔率仅作对照。我们复算了其保留赛季的预测文件，结果见下节。[滚动回测](https://github.com/Sam-Marz/epl-match-prediction/blob/81ecbb1e8438ec7118dad860ae56833dcc366096/src/backtest/rolling_eval.py) · [预测文件和评分脚本](https://github.com/Sam-Marz/epl-match-prediction/blob/81ecbb1e8438ec7118dad860ae56833dcc366096/scripts/reproduce_headline.py) |
| penaltyblog | 有两条不同的计算路径。第一条用历史比分拟合攻防参数；第二条用胜平负和 2.5 大小球市场的概率反推主客进球均值与低比分修正参数。 | 比分矩阵可推算胜平负、大小球和具体比分。市场反推属于单场计算，没有用历史标签训练。我们用源码跑了下面两场实例。[历史比分拟合示例](https://penaltyblog.readthedocs.io/en/latest/models/example.html) · [赔率反推源码](https://github.com/martineastwood/penaltyblog/blob/6870f0e762e9cba35fa722dc14a8daeaf8bc6134/penaltyblog/models/goal_expectancy.py#L190-L373) |

还有经过发表的混合模型。Egidi、Pauli、Torelli 将历史赛果估出的进球强度与赔率隐含的进球强度加权，使用九年数据预测第十个赛季。它给出了可比较的训练方案，权重与效果需要用我们各联赛的样本另行估计。[原论文](https://leoegidi.github.io/paper/combining_egidi.pdf)

## 复算公开英超预测

核对 2024/25、2025/26 共 760 场的日期、主客球队、实际比分，一一对应。用作者归档的主平客概率和独立下载的 Bet365 临场报价重算。两个指标都越低越好。

| 输入或预测来源 | 胜平负 Log loss | Brier |
|---|---:|---:|
| xG 版 | 1.000365 | 0.599370 |
| xG 与短休息版 | 1.003962 | 0.601936 |
| 去除水位的临场市场 | 0.990448 | 0.592009 |

复算的是冻结预测的评分，未从头重新训练作者的模型。收盘价对应临场信息，只能用于相应时段的比较。[作者仓库](https://github.com/Sam-Marz/epl-match-prediction/tree/81ecbb1e8438ec7118dad860ae56833dcc366096) · [赔率字段](https://www.football-data.co.uk/notes.txt)

## 五联赛独立胜平负实验

逐联赛下载 2021/22 至 2025/26 的 25 份文件。以源文件的 H/D/A 结算行为口径，8,908 行中 8,907 行有完整的非收盘平均胜平负赔率，8,908 行有完整平均收盘胜平负赔率，8,907 行有非收盘平均大小 2.5 球报价，8,902 行同时有亚盘让球线及双方非收盘平均报价。这个统计在来源文件内完成，尚未与控制台逐场核定赛事身份、行政改判或历史报价时间。供应商区分首批采集和收盘两套数据，首批采集可能晚于开盘。其公告指出 2025-07-23 起 Pinnacle 特定字段可能滞后。[文件目录和公告](https://www.football-data.co.uk/data) · [字段说明](https://www.football-data.co.uk/notes.txt)

用前四个赛季拟合，最后一个赛季保留测试。每个联赛独立训练。在相同场次分别比较三个方案。足球基线用赛前 Elo 差、双方近五场场均进失球及有效场数训练多分类逻辑回归。市场直接用非收盘平均主平客赔率去水后的概率。混合版给同一个分类器再加两项市场概率对数比。所有比赛按日期处理，当天结果只供后续日期使用。无调参，没有首发、伤停、xG 特征。

| 联赛 | 训练场 | 测试场 | 足球基线 | 仅市场 | 足球加市场 |
|---|---:|---:|---:|---:|---:|
| 英超 | 1,520 | 380 | 1.0351 | 1.0153 | 1.0208 |
| 西甲 | 1,520 | 380 | 0.9851 | 0.9641 | 0.9648 |
| 德甲 | 1,224 | 306 | 0.9632 | 0.9533 | 0.9537 |
| 意甲 | 1,519 | 380 | 1.0072 | 0.9802 | 0.9803 |
| 法甲 | 1,372 | 306 | 1.0128 | 0.9732 | 0.9875 |

五个联赛的 Log loss 都显示加入赔率后优于这个足球基线，单用市场概率的误差又略低于混合版。意甲等小差距不可解释为显著优势。实验只覆盖一个测试赛季和胜平负标签，没有验证比分、让球或爆冷的效果，也不能推断赛前 24 小时能得到同样的报价。

复算时从 `https://www.football-data.co.uk/mmz4281/{赛季}/{联赛}.csv` 下载，赛季为 2122、2223、2324、2425、2526，联赛为 E0、SP1、D1、I1、F1，另存为 `{赛季}-{联赛}.csv`。在仓库根目录执行 `python scripts/research_big_five_market_features.py <下载目录>`。公开源文件后续可能修订。[实验脚本](../../scripts/research_big_five_market_features.py)

## 为什么 1–1 值得单独检查

用 penaltyblog 源码对 2024/25 英超首轮按主胜概率与客胜概率之差选出最接近和最悬殊的两场，未使用真实赛果选场。输入去水的首批平均胜平负及大小球报价。

| 对阵 | 市场隐含主客进球均值 | 概率最高比分 | 1–1 概率 | 至少 4 球概率 |
|---|---|---|---:|---:|
| 埃弗顿–布莱顿 | 1.40 / 1.40 | 1–1（13.4%） | 13.4% | 30.7% |
| 阿森纳–狼队 | 2.73 / 0.62 | 2–0（13.0%） | 5.8% | 43.1% |

在全部五季源文件里，德甲实际至少 4 球占 40.8%，西甲占 25.0%。后续评分应分别检验每个联赛的 1–1、0–1 球、2–3 球、至少 4 球，以及按赛前强弱差分组的概率与实际频率。让球要按具体盘口及结算规则另评。以上是外部案例与离线研究结果，控制台模型和历史预测均未改动。[英超示例数据](https://www.football-data.co.uk/mmz4281/2425/E0.csv) · [市场反推方法](https://penaltyblog.readthedocs.io/en/latest/models/goal_expectancy.html)
