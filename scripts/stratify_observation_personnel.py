"""Descriptive fixed strata; no stratum selection, tuning or promotion."""
import hashlib,json,sys
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from worldcup_mvp.console import model_registry
from worldcup_mvp.console.observation_personnel import personnel_features
from worldcup_mvp.console.observation_personnel_models import load,OUT
from worldcup_mvp.console.observation_features import instant
from evaluate_observation_personnel import metrics


def main():
    path=OUT/'ablation-2025.json';raw=path.read_bytes();report=json.loads(raw)
    archive=OUT/'archive'/('ablation-2025-'+hashlib.sha256(raw).hexdigest()+'.json')
    if not archive.exists():archive.write_bytes(raw)
    protocol={'defined_at':datetime.now(timezone.utc).isoformat(),'usage':'descriptive_after_overall_results_no_selection_or_tuning',
        'early_season':'at_least_one_team_has_fewer_than_5_available_matches_in_this_season',
        'rotation':'at_least_one_team_changed_4_or_more_starters_between_last_two_observed_matches',
        'coach_change':'at_least_one_team_has_observed_same_namespace_coach_change_in_last_two_reports',
        'promotion':'unknown_without_verified_season_admission_and_promotion_rules',
        'market_scoring':'each_market_only_its_own_valid_cohort_coverage_reported_against_all_stratum_targets'}
    report['descriptive_strata_protocol']=protocol
    md=['','## 描述性分组：覆盖、误差与运行缺失','',
        '在整体结果已知后固定以下描述口径，再一次性输出全部分组；不是新的预登记前瞻检验，不用最佳分组挑模型。新赛季=至少一队该赛季已有可用比赛少于5场；轮换突变=最近两场已观察首发至少换4人；教练变化=最近两份同来源教练身份变化。缺事实单列未知。升班马没有独立核实赛季准入规则，本次全部保留未知，不用缺席上一季样本冒充晋级事实。','',
        '| 联赛 | 维度/组 | 目标 | 共同预测 | 覆盖率 | DC LL | 人员教练交锋负荷 LL |','|---|---|---:|---:|---:|---:|---:|']
    for cid,league in report['leagues'].items():
        rows=model_registry.dataset(cid,admission=False)['matches']
        artifact=load(league['full_candidates']['xg_personnel_coach_h2h_clubload']['path'],cid)
        history=load(artifact['personnel_reference'],cid)
        personteams=defaultdict(list);teamgames=defaultdict(list)
        for r in history['rows']:personteams[r['team_id']].append(r)
        for r in rows:
            for side in ('home','away'):teamgames[r[side+'_id']].append(r)
        targets=[r for r in rows if instant(r['kickoff'])>=instant(report['cutoff'])]
        recordmap={r['id']:r for r in league['records']};groups=defaultdict(lambda:defaultdict(list));membership={}
        for target in targets:
            from datetime import timedelta
            at=instant(target['kickoff'])-timedelta(hours=1)
            counts=[sum(str(r['season'])==str(target['season']) and instant(r['kickoff'])<at and instant(r['result_available_at'])<=at for r in teamgames[target[s+'_id']]) for s in ('home','away')]
            labels={'season_phase':'first_5_available_games' if min(counts)<5 else 'later_season', 'promotion_status':'unknown'}
            try:
                p,refs=personnel_features({**history,'rows':personteams[target['home_id']]+personteams[target['away_id']]},target,at,historical=True)
                labels['rotation']='four_or_more_changed' if max(v['last_rotation'] for v in p)>=4 else 'under_four_changed'
                labels['coach']='observed_change' if any(v['observed_coach_change'] for v in p) else 'unknown' if any(v['coach_comparison_missing'] for v in p) else 'no_observed_change'
            except ValueError:labels.update(rotation='unknown',coach='unknown')
            membership[target['id']]=labels
            for dimension,label in labels.items():groups[dimension][label].append(target['id'])
        strat={}
        for dimension,buckets in groups.items():
            strat[dimension]={}
            for label,ids in sorted(buckets.items()):
                records=[recordmap[i] for i in ids if i in recordmap]
                one={'target_count':len(ids),'common_forecast_count':len(records),'missing_count':len(ids)-len(records),
                     'common_coverage':len(records)/len(ids),'fixture_ids':ids,'metrics':{}}
                for market in ('had','hhad','ttg','crs','hafu'):
                    one['metrics'][market]={s:metrics(records,s,market) for s in league['metrics'][market]}
                    one['metrics'][market]['coverage_over_all_targets']=one['metrics'][market]['dc']['n']/len(ids)
                strat[dimension][label]=one
                b=one['metrics']['had']['dc'];e=one['metrics']['had']['xg_personnel_coach_h2h_clubload']
                bll=f"{b['log_loss']:.4f}" if b['n'] else '—';ell=f"{e['log_loss']:.4f}" if e['n'] else '—'
                md.append(f"| {cid} | {dimension}/{label} | {len(ids)} | {len(records)} | {one['common_coverage']:.1%} | {bll} | {ell} |")
        league['descriptive_strata']=strat;league['target_strata_membership']=membership
        print(cid,{k:{g:(v['target_count'],v['common_forecast_count']) for g,v in b.items()} for k,b in strat.items()},flush=True)
    raw=json.dumps(report,ensure_ascii=False,indent=2).encode()
    archive=OUT/'archive'/('ablation-2025-'+hashlib.sha256(raw).hexdigest()+'.json')
    if not archive.exists():archive.write_bytes(raw)
    temp=OUT/'ablation-2025.json.tmp';temp.write_bytes(raw);temp.replace(path)
    doc=Path('docs/reports/2026-11-09-observation-personnel-ablation.md')
    before=doc.read_text(encoding='utf-8').split('\n## 描述性分组：')[0]
    doc.write_text(before+'\n'.join(md)+'\n',encoding='utf-8')


if __name__=='__main__':main()
