# 当前球队名册采集与使用边界

本次实测本地五大联赛当前赛程涉及的全部96队，ESPN roster端点全部成功。已保存96份有真实采集时间、精确ESPN团队/球员ID、原始回执哈希和应用有效期的独立快照。它们证明采集时来源列出了这些人，不能证明官方注册、完整伤停或本场可出场。

## 实际覆盖

|赛事|球队|来源列名关系|
|---|---:|---:|
|英超|20|603|
|西甲|20|628|
|德甲|18|541|
|意甲|20|655|
|法甲|18|529|
|合计|96|2956|

唯一ESPN球员ID为2955个。球员`espn:213535`同时出现在`espn:99`和`espn:175`的名单中：保留两份来源观察、显示`concurrent_listing_conflicts`，不猜测当前归属、转会生效时间或租借关系。

所有96队的球员injuries数组均为空。这只证明该响应未列伤停，不代表伤病为0，也不改变已有FPL和逐场官方消息的部分覆盖口径。

28队返回70条coach记录，存在明显历史条目。例：[ESPN Arsenal roster](https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/teams/359/roster)在2026赛季返回Arsene Wenger；[英超官方2026/27教练名单](https://www.premierleague.com/en/news/4679012/manager-line-up-complete-for-the-202627-season)列Mikel Arteta。因此这些字段只保存在`unverified_coach_listings`，`current_appointment=null`，不作为当前教练、离任日期、关系链或训练特征。曼城响应也包含多名历史教练，进一步说明不能用字段存在推断事实当前有效。

原始回执与逐队计数：`.ai/tmp/roster-probes/coverage.json`，`ingestion.json`及逐队JSON。96份球队/赛季校验成功，合计2956条源列名关系已入独立SQLite表。

## 与现有来源的关系

- 既有`observation_personnel`读取历史逐场上场、分钟和教练事实，继续保持其历史时间规则。
- 既有`observation_personnel_sources`维护FPL人员消息和全赛事俱乐部赛程，不与本次ESPN player IDs按姓名合并。
- 既有逐场summary roster用于实际首发，本次球队名册不是首发。
- 既有`personnel.official_bulletins`维持逐场人工核验公告，不被来源当前列名覆盖。
- 新名册不修改任何训练产物或选用；`training_allowed=false`。

## 已实现接口

模块`observation_rosters.py`：

```python
rosters = CurrentRosterStore(store)
rosters.sync_current_fixtures(max_teams=8)  # 仅后台/批处理，最多并发4
rosters.read_team(cid, team_id, season, asof)  # 纯缓存
rosters.fixture_evidence(fixture, asof)       # 纯缓存，kind=roster
```

后台成功请求按球队/赛事/赛季缓存6小时，失败至少5分钟后重试；数据库原子领取避免重复采集。可按最久未采集球队轮转，也可显式批量采集96队：

```powershell
.venv/Scripts/python.exe scripts/collect_current_rosters.py --max-teams 96
```

原始响应、规范快照均内容寻址并追加保存，读取核验两层哈希。球队ID、赛季、soccer athlete UID和重复球员ID均严格校验。仅接受`espn:`ID，不猜合并其他提供商身份。

`captured_at/available_from`为实际观测时间，`source_response_at`保留来源响应时间但不冒称发布时间，`published_at=null`。24小时有效期仅为应用刷新窗口，**不是合同或注册有效期**。读到过期/截点之前未观测的名单只返回明确不可用状态；今天名册不会进入昨天的预测或历史训练。

未来比赛可使用截止时已观测且未过期的“来源列名”事实；比赛注册名单、赛事停赛资格、目标场次可用性、预计分钟、合同开始结束、完整伤停和确认当前教练仍为null/unknown。球队跨赛事共享ESPN稳定身份，不共享赛事资格推断。

## 测试

测试覆盖源名单与注册/伤停/教练事实分离、球队及赛季错误、球员UID错域/重复、未来来源时间、截止前不可读、过期、跨赛事隔离、原始回执篡改、重复入库幂等、多队同时列名冲突，以及纯缓存读取不触发网络。
