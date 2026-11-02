"""Immutable, competition-owned datasets, candidate runs and observation pointers."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from . import training
from .catalog import catalog
from .store import ROOT, fingerprint, now_iso

REGISTRY = ROOT / "data" / "training" / "competitions"
MANIFEST_FIELDS = ("usage_mode", "local_training_authorized", "authorization_reference",
    "rights_status", "commercial_use_permitted", "training_permitted", "license_reference",
    "source_url", "availability_policy", "quality_basis")


def competition_spec(competition_id):
    for spec in catalog():
        if spec["id"] == competition_id:
            return spec
    raise ValueError("请指定已登记的赛事")


def directory(competition_id):
    competition_spec(competition_id)
    if not re.fullmatch(r"[a-z0-9_.-]+", competition_id):
        raise ValueError("赛事标识无效")
    return REGISTRY / competition_id


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def scoped_dataset(data, competition_id):
    competition_spec(competition_id)
    if (not isinstance(data, dict) or not isinstance(data.get("manifest"), dict)
        or not isinstance(data.get("matches"), list) or not all(isinstance(r,dict) for r in data["matches"])
        or not isinstance(data.get("team_mappings", []), list) or not all(isinstance(r,dict) for r in data.get("team_mappings", []))):
        raise ValueError("训练数据格式无效")
    # Filter before validating: corruption in another competition is not this one's failure.
    rows = [r for r in data.get("matches", []) if r.get("competition_id") == competition_id]
    manifest = {k: data.get("manifest", {})[k] for k in MANIFEST_FIELDS if k in data.get("manifest", {})}
    manifest.update(competition_id=competition_id, training_scope="competition_only", target_period="regular_time")
    value = {"manifest": manifest, "matches": sorted(rows, key=lambda r: (r.get("kickoff", ""), r.get("id", ""))),
             "team_mappings": sorted([r for r in data.get("team_mappings", []) if r.get("competition_id") == competition_id],
                                     key=lambda r: (r.get("provider", ""), r.get("provider_id", "")))}
    validate_scope(value, competition_id)
    return value


def validate_scope(data, competition_id):
    spec = competition_spec(competition_id)
    if spec.get("training_enabled", True) is not True:
        raise ValueError("该赛事尚未完成数据与规则接入")
    if data.get("manifest", {}).get("competition_id") != competition_id or data["manifest"].get("training_scope") != "competition_only":
        raise ValueError("训练数据缺少独立赛事范围")
    if any(r.get("competition_id") != competition_id for r in data.get("matches", [])):
        raise ValueError("训练数据混入其他赛事")
    if any(r.get("competition_id") != competition_id for r in data.get("team_mappings", [])):
        raise ValueError("球队映射混入其他赛事")
    rows = training.validate_dataset(data)
    policy = spec.get("training_stage_policy", {})
    if any(row["stage"] not in policy.get(row["season"], []) for row in rows):
        raise ValueError("比赛赛季或阶段未登记到该赛事训练规则")
    return rows


def import_competition(data, competition_id):
    value = scoped_dataset(data, competition_id)
    digest = fingerprint(value)
    root = directory(competition_id)
    path = root / "datasets" / (digest + ".json")
    if path.exists():
        if fingerprint(read(path)) != digest:
            raise ValueError("已有数据版本校验失败")
    else:
        training.atomic_json(path, value)
    training.atomic_json(root / "dataset.json", {"digest": digest, "updated_at": now_iso()})
    return {"competition_id": competition_id, "dataset_digest": digest, "rows": len(value["matches"])}


def dataset(competition_id, digest=None):
    root = directory(competition_id)
    digest = digest or read(root / "dataset.json")["digest"]
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("数据版本无效")
    value = read(root / "datasets" / (digest + ".json"))
    if fingerprint(value) != digest:
        raise ValueError("训练数据版本校验失败")
    validate_scope(value, competition_id)
    return value


@contextmanager
def training_lock(competition_id):
    path = directory(competition_id) / "training.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if path.stat().st_size == 0:
            handle.write(b"0"); handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError("该赛事已有训练任务") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def train(competition_id, progress=None, target='full_time'):
    if target not in ('full_time','had'):
        raise ValueError('训练目标无效')
    spec = competition_spec(competition_id)
    if spec.get("training_enabled", True) is not True:
        raise ValueError("该赛事尚未完成数据与规则接入")
    with training_lock(competition_id):
        data = dataset(competition_id)
        if target == 'full_time':
            report = training.evaluate(data, progress, modes=("separate",))
        else:
            from .model_evaluation import evaluate
            report = evaluate(data, progress, target=target)
        if progress:
            progress({"phase": "fit"})
        bundle = training.fit_bundle(data, report)
        report.update(model_id=bundle["id"], competition_id=competition_id, training_scope="competition_only")
        run_id = uuid4().hex
        root = directory(competition_id)
        run = root / "runs" / run_id
        training.atomic_json(run / "model.json", bundle)
        training.atomic_json(run / "evaluation.json", report)
        manifest = {"run_id": run_id, "competition_id": competition_id, "created_at": now_iso(),
                    "dataset_digest": fingerprint(data), "model_id": bundle["id"],
                    "model_digest": fingerprint(bundle), "evaluation_digest": fingerprint(report)}
        manifest["digest"] = fingerprint(manifest)
        training.atomic_json(run / "manifest.json", manifest)
        training.atomic_json(root / "candidate.json", {"run_id": run_id})
        if report.get('models'):
            from .model_benchmark import publish_summary
            publish_summary(competition_id, report, run_id)
        # Training never changes observation.json or production selection.
        return {**manifest, "test": report["test"], "reference_check": training.reference_checks(report)[competition_id]}


def load_run(competition_id, run_id):
    if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("模型运行标识无效")
    root = directory(competition_id) / "runs" / run_id
    if not (root / "manifest.json").is_file():
        raise ValueError("该赛事不存在此模型")
    manifest = read(root / "manifest.json")
    if manifest.get("competition_id") != competition_id or manifest.get("run_id") != run_id or manifest.get("digest") != fingerprint({k:v for k,v in manifest.items() if k != "digest"}):
        raise ValueError("模型归属或版本校验失败")
    bundle, report = read(root / "model.json"), read(root / "evaluation.json")
    if fingerprint(bundle) != manifest["model_digest"] or fingerprint(report) != manifest["evaluation_digest"]:
        raise ValueError("模型或评估文件校验失败")
    if (set(bundle.get("models", {})) != {competition_id}
        or bundle["models"][competition_id].get("mode") != "separate"
        or report.get("competition_id") != competition_id
        or bundle.get("dataset_digest") != manifest["dataset_digest"]
        or report.get("dataset_digest") != manifest["dataset_digest"]
        or report.get("model_id") != bundle.get("id")
        or bundle.get("digest") != fingerprint({k:v for k,v in bundle.items() if k != "digest"})):
        raise ValueError("模型、评估与独立赛事数据不一致")
    data = dataset(competition_id, manifest["dataset_digest"])
    return manifest, bundle, report, data


def select_observation(competition_id, run_id):
    if run_id is None:
        training.atomic_json(directory(competition_id) / "observation.json", {"run_id": None, "selected_at": now_iso()})
        return {"competition_id": competition_id, "run_id": None}
    manifest, _, _, _ = load_run(competition_id, run_id)
    selection = {"run_id": run_id, "competition_id": competition_id, "selected_at": now_iso(), "model_id": manifest["model_id"]}
    training.atomic_json(directory(competition_id) / "observation.json", selection)
    return selection


def forecast(match, detail, now=None):
    from .catalog import classify
    cid = classify(match)["id"]
    unavailable = lambda reason: {"status": "unavailable", "reason": reason, "markets": [], "competition_id": cid}
    try:
        if classify(match).get("season") is None:
            return unavailable("该比赛赛季尚未登记")
        pointer = directory(cid) / "observation.json"
        if not pointer.exists() or not read(pointer).get("run_id"):
            return unavailable("该赛事尚未选用观察模型")
        run_id = read(pointer)["run_id"]
        manifest, bundle, report, data = load_run(cid, run_id)
        result = training.shadow_forecast(match, detail, now, artifacts=(bundle, report, data))
        result.update(competition_id=cid, run_id=run_id, training_scope="competition_only")
        return result
    except (ValueError, OSError, KeyError, TypeError):
        return unavailable("该赛事模型或数据版本不可用")


def status(competition_id):
    spec = competition_spec(competition_id)
    root = directory(competition_id)
    value = {"competition_id": competition_id, "dataset_available": (root / "dataset.json").exists(), "ready": False,
        "reason": "尚无本赛事训练数据", "rows": 0, "leagues": [competition_id], "coverage": [],
        "season_coverage": [], "evaluation": None, "model": None, "candidate": None, "runs": [],
        "training_scope": "competition_only", "blockers": [], "usage_mode": None}
    try:
        if (root / "dataset.json").exists():
            data = dataset(competition_id)
            rows = data["matches"]
            value.update(dataset_available=True, rows=len(rows), dataset_digest=fingerprint(data),
                         usage_mode=data["manifest"].get("usage_mode", "licensed"),
                         availability_policy=data["manifest"].get("availability_policy"))
            value["season_coverage"] = [{"id": competition_id+":"+season, "competition_id":competition_id,
                "season":season, "imported":sum(r["season"] == season for r in rows)} for season in sorted({r["season"] for r in rows})]
            value["coverage"] = [{"id":competition_id, "matches":len(rows), "seasons":len(value["season_coverage"]),
                "half_matches":sum(r.get("home_half_goals") is not None for r in rows),
                "odds_matches":sum(bool(r.get("odds")) for r in rows)}]
            training.split_dataset(rows)
            value.update(ready=spec.get("training_enabled", True), reason="可训练与回测")
        if not spec.get("training_enabled", True):
            value.update(ready=False, reason="数据与赛制待接入")
    except (ValueError, OSError, KeyError, TypeError) as exc:
        value.update(ready=False, reason=str(exc))
    for key, file in (("candidate", "candidate.json"), ("model", "observation.json")):
        try:
            if not (root / file).exists() or not read(root / file).get("run_id"):
                continue
            run_id = read(root / file)["run_id"]
            manifest, bundle, report, _ = load_run(competition_id, run_id)
            value[key] = {"id":bundle["id"], "run_id":run_id, "created_at":manifest["created_at"],
                "dataset_digest":manifest["dataset_digest"], "competitions":[competition_id],
                "status":"前瞻观察" if key == "model" else "候选模型",
                "reference_check":training.reference_checks(report)[competition_id]}
            if key == "candidate":
                value["evaluation"] = {k:v for k,v in report.items() if k not in ("records", "models")}
                value["evaluation"]["reference_checks"] = training.reference_checks(report)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            value["blockers"].append(f"{key}：{exc}")
    value["dataset_changed"] = bool(value["model"] and value["model"]["dataset_digest"] != value.get("dataset_digest"))
    for path in sorted((root / "runs").glob("*/manifest.json"), key=lambda p:p.stat().st_mtime, reverse=True):
        try:
            manifest, bundle, report, _ = load_run(competition_id, path.parent.name)
            value["runs"].append({"id":bundle["id"], "run_id":manifest["run_id"], "created_at":manifest["created_at"],
                "dataset_digest":manifest["dataset_digest"], "reference_check":training.reference_checks(report)[competition_id],
                "kind":bundle['models'][competition_id]['kind'],
                "supported_markets":bundle['models'][competition_id].get('supported_markets', ['had','hhad','ttg','crs','hafu']),
                "trained_before":bundle['models'][competition_id]['trained_before'],
                "evaluation_status":report.get('status'),
                "limitations":report.get('limitations', report.get('blockers',[]))})
        except (ValueError, OSError, KeyError, TypeError):
            value["blockers"].append("一个历史模型版本校验失败")
    return value
