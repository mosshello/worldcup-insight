# 免费亚洲让球盘来源实测（2026-11-09）

结论：找到了可匿名读取真实双边四分之一盘口的公开接口 MoviOdds，已做独立观察适配器；但不能承诺当前五大联赛盘口齐全。本轮其五大联赛有96场赛程，标准全场亚洲让球 group938 可用报价为0，不能把赛程数冒充盘口覆盖。InferSports 可匿名取得赛程，但抽测两场亚盘均为空，不作为已接通的替代源。

## 实测证据

原始响应及回执在 `.ai/tmp/free-ah-probes/`，均为无凭据只读请求，没有注册、付费或绕过访问限制。

|来源|请求与实测|结论|
|---|---|---|
|MoviOdds|`GET https://moviodds.com/odds?sport=soccer` HTTP200，539场，61,465,153字节|可访问，有真实双边亚洲盘|
|MoviOdds过滤|`GET https://moviodds.com/odds?sport=soccer&groups=938` HTTP200，310,867字节，541赛程、250报价、125场含该组|按市场过滤可把61MB缩到约311KB；与前次请求不同时间，数量变化正常|
|InferSports|`GET https://api.infersports.dev/v1/events/live` HTTP200；scheduled列表200场|赛程工作，不等于亚盘可用|
|InferSports盘口|Holon(W)–Hapoel Raanana：odds空且stale=true；Al Hussein Baghdad场odds空、stale=false|空盘不能伪造或以1X2替代|
|The Odds API|免key请求欧赔spreads返回401 MISSING_KEY|有免费额度，需要账号key，本次未注册|
|API-Football|免key请求odds返回403 Missing application key|同上|

MoviOdds 真实样本：Al Hussein Baghdad – Al Nasiriyah，2026-11-09 11:30Z，group938，主队 -0.25 / 1.975、客队 +0.25 / 1.825。两边`ts=10:25:09Z`，该组`polled_at=11:06:01Z`。半场group50137另有stale=true，已排除。五大联赛赛程数：英超20、西甲20、德甲18、意甲20、法甲18；这些赛程未来日期多为11月21日以后，本轮均未见标准全场group938报价。该事实不证明未来不会开盘。

## 适配约束与接入

新增 `worldcup_mvp/console/observation_handicap.py`：`fetch_board()`使用上述公开过滤端点、gzip、每进程至少60秒缓存和大小限制；失败不刷新缓存时间。`adapt_board(board, fixtures, now, aliases, id_map)`返回bindings及逐原因拒绝计数。现有后台可把bindings送入观察workspace的ingest_quotes；页面不触发外部请求。

匹配只接受已审计ID映射，或联赛、主客双方规范别名及精确UTC开赛时间唯一一致；不做字符串相似猜测。保留来源比赛ID、双方名称及匹配依据。有歧义、对调、不同联赛/时间均拒绝。

只处理赛前全场`938/Asian Handicap`，同一提供商bet365、同一market_id、主客相反盘口配对；quarterline必须是0.25倍数。盘口记录为`asian_handicap`，绝不改成体彩`hhad`。停盘、stale、超时、未来时间、单边、非对应盘口均拒绝。报价上下变动与盘口升降分开统计。

来源[官方API说明](https://moviodds.com/doc)明确区分：`ts`是最后价格变化；`polled_at`是最近成功读取（即使价格不变/停盘也会更新）。因此本地receipt、来源checked、changed分别保存；检查group级polled_at与本地时间，不拿旧ts认定稳定报价过期，也不拿本地抓取时间冒充来源发布时间。该源没有证明当前记录就是首次开盘；只标首次观察。多家公司横向比较目前仍不具备。

## 结算和使用范围

[bet365亚洲盘规则](https://help.bet365.com/s/en/sportsrules/soccer/asian-handicap)及[quarterline说明](https://help.bet365.com/s/en-gb/sports/asian-lines)说明分盘结算；滚球按下注后剩余比分，与赛前规则不同。本适配器因此拒绝滚球/半场，保留quarterline和水位，只接观察时间线，`settlement_enabled=false`。未将亚洲盘概率硬归一化成两队胜率，未接入体彩预测结算；启用亚盘收益回测前需完成常规时间、走盘、半赢半输、延期腰斩的独立规则版本和测试。

[MoviOdds来源说明](https://moviodds.com/bet365-soccer-odds-api/)称其为独立项目，非bet365官方授权服务，数据用于信息用途；匿名可访问不代表永久可用或可商业再分发。此处只作为研究观察源，接口实际覆盖及新鲜度必须持续显示，不夸大服务保证。

[InferSports OpenAPI](https://raw.githubusercontent.com/infersports-dev/infersports/main/openapi.yaml)支持full_time、asian_handicap和decimal；其opening字段是上游首见报价，不应当作证明真正开盘。免费层不等于拥有Pinnacle权限。本次盘口空，保留候选。

[The Odds API](https://the-odds-api.com/)免费500 credits/月；[API-Football](https://www.api-football.com/pricing)免费100次/日且赛季受限。都不够无差别覆盖五联赛逐分钟采集；需按比赛阶段预算，实际quarterline覆盖须有key后验证。没有使用网页抓取反爬绕行或未公开OddsPortal端点。

## 验收

适配器测试覆盖双边quarterline、旧价格新确认、缺边、线不对、停盘、陈旧、未来时间、半场、已开赛、身份歧义和跨联赛。真实过滤响应按五大联赛运行，96场中bindings=0，拒绝计数明确为no_usable_handicap。当前不能宣称五大联赛亚盘已补齐；后台接入后等待真实开盘，仅积累当时抓到的记录，不补造过去节点。
