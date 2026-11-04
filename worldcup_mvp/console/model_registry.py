"""Immutable, competition-owned datasets, candidate runs and observation pointers."""
from contextlib import contextmanager
from contextvars import ContextVar
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
_validated_reads = ContextVar('football_validated_reads', default=None)


@contextmanager
def validated_read_scope():
    """A read-only page request may reuse its fully validated artifact snapshot.

    Never retained between requests or used by prediction/selection writes.
    Each distinct registry, competition and run is still independently checked.
    """
    token = _validated_reads.set({})
    try:
        yield
    finally:
        _validated_reads.reset(token)

MANIFEST_FIELDS = ("usage_mode", "local_training_authorized", "authorization_reference",
    "rights_status", "commercial_use_permitted", "training_permitted", "license_reference",
    "source_url", "availability_policy", "quality_basis", "source_receipt", "source_receipts",
    "sample_policy", "continuity_policy", "excluded", "unique_source_events", "prepared_at",
    "source_digest", "source_version", "source_manifest", "raw_digest", "coverage_denominator",
    "source_competition", "source_competitions", "qualifying_enabled", "collection_status", "collection_errors")


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


def validate_scope(data, competition_id, *, admission=True):
    spec = competition_spec(competition_id)
    if admission and spec.get("training_enabled", True) is not True:
        raise ValueError("该赛事尚未完成数据与规则接入")
    if data.get("manifest", {}).get("competition_id") != competition_id or data["manifest"].get("training_scope") != "competition_only":
        raise ValueError("训练数据缺少独立赛事范围")
    if any(r.get("competition_id") != competition_id for r in data.get("matches", [])):
        raise ValueError("训练数据混入其他赛事")
    if any(r.get("competition_id") != competition_id for r in data.get("team_mappings", [])):
        raise ValueError("球队映射混入其他赛事")
    rows = training.validate_dataset(data)
    policy = spec.get("training_stage_policy", {})
    if admission and any(row["stage"] not in policy.get(row["season"], []) for row in rows):
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


def import_batch(data, competition_id=None):
    """Explicit legacy batch migration; report every independent admission result."""
    if not isinstance(data, dict) or not isinstance(data.get("matches"), list):
        raise ValueError("训练数据格式无效")
    ids = sorted({str(row.get("competition_id") or "") for row in data['matches'] if isinstance(row, dict)})
    if competition_id is not None:
        ids = [competition_id]
    partitions = []
    for cid in ids:
        try:
            partitions.append({**import_competition(data, cid), "status": "imported"})
        except (ValueError, KeyError, TypeError, OSError) as exc:
            partitions.append({"competition_id": cid, "status": "rejected", "rows": 0, "error": str(exc)})
    return {"partitions": partitions, "imported": sum(p['status'] == 'imported' for p in partitions),
            "rejected": sum(p['status'] == 'rejected' for p in partitions)}


def dataset(competition_id, digest=None, *, admission=True):
    root = directory(competition_id)
    digest = digest or read(root / "dataset.json")["digest"]
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("数据版本无效")
    value = read(root / "datasets" / (digest + ".json"))
    if fingerprint(value) != digest:
        raise ValueError("训练数据版本校验失败")
    validate_scope(value, competition_id, admission=admission)
    return value


@contextmanager
def training_lock(competition_id, name="training"):
    if name not in ("training", "selection"):
        raise ValueError("操作锁无效")
    path = directory(competition_id) / (name + ".lock")
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
            raise ValueError("该赛事已有训练任务" if name == "training" else "模型选择正在更新") from None
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
    cache = _validated_reads.get()
    if cache is None:
        return _load_run(competition_id, run_id)
    key = (str(REGISTRY.resolve()), competition_id, run_id)
    if key not in cache:
        cache[key] = _load_run(competition_id, run_id)
    return cache[key]


