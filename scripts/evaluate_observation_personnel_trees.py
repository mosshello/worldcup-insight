"""Fixed-parameter tree candidates on the exact personnel ablation cohort."""
import json,sys,hashlib
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from worldcup_mvp.console import model_registry
from worldcup_mvp.console.store import ROOT,Store
from worldcup_mvp.console.observation_features import instant
from worldcup_mvp.console.observation_personnel_models import load,prepare_samples,immutable
from worldcup_mvp.console.observation_personnel_trees import fit,probabilities,forecast,CONFIGS
from evaluate_observation_personnel import metrics

OUT=ROOT/'data/research/observation-personnel-trees'


def main():
    prior=json.loads((ROOT/'data/research/observation-personnel-models/ablation-2025.json').read_text(encoding='utf-8'))
    report={'schema':'personnel-tree-evaluation-v1','created_at':datetime.now(timezone.utc).isoformat(),'cutoff':prior['cutoff'],
        'full_fit_cutoff':prior['full_fit_cutoff'],'fixed_configs':CONFIGS,'limitations':prior['limitations'],
        'prior_report_sha256':hashlib.sha256((ROOT/'data/research/observation-personnel-models/ablation-2025.json').read_bytes()).hexdigest(),
        'leagues':{}}
    index={};OUT.mkdir(parents=True,exist_ok=True)
    with Store().connect() as db:fixtures=[json.loads(r['payload']) for r in db.execute('SELECT payload FROM observation_fixtures')]
    smoke={'as_of':datetime.now(timezone.utc).isoformat(),'leagues':{},'excluded':[]}
    for cid,old in prior['leagues'].items():
        base=load(old['full_candidates']['xg_personnel_coach_h2h_clubload']['path'],cid)
        personnel=load(base['personnel_reference'],cid);club=load(base['club_reference'],cid)
        rows=model_registry.dataset(cid,admission=False)['matches']
        samples,excluded,history=prepare_samples(rows,personnel,club)
        samplemap={s['id']:s for s in samples}
        frozen=load(old['retrospective_artifacts']['xg_personnel_coach_h2h_clubload'],cid)
        actualtrain=[s['id'] for s in samples if instant(s['available_at'])<=instant(prior['cutoff'])]
        if actualtrain!=frozen['train_ids']:raise ValueError('树模型训练集合与已冻结消融不同')
        reference=immutable({'schema':'frozen-personnel-feature-matrix-v1','competition_id':cid,
            'samples':samples,'exclusions':excluded,'history_reference':base['history_reference'],
            'personnel_reference':base['personnel_reference'],'club_reference':base['club_reference']},OUT/cid/'inputs')
        refs={'features':reference,'history':base['history_reference'],'personnel':base['personnel_reference'],'club':base['club_reference']}
        test={};full={};paths={};index[cid]={}
        for kind in CONFIGS:
            test[kind]=fit(samples,cid,prior['cutoff'],kind,refs)
            paths[kind]=immutable(test[kind],OUT/cid/'models')
            fullpath=immutable(fit(samples,cid,prior['full_fit_cutoff'],kind,refs),OUT/cid/'models')
            full[kind]=load(fullpath,cid);index[cid][kind]={'path':fullpath,'train_count':full[kind]['train_count'],'supported_markets':full[kind]['supported_markets']}
        records=[]
        for oldrow in old['records']:
            s=samplemap[oldrow['id']]
            preds={'dc':oldrow['predictions']['dc'],'linear_personnel':oldrow['predictions']['xg_personnel_coach_h2h_clubload']}
            preds.update({kind:probabilities(a,s['vectors'],oldrow['line']) for kind,a in test.items()})
            records.append({k:oldrow[k] for k in ('id','kickoff','as_of','line','actual')}|{'predictions':preds})
        sources=['dc','linear_personnel',*CONFIGS]
        stats={m:{s:metrics(records,s,m) for s in sources} for m in ('had','hhad','ttg','crs','hafu')}
        strata={}
        for dimension,groups in old.get('descriptive_strata',{}).items():
            strata[dimension]={}
            for label,group in groups.items():
                matching=[r for r in records if r['id'] in set(group['fixture_ids'])]
                strata[dimension][label]={'target_count':group['target_count'],'common_count':len(matching),
                    'coverage':len(matching)/group['target_count'],
                    'metrics':{m:{s:metrics(matching,s,m) for s in sources} for m in stats}}
        report['leagues'][cid]={'train_count':len(actualtrain),'full_fit_count':full['catboost_had']['train_count'],
            'feature_exclusions':excluded,'common_count':len(records),'target_count':old['test_targets'],
            'retrospective_artifacts':paths,'full_candidates':index[cid],'metrics':stats,'descriptive_strata':strata,'records':records}
        for fixture in sorted([f for f in fixtures if f['competition_id']==cid and instant(f['kickoff'])>instant(smoke['as_of'])],key=lambda f:f['kickoff']):
            try:result={kind:forecast(a,fixture,smoke['as_of']) for kind,a in full.items()}
            except ValueError as exc:smoke['excluded'].append({'id':fixture['id'],'reason':str(exc)});continue
            smoke['leagues'][cid]={'fixture':fixture,'results':result};break
        print(cid,'common',len(records),'HDA',json.dumps({s:{'accuracy':round(m['accuracy'],5),'log_loss':round(m['log_loss'],5)} for s,m in stats['had'].items()}),flush=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    for name,value in (('evaluation',report),('candidate-index',index),('live-smoke',smoke)):
        text=json.dumps(value,ensure_ascii=False,indent=2)
        (OUT/(name+'-'+stamp+'.json')).write_text(text,encoding='utf-8')
        temp=OUT/(name+'.json.tmp');temp.write_text(text,encoding='utf-8');temp.replace(OUT/(name+'.json'))
    doc=['# 人员增强 CatBoost 与 LightGBM：独立固定参数复核','',
        '新增独立特征版本xg-personnel-coach-h2h-clubload-v1及原生JSON/文本模型。每个联赛单独拟合，训练截止2025-07-01；和此前人员/教练/交锋/跨赛事球队负荷线性Poisson使用完全相同训练ID与共同测试比赛。测试期已被研究，是探索性历史复核，不是前瞻认证。','',
        'CatBoost只输出胜平负；LightGBM分别使用两个球队向量预测进球强度，再转成统一比分分布。半全场沿用训练样本半场份额，不冒充新动态半场模型；没有真实盘口线不生成让球。','',
        '| 联赛 | 玩法 | 同场 | DC准确率 | 线性增强准确率 | CatBoost准确率 | LightGBM准确率 | DC LL | 线性 LL | CatBoost LL | LightGBM LL |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for cid,league in report['leagues'].items():
        for market,stats in league['metrics'].items():
            vals=[stats[s] for s in sources]
            accuracy=[f"{v['accuracy']:.2%}" if v['n'] else '不支持' for v in vals]
            ll=[f"{v['log_loss']:.4f}" if v['n'] else '—' for v in vals]
            doc.append('| '+cid+' | '+market+' | '+str(vals[0]['n'])+' | '+' | '.join(accuracy+ll)+' |')
    doc+=['','新赛季、换帅、轮换突变及未知晋级状态的全部分组覆盖和五玩法误差保存在evaluation.json，沿用此前固定描述口径，未选最好分组或调参。','',
        '实际参数为CatBoost 200棵、深度4、学习率0.03；LightGBM Poisson 200棵、深度4、15叶、学习率0.03、最少40条叶样本、L2=5；固定随机种子20261109。全量候选纳入各联赛截止前全部合格特征目标，不自动改变生产选用。',
        '','路径：`data/research/observation-personnel-trees/candidate-index.json`、`evaluation.json`、`live-smoke.json`。每份原生模型、特征矩阵与历史来源均内容寻址，候选支持范围独立记录。']
    (ROOT/'docs/reports/2026-11-09-personnel-tree-evaluation.md').write_text('\n'.join(doc)+'\n',encoding='utf-8')


if __name__=='__main__':main()
