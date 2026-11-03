"""Read models for a competition workspace; no training or prediction mutations."""
from . import model_registry
from .catalog import catalog, classify, provider_configuration
from .history_pipeline import staging_status, competition_staging


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
        'confirmed': count(lambda c: any(r.get('status') == 'confirmed' and len(r.get('players', [])) == 11 for r in c.get('lineups', []))),
        'estimated': count(lambda c: c.get('estimated_lineups')),
        'injuries': count(lambda c: c.get('injuries')),
        'process': count(lambda c: (c.get('match_facts') or {}).get('statistics') or (c.get('match_facts') or {}).get('events')),
        'xg': count(lambda c: any(r.get('code') == 'expectedGoals' and (r.get('home') is not None or r.get('away') is not None) for r in (c.get('match_facts') or {}).get('statistics', []))),
        'external': count(lambda c: (c.get('external_odds') or {}).get('rows')),
        'checked_at': max((c.get('captured_at') or '' for c in contexts), default='') or None,
    }


def workspaces(service):
    result = {}
    matches, statuses = service.store.list_matches(), service.store.statuses()
    for spec in catalog():
        cid = spec["id"]
        training = training_status(service, cid)
        rows = [m for m in matches if classify(m)["id"] == cid]
        details = [(service.store.detail(m["id"]) or {}).get("data", {}) for m in rows]
        coverage = coverage_summary(details)
        def coverage_label(key):
            return f"已取得{coverage[key]}/{coverage['total']}场" if coverage[key] else '尚未取得'
        source_rows = [dict(s) for s in statuses if s["id"] in {"sporttery", "live-scores", "context:"+spec["providers"].get("sporttery", "")}]
        for s in source_rows:
            if s["id"] == "sporttery": s["count"] = len(rows)
            elif s["id"] == "live-scores": s["count"] = sum(m["id"] in service.scores() for m in rows)
            else: s["count"] = sum(bool(d.get("context",{}).get("available")) for d in details)
        if coverage['external']:
            source_rows.append({'id':'context:500:'+cid, 'name':'500公开欧赔',
                'scope':f"初始 / 末次报价，已取得{coverage['external']}/{coverage['total']}场",
                'status':'available' if coverage['external'] == coverage['total'] else 'partial',
                'count':coverage['external'],
                'checked_at':max((d.get('context', {}).get('external_odds') or {}).get('captured_at') or '' for d in details)})
        has_context = any(d.get("context",{}).get("available") for d in details)
        fundamentals = [
            {"id":"results", "name":"历史全场比分", "status":"已入库" if training["rows"] else "待入库", "usage":"攻防与进球模型", "training":bool(training["rows"])},
            {"id":"halves", "name":"历史半场比分", "status":"已入库" if any(c["half_matches"] for c in training["coverage"]) else "待入库", "usage":"条件半全场", "training":bool(training["rows"])},
            {"id":"recent", "name":"近期 / 主客 / 交锋", "status":"已接入" if has_context else "待获取", "usage":"分析依据", "training":False},
            {"id":"lineups", "name":"确认首发", "status":coverage_label('confirmed'), "usage":"至少一队正式名单；逐场核查", "training":False},
            {"id":"estimated", "name":"预计阵容", "status":coverage_label('estimated'), "usage":"非正式首发；未入模型", "training":False},
            {"id":"injuries", "name":"伤停 / 停赛资格", "status":coverage_label('injuries'), "usage":"部分人员消息；名单未完整核查", "training":False},
            {"id":"process", "name":"比赛统计 / 事件", "status":coverage_label('process'), "usage":"赛中事实 / 赛后复盘；未入模型", "training":False},
            {"id":"xg", "name":"xG", "status":coverage_label('xg'), "usage":"来源实测值；未入模型", "training":False},
            {"id":"cross", "name":"跨赛事表现", "status":"未启用", "usage":"未入模型", "training":False},
        ]
        models = []
        method_names = {'poisson':'攻防泊松','dc':'两阶段低比分修正','joint_dc':'联合 Dixon–Coles',
            'elo_logistic':'Elo 逻辑回归','catboost':'CatBoost 胜平负','lightgbm_goals':'LightGBM 进球','dynamic_gamma':'动态 Gamma–Poisson'}
        for m in training["runs"]:
            selected = bool(training["model"] and m["run_id"] == training["model"]["run_id"])
            models.append({**m, "name":spec["name"]+' · '+method_names.get(m.get('kind'),'足球模型'), "method":method_names.get(m.get('kind'),'历史模型'),
                           "validation":m["reference_check"]["label"], "validation_status":m["reference_check"]["label"],
                           "status":"前瞻观察" if selected else "未选用", "selected":selected})
        odds = [{"id":"sporttery", "name":"体彩五种玩法", "source":"体彩官方", "status":"已接入" if spec["providers"].get("sporttery") else "待接入", "usage":"市场参考 / 当前让球线"},
                {"id":"external", "name":"公开欧赔", "source":"500公开欧赔", "status":coverage_label('external'), "usage":"初始 / 末次报价；未入模型"},
                {"id":"historical", "name":"历史盘口快照", "source":"本地快照", "status":"待积累", "usage":"暂不能评估市场增益"}]
        result[cid] = {"training":training, "models":models, "sources":source_rows,
            "fundamentals":fundamentals, "odds":odds, "provider_configuration":provider_configuration(),
            "match_count":len(rows), "competition":spec, "coverage_summary":coverage}
    return result
