"""Small HTTP transport over the application service."""

from urllib.parse import unquote
from .service import service


def get(route, query):
    app = service()
    if route == "/api/v1/matches":
        return {"success": True, **app.list_matches((query.get("date") or [None])[0])}
    if route == "/api/v1/state":
        return {"success": True, "state": app.store.state()}
    if route == "/api/v1/scores":
        if (query.get("refresh") or ["0"])[0] == "1":
            app.sync_scores()
        return {"success": True, "scores": app.scores()}
    if route == "/api/v1/capabilities":
        return {"success": True, **app.capabilities()}
    if route == "/api/v1/competitions":
        return {"success": True, "competitions": app.competitions()}
    if route == "/api/v1/reviews":
        from .ai_reviews import configuration
        return {"success": True, "reviews": app.reviews(), "ai_review_config":configuration()}
    if route == "/api/v1/review-updates":
        raw = (query.get("after") or [None])[0]
        if raw is not None and (not raw.isascii() or not raw.isdigit() or len(raw) > 18):
            raise ValueError("复盘更新游标无效")
        return {"success": True, **app.store.review_updates(int(raw) if raw is not None else None)}
    if route.startswith("/api/v1/matches/"):
        match_id = unquote(route.removeprefix("/api/v1/matches/"))
        return {
            "success": True,
            "detail": app.detail(
                match_id, force=(query.get("refresh") or ["0"])[0] == "1"
            ),
        }
    raise ValueError("未知接口")


def post(route, body):
    if not isinstance(body, dict):
        raise ValueError("请求内容应为 JSON 对象")
    app = service()
    if route == "/api/v1/reviews/sync":
        from .ai_reviews import configuration
        return {"success": True, "reviews": app.reviews(refresh=True), "ai_review_config":configuration()}
    if route == "/api/v1/reviews/generate":
        match_id = body.get("match_id")
        if not isinstance(match_id,str) or not app.store.match(match_id):
            raise ValueError("比赛不存在")
        app.detail(match_id, force=True)
        row = next((r for r in app.reviews() if r["id"] == match_id), None)
        if row is None:raise ValueError("缺少赛前预测归档")
        result = app.ai_reviews.enqueue(row, retry=True)
        app.ai_reviews.start()
        return {"success":True,"ai_review":result}
    if route == "/api/v1/sync":
        app.sync(force=True)
        app.sync_scores()
        if app.last_error:
            raise ValueError(app.last_error)
        return {"success": True, **app.list_matches()}
    if route == "/api/v1/training/evaluate":
        return {"success": True, "job": app.start_training(body.get("competition_id"), body.get('target','full_time'))}
    if route == "/api/v1/models/select":
        from .model_registry import select_observation
        if "run_id" not in body:
            raise ValueError("请指定模型运行标识")
        return {"success":True, "selection":select_observation(body.get("competition_id"), body["run_id"])}
    if route == "/api/v1/datasets/import":
        from .model_registry import import_competition
        if not isinstance(body.get("dataset"), dict):
            raise ValueError("请提供含来源声明的训练数据")
        return {"success":True, **import_competition(body["dataset"], body.get("competition_id"))}
    if route.startswith("/api/v1/matches/") and route.endswith("/evidence"):
        match_id = unquote(route[len("/api/v1/matches/") : -len("/evidence")])
        return {"success": True, "evidence": app.store.add_evidence(match_id, body)}
    if route == "/api/v1/state":
        kind, key, value = body.get("kind"), body.get("key"), body.get("value")
        if not isinstance(key, str) or not key or len(key) > 150:
            raise ValueError("比赛标识无效")
        if kind in ("notes", "reviewNotes", "drafts") and (
            not isinstance(value, str) or len(value) > 4000
        ):
            raise ValueError("笔记应为不超过4000字的文本")
        if kind == "favorites" and not isinstance(value, bool):
            raise ValueError("关注状态应为布尔值")
        if kind in (
            "notes",
            "favorites",
            "reviewNotes",
            "drafts",
        ) and not app.store.match(key):
            raise ValueError("比赛不存在")
        if kind == "plans":
            raise ValueError("训练任务执行服务尚未开放，不能创建空任务")
        app.store.set_state(kind, key, value)
        return {"success": True}
    if route.startswith("/api/v1/matches/") and route.endswith("/predict"):
        match_id = unquote(route[len("/api/v1/matches/") : -len("/predict")])
        return {
            "success": True,
            **app.prediction(match_id, with_narrative=body.get("narrative") is True),
        }
    raise ValueError("未知接口")
