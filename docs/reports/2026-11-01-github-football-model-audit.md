# GitHub 足球预测项目调研与日韩适用性

日期：2026-11-01。用途：为日韩模型的数据、概率校准和评估改进提供依据。本次核对 9 个仓库的说明、关键源码或报告；外部成绩均为作者报告，未在本地完整重跑。现有观察模型和历史预测保持原状。

优先采用有明确实现的统计模型作对照，补齐按赛前时点保存的数据，再检验概率校准。此次资料不足以认定某个项目是日韩预测的最优模型。

## 项目选择

| 项目 | 实际能力 | 对本项目的价值 | 主要限制 |
| --- | --- | --- | --- |
| [penaltyblog](https://github.com/martineastwood/penaltyblog) | 联合 Dixon–Coles、多种进球分布、层级贝叶斯 MCMC、赔率去水、概率网格 | 优先作为现有进球模型的独立实现对照 | 工具库，不附带已证明可靠的日韩权重；库中层级贝叶斯不是动态状态模型 |
| [J1League_score_prediction_site](https://github.com/akiokuyama/J1League_score_prediction_site) | 日职 LightGBM 预期进球、泊松比分分布、赛前快照、影子比较 | 日职特征组织、赛制区分、统一多玩法概率和展示 | 历史部分 xG 等使用赛季汇总估计，不能据此认定全部特征满足赛前时点要求 |
| [kleague-odds](https://github.com/waguwagu796/kleague-odds) | K1/K2 赛果采集、攻防估计、低比分修正和赛季模拟 | 韩职逐日回测、主胜分桶诊断、数据新鲜度提示 | 参数选择与报告成绩使用同批比赛；K1/K2 汇总成绩不能当成 K1 独立结果 |
| [football-forecast](https://github.com/dank0i/football-forecast) | DC 特征、LightGBM、神经网络组合、按赛季评估和特征消融 | 比较哪些输入真正有效，保留市场基线 | 欧洲旧数据；部分模型跨联赛训练，不直接沿用其训练范围；首发仍需核实在具体预测时点的可用性 |
| [football-predictor](https://github.com/yukunai/football-predictor) | 联赛登记、DC/Elo、回测、按置信区间分组修正概率 | 数据覆盖、缺失处理、校准前后记录 | 修正强度包含人为常数；支持日韩不等于已证明日韩有效 |
| [footymodel](https://github.com/tanamsethi31/footymodel) | 赛果/xG/阵容实验、按市场评估、赔率对照 | 拒绝无效特征和不成立的优势假设 | 欧洲数据；部分运行链路属于实验，不能把历史结论当成日韩效果 |
| [callumfm/dixon-coles](https://github.com/callumfm/dixon-coles) | DC、少样本收缩、赛季初调参、进球与 xG 混合 | 升班马与低样本球队的专项对照 | 默认参数来自其自带英超数据，未知球队弱实力先验不能原样搬到日韩 |
| [dixon-coles-vs-market](https://github.com/edrosling/dixon-coles-vs-market) | 英超按时间回测与市场误差对照 | 按赛季报告差异，避免只和均匀猜测比较 | 仓库体量很小，主要作为报告和逐场结果的审阅材料 |
| [K_league_in_game_win_probability_model](https://github.com/jmlee8939/K_league_in_game_win_probability_model) | 贝叶斯赛中胜率，使用比分及事件信息 | 将来赛中模块的方法参考 | README 明确说明训练用了欧洲 814 场事件数据；不是韩职专用赛前模型 |

## 与主胜偏高直接相关的发现

### 韩职项目也有分段高估

`kleague-odds` 报告的 742 场 K1/K2 比赛中，平均主胜预测为 36.5%，实际为 36.0%；但主胜预测落在 40%—60% 区间的 252 场，平均预测 47.2%，实际 43.7%；60%—80% 区间仅 24 场，预测 64.0%，实际 58.3%。总体均值接近，仍可能在较高概率区间失准。[作者报告](https://github.com/waguwagu796/kleague-odds/blob/e8d4127c2463dae47b38c063171b7d9b1ec499f8/README.md)

源码逐日使用此前赛果，能够避免直接读入目标日结果；但文件开头明确说明半衰期和先验强度通过这 742 场选定。因此这些成绩属于调参探索，不能称为独立测试。主场乘数由历史主客进球比估计，允许低于 1；伤停每人减攻击力 3% 则是设定值，不是训练结论。[拟合与回测源码](https://github.com/waguwagu796/kleague-odds/blob/e8d4127c2463dae47b38c063171b7d9b1ec499f8/engine.js)

本项目可以借鉴分联赛、分主平客、分概率区间报告的做法。不能搬用其 0.97 主场乘数、伤停扣分或升降级附加赛系数。

### 日职项目校准后反而变差

日职项目现用 LightGBM L2 回归估计双方进球，再生成独立泊松比分分布。作者比较了 L2、Poisson 损失及混合方案，L2 的滚动评估 Log loss 为 1.06544；温度校准后为 1.06762，Brier 也变差，因此保留不校准的 T=1。不能把实现校准函数等同于预测质量改善。[比分分布更新报告](https://github.com/akiokuyama/J1League_score_prediction_site/blob/3635a6626b963588a89f549762b96a148a14cbc2/docs/reports/score_distribution_model_update_2026_27.md)

该报告也记录了大量比赛主胜概率排名第一、实际优势却不强的展示问题。其页面对最高概率偏低或前两项接近的比赛显示接近均势，比分和胜平负由同一分布计算。阈值是它的产品设定，不应直接当作本项目已验证的推荐策略。

温度校准只对各类别概率做同一个正温度变换，保持类别排序。因此即使它改善了概率误差，也不能单靠它把“主胜第一”改为“平局第一”。本项目需分别检验整体过度自信与主客类别偏差。[校准函数](https://github.com/akiokuyama/J1League_score_prediction_site/blob/3635a6626b963588a89f549762b96a148a14cbc2/src/models/probability_calibration.py)

该项目历史 xG、AGI、KAGI 部分使用赛季集计估计；2026 特殊赛季 200 场中，报告称仅 21 场有有效赛前快照。特征数量多不等于赛前证据完整。应借鉴其逐轮存快照的流程，而不能直接导入其历史增强特征并声称无泄漏。[数据准备说明](https://github.com/akiokuyama/J1League_score_prediction_site/blob/3635a6626b963588a89f549762b96a148a14cbc2/docs/reports/season_2026_27_model_preparation.md)

## 模型与评估可借鉴的部分

`penaltyblog` 的联合 DC 显式拟合球队攻防、主场和低比分相关参数；层级贝叶斯实现包含多条 MCMC 链和 R-hat、有效样本量诊断，适合作为本地近似 Gamma 模型之外的研究对照。它不是现有模型的升级证明，也不是带逐场动态状态转移的同一算法。若采用，仍须单赛事拟合、核验概率网格及体彩玩法映射。[联合 DC](https://github.com/martineastwood/penaltyblog/blob/6870f0e762e9cba35fa722dc14a8daeaf8bc6134/penaltyblog/models/dixon_coles.py)、[层级贝叶斯](https://github.com/martineastwood/penaltyblog/blob/6870f0e762e9cba35fa722dc14a8daeaf8bc6134/penaltyblog/models/hierarchical_bayesian_goal_model.py)

`football-forecast` 将 DC、近期表现、评级和阵容特征分组比较，训练窗口内末段用作早停，后续赛季用于评估。作者报告加入市场输入后准确率 52.7%，市场参考为 53.0%；这些欧洲旧数据成绩不能外推日韩。其阵容代码只向过去关联球员评级，但使用当场首发名单时，仍要另行证明名单在我们的赛前截止点已公布。[项目报告](https://github.com/dank0i/football-forecast)、[时间切分](https://github.com/dank0i/football-forecast/blob/32417ea2a404b4ab63240e7d0f1e053ca9cfc073/src/football_forecast/backtest.py)、[阵容关联](https://github.com/dank0i/football-forecast/blob/32417ea2a404b4ab63240e7d0f1e053ca9cfc073/src/football_forecast/features/squad.py)

`football-predictor` 的概率修正按最高概率所在分组读取历史准确率，样本不足 30 时不应用；修正强度还使用固定的先验样本数与质量乘数。这是可以审阅的策略，不应仅凭函数名认定为严格验证后的校准器。若借鉴，必须另外评价修正后的后续比赛。[校准实现](https://github.com/yukunai/football-predictor/blob/b63dbcdb916ac11ca71295fc46d3e035118450f5/models/calibration.py)

`footymodel` 分别报告进球、xG 和阵容实验的失败与收益限制。作者的欧洲五大联赛实验中，混合 xG 改善部分概率指标，但没有形成相对赔率的正收益证据。对我们有用的是同样本逐项加特征、记录无效结果，而不是承诺“补上 xG 就会准确”。[实验记录](https://github.com/tanamsethi31/footymodel/blob/e37e2e17b2eabfc079ac3031c068ed929c3bc2cc/RESULTS.md)

`callumfm/dixon-coles` 按球队样本量调节收缩强度，并在赛季初窗口比较参数。适合研究长崎等少样本队伍，但其英超调参结果及未知队伍先验需要在日韩重新验证。[模型](https://github.com/callumfm/dixon-coles/blob/dea06c6e4b38085d6a4b84186cb9ead321a73ce4/src/dixon_coles/model.py)、[调参窗口](https://github.com/callumfm/dixon-coles/blob/dea06c6e4b38085d6a4b84186cb9ead321a73ce4/src/dixon_coles/backtesting.py)

## 对现有系统的实施建议

现有系统已具备分赛事训练、联合 DC、LightGBM、近似动态 Gamma 和比分分布输出。下一步优先验证数据及时性和评估完整性，不以新增算法数量作为完成标准。

| 顺序 | 工作 | 验收条件 |
| --- | --- | --- |
| 1 | 核对近期赛果缺口、来源身份、主客方向、90 分钟标签和实际可用时间 | 每场能追溯训练用到的最新事实；模拟可用时间与真实采集时间分开 |
| 2 | 增加本赛事历史主平客频率基线和同截止时点市场对照 | 基线只使用过去；没有历史盘口就不报市场优势；开盘与临场价不混用 |
| 3 | 按赛事、赛季、阶段、主平客类别检查概率误差 | 输出样本数、概率分桶及按时间块估计的不确定区间，区分系统偏差与少量赛果波动 |
| 4 | 比较不校准、温度校准及可修正类别偏差的正则化方案 | 校准器只拟合此前预测；后续留出段评价；变差则不启用 |
| 5 | 逐组接入有时点证据的休息天数、射门、xG、首发和伤停 | 缺失不冒充已知；逐组消融；同时报告覆盖率与共同样本成绩 |
| 6 | 以 penaltyblog 作联合 DC 实现对照，必要时实验层级贝叶斯 | 同一日韩数据、同一截止、同一比赛集合；贝叶斯另检验收敛、耗时和分布有效性 |
| 7 | 冻结方案后前瞻观察，再单独决定选用 | 既有测试段标探索；不改历史预测；日职、韩职分别通过或失败 |

校准方案还必须维持玩法一致性：只修改胜平负而保持比分、总进球不变，会造成不同页面相互矛盾。应验证对比分分布的调整，或明确把单独校准的胜平负作为独立预测能力，不静默拼接。半全场仍需独立的联合半场评估，不能由新增全场算法自动宣布通过。

市场参考与足球模型保持独立。只有积累了足够的赛前盘口快照，才另建“包含赔率输入”的实验；不能用临场收盘价帮助提前数小时的预测，也不能把体育彩票整数让球三项玩法与亚洲让球盘混算。

主场参数已在本系统拟合；本次调研不支持直接删除它或统一降低若干百分点。可比较按时间变化的主场项、少样本收缩和类别校准，但是否采用必须由事先固定的验证方案决定。

## 复核范围与来源记录

API 元数据显示 penaltyblog 和 callumfm/dixon-coles 声明 MIT，J1League_score_prediction_site 声明 Apache-2.0；其余六个仓库未识别到许可证。未识别不等于许可不存在，接入代码前仍须核对具体文件；代码许可也不能替代原始数据的使用权限。

源码快照、仓库元数据和摘要存放在本地 `.ai/tmp/github-research-20261101/`；上文关键链接固定到所查提交。仓库作者报告的测试成绩没有作为本项目训练或模型选择输入。韩职赛中事件模型和传球落点竞赛不属于本次赛前胜平负/比分模型候选。

上述建议延续 [模型扩展方案](../football-model-expansion-plan.md) 和 [系统契约](../football-system-contract.md)，本调研不表示新增能力已实现，也不变更当前模型选择。
