"""Read models for a competition workspace; no training or prediction mutations."""
from . import model_registry
from .catalog import catalog, classify, provider_configuration
from .history_pipeline import staging_status, competition_staging
from .quality import complete, age_seconds


def training_status(service, competition_id):
    result = model_registry.status(competition_id)
    staging = staging_status(competition_staging(competition_id))
    staging["coverage"] = [r for r in staging.get("coverage", []) if r["competition_id"] == competition_id]
    staging["available"] = bool(staging["coverage"])
    result["staging"] = staging
    result["collected_rows"] = sum(r.get("collected",0) for r in staging["coverage"])
    result["job"] = service.training_jobs.status(competition_id)
    benchmark = model_registry.directory(competition_id) / 'benchmarks' / 'latest.json'
    if benchmark.is_file():
        import json
        try:
            value = json.loads(benchmark.read_text(encoding='utf-8'))
            if value.get('competition_id') == competition_id:
                result['benchmark'] = value
        except (OSError, ValueError):
            result['benchmark'] = None
    if not result["dataset_available"] and staging["available"]:
        result["reason"] = "历史数据待验收"
        if staging.get("rights_status") != "verified":
            result["blockers"].append("来源使用依据待确认")
        result["blockers"].extend(f"{r['season']}：{issue}" for r in staging["coverage"] for issue in r.get("issues", []))
    return result


def aggregate(statuses):
    return {"scoped":True, "rows":sum(s["rows"] for s in statuses), "ready":False,
            "model":None, "evaluation":None, "reason":"请选择赛事", "leagues":[s["competition_id"] for s in statuses if s["rows"]],
            "coverage":[r for s in statuses for r in s["coverage"]],
            "season_coverage":[r for s in statuses for r in s["season_coverage"]],
            "staging":{"coverage":[r for s in statuses for r in s["staging"]["coverage"]]}}


def coverage_summary(details):
    contexts = [d.get('context') or {} for d in details]
    def count(predicate):
        return sum(bool(predicate(c)) for c in contexts)
    return {
        'total': len(details),
        'recent': count(lambda c: any(c.get('recent') or [])),
        'recent_both': count(lambda c: len(c.get('recent') or []) == 2 and all(c['recent'])),
        'venue_both': count(lambda c: len(c.get('venue_recent') or []) == 2 and all(c['venue_recent'])),
        'h2h_checked': count(lambda c: c.get('h2h_checked')),
        'confirmed_both': count(lambda c: {r.get('side') for r in c.get('lineups', []) if r.get('status') == 'confirmed' and len(r.get('players', [])) == 11} == {'home', 'away'}),
        'confirmed': count(lambda c: any(r.get('status') == 'confirmed' and len(r.get('players', [])) == 11 for r in c.get('lineups', []))),
        'estimated': count(lambda c: c.get('estimated_lineups')),
        'estimated_after': count(lambda c: any(r.get('observed_before_kickoff') is False for r in c.get('estimated_lineups', []))),
        'injuries': count(lambda c: c.get('injuries')),
        'process': count(lambda c: (c.get('match_facts') or {}).get('statistics') or (c.get('match_facts') or {}).get('events')),
        'statistics': count(lambda c: any(r.get('code') != 'expectedGoals' for r in (c.get('match_facts') or {}).get('statistics', []))),
        'events': count(lambda c: (c.get('match_facts') or {}).get('events')),
        'xg': count(lambda c: any(r.get('code') == 'expectedGoals' and (r.get('home') is not None or r.get('away') is not None) for r in (c.get('match_facts') or {}).get('statistics', []))),
        'external': count(lambda c: (c.get('external_odds') or {}).get('rows')),
        'checked_at': max((c.get('captured_at') or '' for c in contexts), default='') or None,
    }