def _load_run(competition_id, run_id):
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
    data = dataset(competition_id, manifest["dataset_digest"], admission=False)
    if bundle.get('cold_start_policy'):
        from .cold_start import validate_binding
        validate_binding(bundle, report)
        policy = bundle['cold_start_policy']
        report_id = policy['report_id']
        if not isinstance(report_id,str) or not re.fullmatch(r'[0-9a-f]{32}',report_id):
            raise ValueError('冷启动报告标识无效')
        evidence = read(directory(competition_id)/'reports'/(report_id+'.json'))
        if (evidence.get('id') != report_id or evidence.get('competition_id') != competition_id
            or evidence.get('digest') != fingerprint({k:v for k,v in evidence.items() if k!='digest'})
            or evidence['digest'] != policy['report_digest']
            or evidence.get('dataset_digest') != bundle['dataset_digest']
            or evidence.get('admission') != {k:v for k,v in policy.items() if k not in ('report_id','report_digest','dataset_digest')}):
            raise ValueError('冷启动评估证据缺失或已变更')
    return manifest, bundle, report, data


def select_observation(competition_id, run_id, expected_revision=None):
    manifest = load_run(competition_id, run_id)[0] if run_id is not None else None
    root = directory(competition_id)
    with training_lock(competition_id, "selection"):
        path = root / "observation.json"
        prior = read(path) if path.exists() else {}
        revision = prior.get("revision", 0)
        if expected_revision is not None and expected_revision != revision:
            raise ValueError("模型选择已更新，请刷新后重试")
        selection = {"run_id": run_id, "competition_id": competition_id,
                     "selected_at": now_iso(), "revision": revision + 1,
                     "model_id": manifest["model_id"] if manifest else None}
        training.atomic_json(root / "selections" / (uuid4().hex + ".json"),
                             {"previous": prior, "selection": selection})
        training.atomic_json(path, selection)
        return selection


def selection(competition_id):
    path = directory(competition_id) / "observation.json"
    value = read(path) if path.exists() else {"run_id": None, "model_id": None}
    return {**value, "competition_id": competition_id, "revision": value.get("revision", 0)}


def commit_lock(competition_id):
    return training_lock(competition_id, "selection")


def supported_markets(model, competition_id):
    if model.get("supported_markets") is not None:
        return model["supported_markets"]
    if model.get("kind") in ("elo_logistic", "catboost"):
        return ["had"]
    return ["had", "hhad", "ttg", "crs"] + (["hafu"] if
        model.get("half_shares", {}).get(competition_id) or model.get("half_share") else [])


def evaluation_reports(competition_id):
    reports = []
    for path in (directory(competition_id) / "reports").glob("*.json"):
        report = read(path)
        if report.get("competition_id") != competition_id or report.get("digest") != fingerprint({k:v for k,v in report.items() if k != "digest"}):
            raise ValueError("评估报告归属或版本校验失败")
        reports.append(report)
    return sorted(reports, key=lambda r:r["created_at"], reverse=True)


def report_summary(report):
    value = {k:v for k,v in report.items() if k not in ('records','warm_records','models')}
    if 'candidates' in value:
        value['candidates'] = [{k:v for k,v in c.items() if k != 'validation_records'} for c in value['candidates']]
    for key in ('selected',):
        if isinstance(value.get(key), dict):
            value[key] = {k:v for k,v in value[key].items() if k != 'validation_records'}
    if isinstance(value.get('selected_by_competition'), dict):
        value['selected_by_competition'] = {cid:{k:v for k,v in config.items() if k != 'validation_records'}
                                          for cid,config in value['selected_by_competition'].items()}
    value['capabilities_verified'] = report.get('capabilities_verified', False)
    value['feature_metadata_verified'] = bool(report.get('feature_metadata') or report.get('feature_metadata_by_candidate'))
    value['artifact_replay_available'] = bool(report.get('records')) and all(r.get('artifact_ref') for r in report['records'])
    return value


