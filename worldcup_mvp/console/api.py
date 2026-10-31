"""Small HTTP transport over the application service."""

from urllib.parse import unquote
from .service import service


def get(route, query):
    app = service()
    if route == "/api/v1/matches":
        return {"success": True, **app.list_matches((query.get("date") or [None])[0])}
    if route == "/api/v1/state":
        return {"success": True, "state": app.store.state()}
    if route == "/api/v1/capabilities":
        return {"success": True, **app.capabilities()}
    if route == "/api/v1/reviews":
        return {"success": True, "reviews": app.reviews()}
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
        return {"success": True, "reviews": app.reviews(refresh=True)}
    if route == "/api/v1/sync":
        app.sync(force=True)
        if app.last_error:
            raise ValueError(app.last_error)
        return {"success": True, **app.list_matches()}
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