def workspaces(service, date=None, competition_id=None, scores=None):
    result = {}
    matches, statuses = service.store.list_matches(), service.store.statuses()
    scores = service.scores() if scores is None else scores
    for spec in catalog():
        cid = spec["id"]
        if competition_id is not None and cid != competition_id:
            continue
        training = training_status(service, cid)
        rows = [m for m in matches if classify(m)["id"] == cid]
        if date:
            from datetime import timedelta, timezone
            from .sources import parse_date
            rows = [m for m in rows if m.get('sale_date')==date or parse_date(m['kickoff']).astimezone(timezone(timedelta(hours=8))).date().isoformat()==date]
        details = [(service.store.detail(m["id"]) or {}).get("data", {}) for m in rows]
        coverage = coverage_summary(details)
        def coverage_label(key):
            return f"已取得{coverage[key]}/{coverage['total']}场" if coverage[key] else '尚未取得'
        source_rows = [dict(s) for s in statuses if s["id"] in {"sporttery", "live-scores", "context:"+spec["providers"].get("sporttery", "")}]
        for s in source_rows:
            if s["id"] == "sporttery": s["count"] = len(rows)
            elif s["id"] == "live-scores": s["count"] = sum(m["id"] in scores for m in rows)
            else: s["count"] = sum(bool(d.get("context",{}).get("available")) for d in details)
        public = [d.get('context',{}).get('public_facts_source') for d in details]
        public = [s for s in public if s and s.get('source') == 'FotMob']
        if public:
            fresh = sum(-30 <= age_seconds(s.get('captured_at')) <= 180 for s in public)
            source_rows.append({'id':'public:fotmob:'+cid,'name':'FotMob公开资料',
                'scope':f"已核对{len(public)}/{len(rows)}场；人员 / 阵容 / xG按场次覆盖",
                'status':'available' if fresh==len(rows) else 'partial' if fresh else 'stale',
                'count':len(public),'fresh_count':fresh,'checked_at':max(s.get('captured_at') or '' for s in public),
                'evidence':public,'training':False})
        if coverage['external']:
            source_rows.append({'id':'context:500:'+cid, 'name':'500公开欧赔',
                'scope':f"初始 / 末次报价，已取得{coverage['external']}/{coverage['total']}场",
                'status':'available' if coverage['external'] == coverage['total'] else 'partial',
                'count':coverage['external'],
                'checked_at':max((d.get('context', {}).get('external_odds') or {}).get('captured_at') or '' for d in details)})
        fundamentals = [
            {"id":"results", "name":"历史全场比分", "status":"已入库" if training["rows"] else "待入库", "usage":"攻防与进球模型", "training":bool(training["rows"])},
            {"id":"halves", "name":"历史半场比分", "status":"已入库" if any(c["half_matches"] for c in training["coverage"]) else "待入库", "usage":"条件半全场", "training":any(c['half_matches'] for c in training['coverage'])},
            {"id":"recent", "name":"双方近期赛果", "status":coverage_label('recent_both'), "usage":"逐队近5场；分析依据", "training":False},
            {"id":"venue", "name":"主客场表现", "status":coverage_label('venue_both'), "usage":"主队主场 / 客队客场；仅本赛事", "training":False},
            {"id":"h2h", "name":"历史交锋", "status":f"已核查{coverage['h2h_checked']}/{coverage['total']}场" if coverage['h2h_checked'] else "尚未核查", "usage":"仅本赛事；无历史记录保留为空", "training":False},
            {"id":"lineups", "name":"双方确认首发", "status":coverage_label('confirmed_both'), "usage":"双方各11人；赛前取得时间逐场核查", "training":False},
            {"id":"estimated", "name":"预计阵容", "status":coverage_label('estimated'), "usage":f"其中{coverage['estimated_after']}场赛后补采；非赛前已知" if coverage['estimated_after'] else "非正式首发；未入模型", "training":False},
            {"id":"injuries", "name":"伤停 / 停赛资格", "status":coverage_label('injuries'), "usage":"部分消息；本场状态与完整名单仍需核查" if coverage['injuries'] else "公开页未取得名单；不代表无人缺席", "training":False},
            {"id":"process", "name":"比赛统计", "status":coverage_label('statistics'), "usage":"比赛累计值；未入模型", "training":False},
            {"id":"events", "name":"比赛关键事件", "status":coverage_label('events'), "usage":"已列进球 / 牌 / 换人；非逐秒记录", "training":False},
            {"id":"xg", "name":"xG", "status":coverage_label('xg'), "usage":"来源实值；本地研究，未入模型" if coverage['xg'] else "来源有效值待核实；不以0替代缺失", "training":False},
            {"id":"cross", "name":"跨赛事模型特征", "status":"待评估", "usage":"尚未完成特征与回测；未入模型", "training":False},
        ]
        models = []
        method_names = {'poisson':'攻防泊松','dc':'两阶段低比分修正','joint_dc':'联合 Dixon–Coles',
            'elo_logistic':'Elo 逻辑回归','catboost':'CatBoost 胜平负','lightgbm_goals':'LightGBM 进球','dynamic_gamma':'动态 Gamma–Poisson'}
        for m in training["runs"]:
            selected = bool(training["model"] and m["run_id"] == training["model"]["run_id"])
            models.append({**m, "name":spec["name"]+' · '+method_names.get(m.get('kind'),'足球模型'), "method":method_names.get(m.get('kind'),'历史模型'),
                           "validation":(m.get('reference_check') or {}).get('label','尚未评估'), "validation_status":(m.get('reference_check') or {}).get('label','尚未评估'),
                           "status":"前瞻观察" if selected else "未选用", "selected":selected})
        quote_count = sum(bool(d.get('odds_fresh') and -30 <= age_seconds(d.get('captured_at')) <= 180 and any(complete(p) for p in d.get('markets',{}).values())) for d in details)
        odds = [{"id":"sporttery", "name":"体彩报价", "source":"体彩官方", "status":f"有效{quote_count}/{len(rows)}场" if rows else '无当前比赛', "usage":"市场参考 / 当前让球线",'count':quote_count,'total':len(rows)},
                {"id":"external", "name":"公开欧赔", "source":"500公开欧赔", "status":coverage_label('external'), "usage":"初始 / 末次报价；未入模型"},
                {"id":"historical", "name":"历史盘口快照", "source":"本地快照", "status":"待积累", "usage":"暂不能评估市场增益"}]
        result[cid] = {"training":training, "models":models, "sources":source_rows,
            "fundamentals":fundamentals, "odds":odds, "provider_configuration":provider_configuration(),
            "match_count":len(rows), "competition":spec, "coverage_summary":coverage}
    return result
