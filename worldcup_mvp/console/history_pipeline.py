"""Season result collection into quarantine, with explicit admission to training."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

from .history_audit import Archive, Document, score
from .history_j_special import verify_special
from .store import ROOT, fingerprint
from .training import atomic_json, validate_dataset, import_dataset

STAGING = ROOT / "data/training/history_staging.json"

def competition_staging(competition_id):
    from .catalog import catalog
    if competition_id not in {c["id"] for c in catalog()}:
        raise ValueError("请指定已登记的赛事")
    return STAGING.with_name("history_staging." + competition_id + ".json")


def split_staging(data, competition_id):
    """One-time migration of a verified aggregate snapshot; subsequent collection is scoped."""
    if data.get("digest") != fingerprint({"matches": data["matches"], "coverage": data["coverage"]}) or data.get("fixture_digest") != fingerprint(data.get("fixtures", [])):
        raise ValueError("原始采集版本校验失败")
    value = {**data, "competition_id":competition_id}
    for key in ("matches", "coverage", "sources", "fixtures", "excluded"):
        value[key] = [r for r in data.get(key, []) if r.get("competition_id") == competition_id]
    value["digest"] = fingerprint({"matches":value["matches"], "coverage":value["coverage"]})
    value["fixture_digest"] = fingerprint(value["fixtures"])
    atomic_json(competition_staging(competition_id),value)
    return value

# IDs discovered in the provider's season navigation. Special J1 is a separate
# season with regional and playoff stages and bounded official period checks.
SEASONS = [
    ("jp.j1", "2023", "6773", 306, 18, 34),
    ("jp.j1", "2024", "7125", 380, 20, 38),
    ("jp.j1", "2025", "7372", 380, 20, 38),
    ("jp.j1", "2026-special", "19524", 200, 20, 20),
    ("jp.j1", "2026-27", "19895", None, 20, None),
    ("kr.k1", "2023", "6781", 228, 12, 38),
    ("kr.k1", "2024", "7105", 228, 12, 38),
    ("kr.k1", "2025", "7396", 228, 12, 38),
    ("kr.k1", "2026", "19554", None, 12, None),
]
STAGES = {"联赛": "regular", "联赛赛程": "regular", "冠军赛": "final_a",
          "冠军附加": "final_a", "降级赛": "final_b", "降级附加": "final_b"}
SPECIAL_STAGES = {"西区": "special_west", "东区": "special_east", "决赛": "playoff_1_2",
                  **{f"{n}-{n+1}排名赛": f"playoff_{n}_{n+1}" for n in range(3, 20, 2)}}


def stage_rounds(html, playoff=False):
    m = re.search(r"allRounds\s*=\s*(\[[\d,\s]*\])", html)
    if not m:
        raise ValueError("缺少轮次定义")
    values = json.loads(m[1])
    if playoff and values == [] and re.search(r"roundType\s*=\s*['\"]other['\"]", html):
        return [0]
    if not values or len(values) > 50 or len(set(values)) != len(values) or any(type(x) is not int or not 1 <= x <= 50 for x in values):
        raise ValueError("轮次定义无效")
    return values


def normalize(row, competition, season, stage, source_url, collected_at):
    if str(row.get("status")) != "5":
        return None
    for field in ("fid", "hid", "gid"):
        if not str(row.get(field, "")).isdigit():
            raise ValueError("缺少比赛或球队标识")
    if str(row["hid"]) == str(row["gid"]):
        raise ValueError("主客队重复")
    full = score(f"{row['hscore']}-{row['gscore']}")
    half = [None, None]
    if row.get("hhalfscore") not in (None, "") and row.get("ghalfscore") not in (None, ""):
        half = score(f"{row['hhalfscore']}-{row['ghalfscore']}")
        if any(h > f for h, f in zip(half, full)):
            raise ValueError("半场比分超过全场")
    kickoff = datetime.fromisoformat(row["stime"] + ":00+08:00")
    year_parts = season.removesuffix("-special").split("-")
    if kickoff.year not in {int(y) if len(y) == 4 else 2000 + int(y) for y in year_parts}:
        raise ValueError("比赛年份与赛季不符")
    if season == "2026-special" and (competition != "jp.j1" or stage not in SPECIAL_STAGES.values() or not 2 <= kickoff.month <= 6):
        raise ValueError("百年构想赛阶段或日期不符")
    if kickoff > datetime.fromisoformat(collected_at):
        raise ValueError("未来比赛被标为完赛")
    return dict(id="500:" + str(row["fid"]), competition_id=competition, season=season, stage=stage,
                home_id="500:" + str(row["hid"]), away_id="500:" + str(row["gid"]),
                home=row.get("hname"), away=row.get("gname"), kickoff=kickoff.isoformat(),
                kickoff_basis="provider_schedule", period="regular_time", home_goals=full[0], away_goals=full[1],
                home_half_goals=half[0], away_half_goals=half[1], result_available_at=None,
                collected_at=collected_at, source_url=source_url,
                independent_verification="pending", training_eligible=False)


def season_quality(rows, spec, errors):
    competition, season, _, expected, teams, per_team = spec
    counts = Counter(r[s] for r in rows for s in ("home_id", "away_id"))
    issues = list(errors)
    if expected is not None:
        if len(rows) != expected:
            issues.append(f"场次 {len(rows)}/{expected}")
        if len(counts) != teams or any(n != per_team for n in counts.values()):
            issues.append("球队数量或球队场次不符")
    if len(counts) > teams:
        issues.append("球队数量超过赛制")
    timing = sum(bool(r.get("kickoff_review_required")) for r in rows)
    if timing:
        issues.append(f"{timing} 场开赛时间差异待核查")
    if season == "2026-special":
        stage_counts = Counter(r["stage"] for r in rows)
        expected_stages = {s: 90 if s.startswith("special_") else 2 for s in SPECIAL_STAGES.values()}
        if dict(stage_counts) != expected_stages:
            issues.append("百年构想赛分组或排名赛场次不符")
        for stage in ("special_east", "special_west"):
            grouped = [r for r in rows if r["stage"] == stage]
            team_counts = Counter(r[s] for r in grouped for s in ("home_id", "away_id"))
            if len(team_counts) != 10 or any(n != 18 for n in team_counts.values()):
                issues.append(f"{stage} 球队数量或球队场次不符")
    return dict(id=f"{competition}:{season}", competition_id=competition, season=season,
                collected=len(rows), expected=expected, teams=len(counts), team_matches=dict(counts),
                half_matches=sum(r["home_half_goals"] is not None for r in rows),
                verified=sum(r["independent_verification"] == "agree" for r in rows),
                conflicts=sum(r["independent_verification"] == "conflict" for r in rows),
                issues=issues, structure_ok=not issues, status="待核查" if issues else "待入库")


def attach_audit(rows, audit_path):
    if not audit_path.exists():
        return
    audit = json.loads(audit_path.read_text(encoding="utf8"))
    by_id = {r["id"]: r for r in rows}
    for sample in audit.get("matches", []):
        check = sample.get("cross_check", {})
        r = by_id.get("500:" + str(check.get("provider_event_id")))
        if not r:
            continue
        same = r["competition_id"] == sample["competition_id"] and r["season"] == sample["season"]
        same = same and r["home_id"] == "500:" + str(check.get("provider_home_id")) and r["away_id"] == "500:" + str(check.get("provider_away_id"))
        same = same and all(r[k] == sample.get(k) for k in ("home_goals", "away_goals", "home_half_goals", "away_half_goals"))
        r["independent_verification"] = "agree" if same else "conflict"
        r["verification_url"] = sample["source_url"]
        r["kickoff_review_required"] = bool(check.get("kickoff_review_required"))


def collect(output=STAGING, offline=False, progress=None, specs=SEASONS):
    output = Path(output)
    # Immutable snapshots per UTC day for current seasons; history is reusable.
    raw_root = output.parent / "history_raw"
    history = Archive(raw_root / "completed_seasons", offline=offline)
    current = Archive(raw_root / datetime.now(timezone.utc).strftime("%Y-%m-%d"), offline=offline)
    all_rows, coverage, sources, fixtures = [], [], [], []
    audit_path = output.parent / "source_audit/report.json"
    for spec in specs:
        competition, season, sid, expected, *_ = spec
        archive = history if expected else current
        url = f"https://liansai.500.com/zuqiu-{sid}/"
        rows, errors, seen = [], [], set()
        try:
            root = Document(archive.fetch(url)).root
            title = root.find("title")[0].text
            title_season = season.removesuffix("-special")
            if title_season.replace("-", "/20") not in title and title_season not in title:
                raise ValueError("赛季页面标题不匹配")
            paths = {a.attrs.get("href") for a in root.find("a") if "完整赛程" in a.text}
            if len(paths) != 1:
                raise ValueError("赛程入口不唯一")
            schedule_url = urljoin(url, paths.pop())
            html = archive.fetch(schedule_url)
            root = Document(html).root
            stage_names = SPECIAL_STAGES if season == "2026-special" else STAGES
            stages = [(stage_names[a.text], urljoin(url, a.attrs["href"]), a.attrs["data-id"])
                      for a in root.find("a") if a.text in stage_names and a.attrs.get("data-id") and "/jifen-" in a.attrs.get("href", "")]
            if competition == "jp.j1" and season != "2026-special":
                stid = re.search(r"stageId\s*=\s*(\d+)", html)[1]
                stages = [("regular", schedule_url, stid)]
            if not stages or len({s[0] for s in stages}) != len(stages):
                raise ValueError("阶段定义缺失或重复")
            for stage, stage_url, stid in stages:
                stage_html = html if stage_url == schedule_url else archive.fetch(stage_url)
                for round_id in stage_rounds(stage_html, playoff=stage.startswith("playoff_")):
                    api = f"https://liansai.500.com/index.php?c=score&a=getmatch&stid={stid}&round={round_id}"
                    try:
                        payload = json.loads(archive.fetch(api))
                        if not isinstance(payload, list):
                            raise ValueError("轮次数据格式无效")
                        for item in payload:
                            if expected is None:
                                start = datetime.fromisoformat(item["stime"] + ":00+08:00")
                                if not all(str(item.get(k, "")).isdigit() for k in ("fid", "hid", "gid")):
                                    raise ValueError("赛程身份无效")
                                years = {int(y) if len(y) == 4 else 2000+int(y) for y in season.split("-")}
                                if start.year not in years or str(item['hid']) == str(item['gid']):
                                    raise ValueError("赛程年份或球队不符")
                                fixtures.append(dict(id="500:"+str(item['fid']), competition_id=competition,
                                    season=season, stage=stage, round=item.get('round'), kickoff=start.isoformat(),
                                    home=item['hname'], away=item['gname'], home_id="500:"+str(item['hid']),
                                    away_id="500:"+str(item['gid']), source_url=api,
                                    source_sha256=archive.entries[-1].get('sha256'), collected_at=archive.entries[-1]['collected_at']))
                            row = normalize(item, competition, season, stage, api, archive.entries[-1]["collected_at"])
                            if row is None:
                                continue
                            row["source_sha256"] = archive.entries[-1].get("sha256")
                            if row["id"] in seen:
                                raise ValueError("同一赛季比赛重复")
                            seen.add(row["id"])
                            rows.append(row)
                    except Exception as exc:
                        errors.append(f"{stage}/{round_id}: {exc}")
            attach_audit(rows, audit_path)
            if season == "2026-special":
                errors.extend(verify_special(rows, history))
        except Exception as exc:
            errors.append(str(exc))
        coverage.append(season_quality(rows, spec, errors))
        sources.append({"competition_id": competition, "season": season, "url": url})
        all_rows.extend(rows)
        result = dict(schema="club-history-staging-v1", collected_at=datetime.now(timezone.utc).isoformat(),
                      status="collecting", rights_status="unverified", sources=sources,
                      matches=all_rows, coverage=coverage, fixtures=fixtures, fixture_digest=fingerprint(fixtures),
                      excluded=[])
        atomic_json(output, result)
        print(f"{competition} {season}: {len(rows)} matches; {len(errors)} errors", flush=True)
        if progress:
            progress(coverage[-1])
    result["status"] = "collected"
    result["digest"] = fingerprint({"matches": all_rows, "coverage": coverage})
    atomic_json(output, result)
    write_report(output.with_suffix(".md"), result)
    return result


def write_report(path, data):
    lines = ["# 历史数据采集进度", "", "本报告统计待验收数据，不代表已导入训练或完成授权。", "",
             "| 赛事 | 赛季 | 已采集 | 历史应有场次 | 含半场 | 双源核对 | 状态 |",
             "|---|---|---:|---:|---:|---:|---|"]
    for c in data["coverage"]:
        league = {"jp.j1": "日职", "kr.k1": "韩职"}[c["competition_id"]]
        lines.append(f"| {league} | {c['season']} | {c['collected']} | {c['expected'] or '进行中'} | {c['half_matches']} | {c['verified']} | {c['status']} |")
    lines += ["", "## 待处理", "",
              "- 数据来源：500 的公开赛季及轮次记录。使用依据尚待确认，不把网页公开访问当作训练或商用授权。",
              "- 双源核对覆盖此前 12 场历史样本及百年构想赛的抽查；未核对场次不算官方逐场复核。",
              "- 日职 2026 百年构想赛独立赛季：分组 180 场、排名赛 20 场，90 分钟比分与加时、点球分离；官方样本失败会阻止该赛季入库。",
              "- 汇总赔率未提供观察时间，未纳入训练及收益回测。",
              "- 训练导入支持明确标注的赛果延迟 7 天假设，当前尚未应用；这不等于取得真实历史发布时间。",
              "- 开球时间差异仍需处理。待验收数据不自动导入、训练或切换线上模型。", ""]
    for c in data["coverage"]:
        if c["issues"]:
            lines.append(f"- {c['id']}：" + "；".join(c["issues"]))
    Path(path).write_text("\n".join(lines), encoding="utf8")


def staging_status(path=None):
    path = Path(path or STAGING)
    if not path.exists():
        return {"available": False, "coverage": [], "status": "未采集"}
    try:
        data = json.loads(path.read_text(encoding="utf8"))
        if data["schema"] != "club-history-staging-v1" or not isinstance(data["coverage"], list):
            raise ValueError()
        return {"available": True, "status": data["status"], "collected_at": data["collected_at"],
                "rights_status": data["rights_status"], "coverage": data["coverage"], "excluded": data["excluded"]}
    except (ValueError, OSError, KeyError, TypeError):
        return {"available": False, "coverage": [], "status": "数据文件无效"}


def prepare_import(staging_path, manifest_path, output):
    """Explicit, reviewable admission; no fabricated source permission or timestamp."""
    data = json.loads(Path(staging_path).read_text(encoding="utf8"))
    approval = json.loads(Path(manifest_path).read_text(encoding="utf8"))
    if data.get("digest") != fingerprint({"matches": data["matches"], "coverage": data["coverage"]}):
        raise ValueError("待验收数据摘要不符")
    selected = approval.get("seasons")
    if selected is None:
        selected_coverage = data["coverage"]
    else:
        if not isinstance(selected, list) or not selected or any(not isinstance(s, str) for s in selected) or len(set(selected)) != len(selected):
            raise ValueError("入库赛季选择无效")
        selected_coverage = [c for c in data["coverage"] if c["id"] in selected]
        if len(selected_coverage) != len(selected):
            raise ValueError("入库赛季不在本次采集范围")
    if data.get("status") != "collected" or any(not c["structure_ok"] or c["conflicts"] for c in selected_coverage):
        raise ValueError("采集未完成或存在待核查问题")
    if approval.get("dataset_digest") != data.get("digest") or approval.get("training_permitted") is not True or approval.get("quality_accepted") is not True:
        raise ValueError("使用依据未确认或对应的数据版本不符")
    if not approval.get("license_reference") or not approval.get("source_url"):
        raise ValueError("缺少来源与使用依据")
    if approval.get("availability_policy") != "kickoff_plus_7_days_assumption":
        raise ValueError("须明确历史赛果可用时间假设")
    rows = []
    for original in data["matches"]:
        if selected is not None and original["competition_id"] + ":" + original["season"] not in selected:
            continue
        if original.get("kickoff_review_required"):
            raise ValueError("仍有开赛时间差异未处理")
        row = dict(original)
        available = datetime.fromisoformat(row["kickoff"]) + timedelta(days=7)
        if available > datetime.now(timezone.utc):
            continue
        row.update(result_available_at=available.isoformat(), availability_basis="assumed_7_day_lag", training_eligible=True)
        rows.append(row)
    candidate = {"manifest": approval, "matches": rows, "team_mappings": approval.get("team_mappings", [])}
    validate_dataset(candidate)
    atomic_json(Path(output), candidate)
    return candidate


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--offline", action="store_true")
    p.add_argument("--competition", required=True)
    p.add_argument("--output", type=Path)
    p.add_argument("--manifest", type=Path)
    p.add_argument("--import-prepared", action="store_true")
    args = p.parse_args()
    args.output = args.output or competition_staging(args.competition)
    specs = [s for s in SEASONS if s[0] == args.competition]
    if not specs:
        p.error("该赛事采集适配器未接入")
    if args.manifest:
        prepared = args.output.parent / "club_history.prepared.json"
        prepare_import(args.output, args.manifest, prepared)
        if args.import_prepared:
            from .model_registry import import_competition
            import_competition(json.loads(prepared.read_text(encoding="utf8")), args.competition)
    elif args.import_prepared:
        p.error("--import-prepared requires --manifest")
    else:
        collect(args.output, args.offline, specs=specs)
