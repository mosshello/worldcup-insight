# 2026-11-04 人员与 xG 补充来源核验

范围：体彩销售日 / 北京开赛日命中今天的全部 20 场；逐场实际网络请求，非抽样。

## 已接入

- FotMob 公共日期赛程与比赛详情接口，20/20 唯一匹配；赛事、主客双方来源 ID、精确开球时间在赛程和详情两次核验。
- 18/20 场返回共 131 条人员记录；这是来源报告，不是完整医疗或停赛资格核查。
- 8 场已结束欧冠都有来源实测 xG；仅显示赛中/赛后事实，禁止当作赛前可见输入。
- 8 场后续欧冠返回真正 predicted 首发。其余来源 lastStarting11 不冒充本场预计或确认首发。
- 鹿岛官方 10 月 29 日公布金太铉右胫骨近端骨折，治疗期未公开；单独记录官方伤情，未据此宣称本场最终缺阵。

## 逐场结果

| 比赛 ID | 比赛 | 人员记录 | 本源预计首发队数 | 本源阵容类型 | 本场实测 xG | 采集时间 UTC | 来源 |
|---|---|---:|---:|---|---|---|---|
| sporttery:2042498 | 加拉塔萨 vs 斯图加特 | 5 | 0 | standard | 0.98 / 2.2 | 2026-11-04T10:28:32.824501+00:00 | [详情](https://www.fotmob.com/match/6106341) |
| sporttery:2042499 | 马竞 vs 拜仁 | 6 | 0 | standard | 1.02 / 1.91 | 2026-11-04T10:28:33.808879+00:00 | [详情](https://www.fotmob.com/match/6106266) |
| sporttery:2042500 | LASK林茨 vs 布拉迪斯 | 3 | 0 | standard | 4.45 / 0.47 | 2026-11-04T10:28:32.593075+00:00 | [详情](https://www.fotmob.com/match/6106408) |
| sporttery:2042501 | 费耶诺德 vs 国际米兰 | 9 | 0 | standard | 0.73 / 1.19 | 2026-11-04T10:28:32.624205+00:00 | [详情](https://www.fotmob.com/match/6106306) |
| sporttery:2042502 | 曼联 vs 罗马 | 10 | 0 | standard | 2.7 / 1.6 | 2026-11-04T10:28:34.143465+00:00 | [详情](https://www.fotmob.com/match/6106291) |
| sporttery:2042503 | 巴萨 vs 维拉 | 9 | 0 | standard | 1.96 / 0.72 | 2026-11-04T10:28:34.400645+00:00 | [详情](https://www.fotmob.com/match/6106263) |
| sporttery:2042504 | 比利亚雷 vs 巴黎圣曼 | 9 | 0 | standard | 0.7 / 2.37 | 2026-11-04T10:28:34.643907+00:00 | [详情](https://www.fotmob.com/match/6106326) |
| sporttery:2042505 | 博德闪耀 vs 里尔 | 7 | 0 | standard | 0.65 / 0.59 | 2026-11-04T10:28:35.167752+00:00 | [详情](https://www.fotmob.com/match/6106316) |
| sporttery:2042506 | 浦项制铁 vs 大阪钢巴 | 0 | 0 | standard | 来源不提供 | 2026-11-04T10:28:36.156271+00:00 | [详情](https://www.fotmob.com/match/6050006) |
| sporttery:2042507 | 布里兰 vs 鹿岛鹿角 | 0 | 0 | lastStarting11 | 尚未开球，无本场实测值 | 2026-11-04T10:28:36.032054+00:00 | [详情](https://www.fotmob.com/match/6050007) |
| sporttery:2042508 | 雷恩 vs 奥林匹亚 | 6 | 0 | lastStarting11 | 尚未开球，无本场实测值 | 2026-11-04T10:28:36.230397+00:00 | [详情](https://www.fotmob.com/match/6112306) |
| sporttery:2042509 | 费内巴切 vs 利物浦 | 9 | 2 | predicted | 尚未开球，无本场实测值 | 2026-11-04T10:28:36.780856+00:00 | [详情](https://www.fotmob.com/match/6106330) |
| sporttery:2042510 | 莱红牛 vs 曼城 | 5 | 2 | predicted | 尚未开球，无本场实测值 | 2026-11-04T10:28:36.634388+00:00 | [详情](https://www.fotmob.com/match/6106322) |
| sporttery:2042511 | 波尔图 vs 那不勒斯 | 14 | 2 | predicted | 尚未开球，无本场实测值 | 2026-11-04T10:28:37.965424+00:00 | [详情](https://www.fotmob.com/match/6106288) |
| sporttery:2042512 | 埃因霍温 vs 布鲁日 | 7 | 2 | predicted | 尚未开球，无本场实测值 | 2026-11-04T10:28:37.024290+00:00 | [详情](https://www.fotmob.com/match/6106303) |
| sporttery:2042513 | 多特蒙德 vs 贝蒂斯 | 7 | 2 | predicted | 尚未开球，无本场实测值 | 2026-11-04T10:28:37.451774+00:00 | [详情](https://www.fotmob.com/match/6106271) |
| sporttery:2042514 | 维京 vs 萨巴赫 | 5 | 2 | predicted | 尚未开球，无本场实测值 | 2026-11-04T10:28:37.732283+00:00 | [详情](https://www.fotmob.com/match/6106420) |
| sporttery:2042515 | 朗斯 vs 科莫 | 10 | 2 | predicted | 尚未开球，无本场实测值 | 2026-11-04T10:28:37.997655+00:00 | [详情](https://www.fotmob.com/match/6106416) |
| sporttery:2042516 | 斯拉维亚 vs 阿森纳 | 5 | 2 | predicted | 尚未开球，无本场实测值 | 2026-11-04T10:28:38.536240+00:00 | [详情](https://www.fotmob.com/match/6106389) |
| sporttery:2042517 | 布城 vs 伍尔弗 | 5 | 0 | lastStarting11 | 尚未开球，无本场实测值 | 2026-11-04T10:28:39.068065+00:00 | [详情](https://www.fotmob.com/match/5836918) |

## 来源核对与边界

- ESPN：18 个原有精确映射比赛详情实际成功返回；伤停字段不存在，已结束比赛每场两队共 56 个原始统计字段中没有 xG。球队 injuries 端点返回空对象，不能解释成无人缺阵。另两场 ESPN 身份映射由独立任务修复。
- UEFA 官方赛前稿列出 8 场后续欧冠的预计首发和缺阵/存疑名单；网页检索可读，但本机默认网络和直连请求均超时，所以未谎称自动采集成功。
- 鹿岛官方新闻主页本机请求返回 HTTP 429；未绕过访问限制。通过官方原文核对一条伤情并按现有人工核验公告机制保存。
- 两场亚冠的 FotMob unavailable 字段缺失，不是空名单。布里兰—鹿岛补到一条官方伤情；浦项—大阪钢巴仍缺可核验的完整伤停/资格名单。部分非官方页面甚至链接到无关主教练任命稿，未采用。
- 当前实测 xG 不是训练特征：历史 xG 特征还需要按历史场次、采集可见时间、比赛阶段、来源一致性构建独立数据集并独立评估；不能将今日赛后补采的值回填到旧预测。
- 初始 40 支球队映射仅覆盖已逐场核验的四类赛事；未来新队必须新增经核实的来源 ID，未映射明确报错，不用名称模糊匹配或只凭开球时间。

## 可复核来源

- [UEFA 官方阵容与伤停稿](https://fr.uefa.com/uefachampionsleague/news/02ab-21f9953bfd93-e349e95dc494-1000--ligue-des-champions-compositions-de-depart-et-probables-d/)
- [鹿岛官方伤情公告](https://www.antlers.co.jp/blogs/news/261029hb37vq)
- [FotMob 日期赛程](https://www.fotmob.com/api/data/matches?date=20261104)

## 实现与验证

- 模块 public_match_facts.py；supplemental_facts/fundamentals 只追加当前资料，不改历史预测或训练集。
- 原始 JSON 与 SHA256、来源 URL、真实 captured_at 保存在 data/console/public_match_facts；同响应内容可复核。
- 41 项聚焦测试通过：双方/赛事/时间冲突、重复赛程、先后时间、缺字段与空名单、上一场名单、重复球员、赛前 xG、NaN/负值、网络失败保留旧来源等。
- 四场实际完整基本面链路验证通过：已结束欧冠、后续欧冠、亚冠、英冠。人员/xG 合并正确，injuries_verified 全部保持 false。
- 原始逐场核验：.ai/tmp/personnel-xg-audit/fotmob-audit.json；ESPN核验：espn-audit.json；完整链路：merged-context-smoke.json。
