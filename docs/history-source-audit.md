# 日韩历史数据采集验收

目前实现的是限量、可复现的数据源核验程序，不是全量训练数据导入器。

## 本轮实测

2026-10-31，对 2023、2024、2025 年日职和韩职各取 6 场，共 12 场：

- 12 场官方赛程与详情的全场比分一致。
- 12 场半场、全场比分均与 500 的对应记录一致。此比例是样本数据一致率，不是预测准确率。
- 日职读取了三个赛季索引，分别有 306、380、380 条记录；只有 6 场详情完成核验，不能把索引数量当成已验收训练样本量。
- 韩职读取三个赛季的 3 月和 11 月索引，共 120 条记录；只有 6 场完成详情和跨来源核验。
- 500 单场体彩让球胜平负走势读取到 9 条带更新时间的 SP；尚未通过比赛 ID、让球数、截止时刻的完整联合校验，不进入回测。
- 懂球帝所测战报页返回 403。程序不绕过限制，403/429 存档后不自动重试。

## 发现并处理的差异

1. 韩职 500 页面阶段名称跨年份不同：联赛/联赛赛程、冠军赛/冠军附加、降级赛/降级附加。通过明确对照解析，未知名称报错。
2. 韩职附加阶段有两种轮次编号：34—38 和 1—5。先读取阶段页面的轮次定义，再转换；不按年份硬编码猜测。
3. 韩职比赛事件的结束标记包括“后半场结束”和“比赛结束”。需确认阶段完整且累计进球与全场比分一致，才能推导半场比分；缺失或乌龙球归属不明确时留空。
4. 日职官方实际开球时间和 500 赛程时间有差异。2023-12-03 名古屋对柏相差 48 分钟，保留人工核查标记；不能用实际开球时间替代历史预测时使用的赛程时间。
5. 500 轮次列表里的平均欧指没有历史观察时间，不能用于 T−60 分钟或收益回测，也不能当成体彩 SP。

## 运行

```powershell
.venv/Scripts/python.exe -m worldcup_mvp.console.history_audit
.venv/Scripts/python.exe -m worldcup_mvp.console.history_audit --offline
.venv/Scripts/python.exe -m unittest tests.test_history_audit
```

默认输出到 `data/training/source_audit/`，整个目录已被现有 Git 忽略规则覆盖：

- `report.md`：可阅读的覆盖、样本及差异报告。
- `report.json`：结构化样本、跨来源 ID、核对结果、数据来源。
- `raw/*.body`：原始响应；同名 `.json` 保存 URL、请求体、时间、编码及 SHA-256。
- `raw/*.denied.json`：403/429 访问失败记录。不会自动重试；确认来源恢复或使用条件后，再单独处理该失败缓存。

默认串行访问，未命中缓存时相邻请求至少间隔一秒，超时 25 秒，不登录、不绕过访问限制。离线模式不发出网络请求。成功缓存保留最初采集时间。

## 入库边界

当前 `rights_status=unverified`、`training_eligible=false`、`result_available_at=null`。它们分别表示使用依据未确认、未允许导入训练、历史赛果可用时间未取得，不能互相代替。

代码不会写入 `club_history.json`、训练模型、激活模型、改写预测或结算结果。

公开页面可读取，不等于已取得批量采集、训练或收费产品使用许可。本次未取得明确适用于这些用途的使用依据；不将会员协议、视频使用规范或第三方转载误当作数据授权，也不据此推断采集必然违法。

后续全量采集需扩展球队对照表、核对完整赛季及球队场次、隔离比分冲突，并明确赛果可用时间的可靠来源或标注为假设的保守回测口径。全部验收通过后，再通过现有 `console.training --import-file` 导入。

## 已核验入口

- 日职官方：[赛季索引](https://data.j-league.or.jp/SFMS01/search?competition_years=2024&competition_frame_ids=1)、[比赛详情](https://data.j-league.or.jp/SFMS02/?match_card_id=30430)。
- 韩职官方：[赛程](https://www.kleague.com/schedule.do?leagueId=1)、[比赛详情](https://www.kleague.com/match.do?year=2024&leagueId=1&gameId=1&meetSeq=1)。赛程页面使用公开 POST `/getScheduleList.do`，详情使用公开 `/api/ddf/match/matchInfo.do`。
- 500：[2024 日职](https://liansai.500.com/zuqiu-7125/)、[2024 韩职](https://liansai.500.com/zuqiu-7105/)、[轮次数据示例](https://liansai.500.com/index.php?c=score&a=getmatch&stid=20613&round=1)。轮次接口来自页面自身加载的 `stage.js`，不是未公开授权接口。

来源角色：官方赛果作为主记录，500 用于交叉校验和赔率补充；懂球帝暂不作为数据链路依赖。