def forecast(match, detail, now=None, allow_network=True):
    from .catalog import classify
    cid = classify(match)["id"]
    unavailable = lambda reason: {"status": "unavailable", "reason": reason, "markets": [], "competition_id": cid}
    try:
        if classify(match).get("season") is None:
            return unavailable("该比赛赛季尚未登记")
        pointer = selection(cid)
        if not pointer.get("run_id"):
            return unavailable("该赛事尚未选用观察模型")
        run_id = pointer["run_id"]
        manifest, bundle, report, data = load_run(cid, run_id)
        result = training.shadow_forecast(match, detail, now, artifacts=(bundle, report, data), allow_network=allow_network)
        result.update(competition_id=cid, run_id=run_id, training_scope="competition_only",
                      selection_revision=pointer["revision"], rule_revision=fingerprint(competition_spec(cid)),
                      supported_markets=supported_markets(bundle["models"][cid], cid))
        return result
    except (ValueError, OSError, KeyError, TypeError):
        return unavailable("该赛事模型或数据版本不可用")


def status(competition_id):
    spec = competition_spec(competition_id)
    root = directory(competition_id)
    value = {"competition_id": competition_id, "dataset_available": (root / "dataset.json").exists(), "ready": False,
        "reason": "尚无本赛事训练数据", "rows": 0, "leagues": [competition_id], "coverage": [],
        "season_coverage": [], "evaluation": None, "current_evaluation": None,
        "model": None, "candidate": None, "runs": [], "reports": [],
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
                "reference_check":training.reference_checks(report).get(competition_id),
                "supported_markets":supported_markets(bundle['models'][competition_id], competition_id),
                "trained_before":bundle['models'][competition_id]['trained_before'],
                "kind":bundle['models'][competition_id]['kind'],
                "selection_revision":selection(competition_id)["revision"] if key == "model" else None}
            if key == "model":
                value["current_evaluation"] = (report_summary(report)
                    if report.get("evaluation_type") == "fixed_model" else None)
                if report.get("evaluation_type") != "unassessed_artifact":
                    value["current_strategy_evaluation"] = {**report_summary(report),
                                                           "evaluation_type": "rolling_strategy"}
            if key == "candidate":
                value["evaluation"] = report_summary(report)
                value["evaluation"]["reference_checks"] = training.reference_checks(report)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            value["blockers"].append(f"{key}：{exc}")
    value["dataset_changed"] = bool(value["model"] and value["model"]["dataset_digest"] != value.get("dataset_digest"))
    try:
        reports = evaluation_reports(competition_id)
        value["reports"] = [report_summary(report) for report in reports]
        if value["model"]:
            current = next((report for report in value["reports"] if report.get("run_id") == value["model"]["run_id"]
                            and report.get("model_id") == value["model"]["id"] and report.get("evaluation_type") == "fixed_model"), None)
            if current:
                value["current_evaluation"] = current
    except (ValueError, OSError, KeyError, TypeError) as exc:
        value["blockers"].append(str(exc))
    for path in sorted((root / "runs").glob("*/manifest.json"), key=lambda p:p.stat().st_mtime, reverse=True):
        try:
            manifest, bundle, report, _ = load_run(competition_id, path.parent.name)
            value["runs"].append({"id":bundle["id"], "run_id":manifest["run_id"], "created_at":manifest["created_at"],
                "dataset_digest":manifest["dataset_digest"], "reference_check":training.reference_checks(report).get(competition_id),
                "kind":bundle['models'][competition_id]['kind'],
                "supported_markets":supported_markets(bundle['models'][competition_id], competition_id),
                "trained_before":bundle['models'][competition_id]['trained_before'],
                "evaluation_status":report.get('status'),
                "limitations":report.get('limitations', report.get('blockers',[]))})
        except (ValueError, OSError, KeyError, TypeError):
            value["blockers"].append("一个历史模型版本校验失败")
    return value
