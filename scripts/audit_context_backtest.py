"""Independently replay archived context candidates and generate the readable report."""
import argparse
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from research_big_five_context import (
    OPTIONS, VARIANTS, ROOT, PROTOCOL, LEAGUES, read, sha, registry, training, parse_date,
    fit, grid, markets, summarize, metric, year_start, END, save_immutable,
)
import numpy as np
from scipy.special import softmax
from threadpoolctl import threadpool_limits

NAMES={'eng.premier':'英超','esp.laliga':'西甲','ger.bundesliga':'德甲','ita.serie_a':'意甲','fra.ligue1':'法甲'}
LABELS={'had':'胜平负','hhad':'真实整数让球','ttg':'总进球','crs':'比分','hafu':'半全场'}
VERSIONS={'recent_nb':'近期比分','xg':'xG','xg_h2h':'xG+交锋','xg_surprise':'xG+预期偏差','xg_context':'xG+休息/分钟','all':'全部增强'}


def audit(index_path):
    index=read(index_path)
    assert index['protocol_sha256']==sha(PROTOCOL.read_bytes())
    assert index['code_sha256']==sha((ROOT/'scripts/research_big_five_context.py').read_bytes())
    checks={'feature_references':0,'model_refits':0,'forecast_replays':0,'market_replays':0,
            'target_matches':0,'predicted_matches':0,'dataset_matches':0,'common_matches':0}
    reports={}
    for cid,path in index['reports'].items():
        p=ROOT/path
        assert sha(p.read_bytes())==p.stem
        report=read(p)
        assert report['competition_id']==cid
        data=registry.dataset(cid,report['dataset'],admission=False)
        rows=training.validate_dataset(data)
        source={r['id']:r for r in rows}
        from research_big_five_context import checked_lines
        archive,_=checked_lines(cid)
        lines={r['id']:r['hh']['line'] for r in archive['official_rows']}
        checks['dataset_matches']+=len(rows)
        samples=report['samples']
        lookup={s['id']:s for s in samples}
        for s in samples:
            at=parse_date(s['time'])
            from datetime import timedelta
            at-=timedelta(hours=1)
            references=[]
            for side in s['inputs']:
                references+=side['results']+side['xg']+side['minutes']
            for side in s['context_inputs']:
                references+=side['h2h']+side['recent']+side['market']
            for fid in references:
                r=source[fid]
                assert r['competition_id']==cid and fid!=s['id']
                assert parse_date(r['kickoff'])<at and parse_date(r['result_available_at'])<at
                checks['feature_references']+=1
        winner=min(report['validation'],key=lambda k:(report['validation'][k]['log_loss'],k))
        assert report['selected']==winner
        for year,season in report['seasons'].items():
            target={r['id'] for r in rows if int(r['season'])==int(year) and parse_date(r['result_available_at'])<END}
            eligible={s['id'] for s in samples if s['season']==int(year) and parse_date(s['available'])<END}
            excluded={r['id'] for r in season['excluded']}
            assert not eligible & excluded and eligible | excluded==target
            checks['target_matches']+=len(target)
            checks['predicted_matches']+=len(eligible)
            checks['common_matches']+=season['selected_common']['n']
            _,bundle,_,_=registry.load_run(cid,season['dc_run_id'])
            dc=training.GoalModel.from_dict(bundle['models'][cid])
            half=dc.half_shares.get(cid)
            for name,run in season['variants'].items():
                model=run['model']
                refit=fit(samples,name,year_start(int(year)))
                assert refit==model, f'Parameter mismatch {cid}/{year}/{name}'
                checks['model_refits']+=1
                assert {r['id'] for r in run['records']}==eligible
                for record in run['records']:
                    s=lookup[record['id']]
                    # Reconstruct actual handicap from the verified archive, not predictions.
                    actual=markets(grid(model,s),half,lines.get(s['id']))
                    for code,value in actual.items():
                        np.testing.assert_allclose(value,record['probabilities'][code],rtol=1e-12,atol=1e-12)
                    checks['forecast_replays']+=1
                assert summarize(run['records'])==run['metrics']
            dc_records=season['dc']['records']
            dc_ids={r['id'] for r in dc_records}
            common=[r for r in season['variants'][winner]['records'] if r['id'] in dc_ids]
            assert summarize(common)==season['selected_common']['metrics']
            assert summarize(dc_records)==season['dc']['metrics']
        for code,trial in report['market_experiment'].items():
            if trial['status']!='completed':
                continue
            selected=min(trial['validation_candidates'],key=lambda d:(d['validation']['log_loss'],d['weight'],d['c']))
            assert trial['selection']==selected
            boundary=parse_date(trial['validation_boundary'])
            assert not set(trial['selection_train_ids']) & set(trial['validation_ids'])
            for fid in trial['selection_train_ids']:
                assert parse_date(source[fid]['result_available_at'])<boundary
            for fid in trial['train_ids']:
                assert parse_date(source[fid]['result_available_at'])<year_start(2026)
            params=trial['parameters']
            for record in trial['records']:
                assert record['season']==2026 and record['id'] not in trial['train_ids']
                z=(np.array(record['x'])-params['mean'])/params['scale']
                p=softmax(z @ np.array(params['coef']).T+params['intercept'])
                np.testing.assert_allclose(p,record['candidate'],atol=1e-12)
                selected=trial['selection']['weight']*p+(1-trial['selection']['weight'])*np.array(record['market'])
                np.testing.assert_allclose(selected,record['selected'],atol=1e-12)
                checks['market_replays']+=1
            for name in ('market','football','candidate','selected'):
                assert metric([r[name] for r in trial['records']],[r['y'] for r in trial['records']])==trial['metrics'][name]
        reports[cid]=report
        print(cid,'audit passed',flush=True)
    checks['protected_artifacts_unchanged']=index['protected_artifacts_unchanged']
    save_immutable(index_path.parent/'audit.json',checks)
    return reports,checks


def pct(x):
    return '—' if x is None else f'{x*100:.2f}%'


def number(x):
    return '—' if x is None else f'{x:.4f}'


def h2h_cohorts(report,source):
    """Descriptive follow-up only: thresholds were not used for model selection."""
    samples={s['id']:s for s in report['samples']}
    groups={'home_won_last3':[], 'draw3_of5':[]}
    for season in report['seasons'].values():
        for pred in season['dc']['records']:
            sample=samples[pred['id']]
            current=source[sample['id']]
            past=[source[x] for x in sample['context_inputs'][0]['h2h']]
            wins=[(p['home_goals']>p['away_goals']) if p['home_id']==current['home_id']
                  else (p['away_goals']>p['home_goals']) for p in past]
            if len(past)>=3 and all(wins[-3:]):
                groups['home_won_last3'].append({'id':sample['id'],'prior':[p['id'] for p in past[-3:]],
                    'hit':current['home_goals']>current['away_goals'],'baseline_p':pred['probabilities']['had'][0]})
            if len(past)>=5 and sum(p['home_goals']==p['away_goals'] for p in past[-5:])>=3:
                groups['draw3_of5'].append({'id':sample['id'],'prior':[p['id'] for p in past[-5:]],
                    'hit':current['home_goals']==current['away_goals'],'baseline_p':pred['probabilities']['had'][1]})
    from replay_big_five_backtests import accuracy
    return {k:{**accuracy(sum(r['hit'] for r in records),len(records)),
               'baseline_mean_probability':float(np.mean([r['baseline_p'] for r in records])) if records else None,
               'records':records} for k,records in groups.items()}


def write_report(reports,checks,index_path):
    output=ROOT/'docs/reports/2026-11-09-context-backtest-results.md'
    csv_path=output.with_suffix('.csv')
    text=['# 五大联赛交锋、状态与市场增强：实际回测结果','',
          '本轮没有发现可以稳定替换现有模型的增强方案。六种候选在验证期选出的结果，在后续两段历史中的胜平负改善不稳定；市场增强多数被验证期淘汰。保留全部结果，未改生产选用。','',
          f'本轮读取 {checks["dataset_matches"]:,} 场历史，2025/26 与 2026/27 共 {checks["target_matches"]:,} 场目标，增强模型预测 {checks["predicted_matches"]:,} 场，缺少五场可用历史而排除 {checks["target_matches"]-checks["predicted_matches"]} 场。与赛季前固定 DC 模型共有 {checks["common_matches"]:,} 场可比较。数据最新场次及各赛事排除名单见归档。','',
          '[预先固定的研究协议](2026-11-09-context-backtest-protocol.md) · [调研与总体架构](2026-11-09-ai-intelligence-market-research.md)','',
          '注意：此前已经查看过这些历史时期，属于探索性复核；不是全新前瞻测试。结果、xG 和分钟按七天历史发布时间假设，不能证明当时已经采集到。盘口为归档中发布于赛前一小时以前的报价，首次抓取在赛后；不能把历史报价回放称为真实赛前留档。','',
          '## 同场胜平负对照','',
          '两组都每场强制取最大概率一项；不是观望后挑样本。新方案仅按 2024/25 验证 Log loss 选出，测试后不更换。与前一轮滚动训练的测试场次及拟合方式不同，不能直接横比两个报告的百分比。','',
          '| 联赛 | 冻结增强方案 | 2025/26 同场 | 原 DC → 增强 | 2026/27 同场 | 原 DC → 增强 |',
          '|---|---|---:|---:|---:|---:|']
    for cid,r in reports.items():
        a,b=r['seasons']['2025'],r['seasons']['2026']
        text.append(f'| {NAMES[cid]} | {VERSIONS[r["selected"]]} | {a["selected_common"]["n"]} | {pct(a["dc"]["metrics"]["had"]["accuracy"])} → {pct(a["selected_common"]["metrics"]["had"]["accuracy"])} | {b["selected_common"]["n"]} | {pct(b["dc"]["metrics"]["had"]["accuracy"])} → {pct(b["selected_common"]["metrics"]["had"]["accuracy"])} |')
    text+=['','## 最新区间全部玩法','',
           '以下均为每个玩法可评分的共同比赛。比分按体彩 31 个选项，总进球为 0–6 与 7+；括号中为真实样本量。半全场沿用训练期半场份额的条件分配，只是基线时序假设。','',
           '| 联赛 | 胜平负 原→新 | 真实让球 原→新 | 总进球单选 原→新 | 比分单选 原→新 | 半全场单选 原→新 |',
           '|---|---:|---:|---:|---:|---:|']
    for cid,r in reports.items():
        s=r['seasons']['2026']; a=s['dc']['metrics']; b=s['selected_common']['metrics']
        text.append('| '+NAMES[cid]+' | '+' | '.join(f'{pct(a[k]["accuracy"])}→{pct(b[k]["accuracy"])}（{b[k]["n"]}）' for k in OPTIONS)+' |')
    text+=['','### 多选覆盖单列','', '| 联赛 | 进球前二 原→新 | 比分前三 原→新 |', '|---|---:|---:|']
    for cid,r in reports.items():
        s=r['seasons']['2026'];a=s['dc']['metrics'];b=s['selected_common']['metrics']
        text.append(f'| {NAMES[cid]} | {pct(a["ttg"]["top_k_coverage"])}→{pct(b["ttg"]["top_k_coverage"])} | {pct(a["crs"]["top_k_coverage"])}→{pct(b["crs"]["top_k_coverage"])} |')
    text+=['','### 胜平负概率质量与差值区间','',
           'Log loss 越低越好。区间以比赛周为块做 2,000 次配对重采样，差值为增强减原模型；准确率区间单位为百分点。未经多重比较校正，不用个别区间给候选作正式效果认证。','',
           '| 联赛 | 最新 Log loss 原→新 | 准确率差 95% 区间 | Log loss 差 95% 区间 |', '|---|---:|---:|---:|']
    for cid,r in reports.items():
        s=r['seasons']['2026'];a=s['dc']['metrics']['had'];b=s['selected_common']['metrics']['had'];ci=s['paired']['had']
        text.append(f'| {NAMES[cid]} | {number(a["log_loss"])}→{number(b["log_loss"])} | '+ '～'.join(f'{v*100:+.2f}' for v in ci['accuracy_delta95'])+' | '+'～'.join(f'{v:+.4f}' for v in ci['log_loss_delta95'])+' |')
    cohorts={cid:h2h_cohorts(r,{x['id']:x for x in registry.dataset(cid,r['dataset'],admission=False)['matches']})
             for cid,r in reports.items()}
    save_immutable(index_path.parent/'h2h-cohorts.json',{
        'usage':'descriptive_followup_not_preregistered_not_used_for_selection', 'leagues':cohorts})
    text+=['','## 交锋直觉的直接核对（补充描述统计）','',
           '这两个分组是主实验完成后的补充描述，没有用于模型选优或改变预测。限定上面两个测试季的 DC 共同样本、当时已可用的三年交锋历史。连续三胜指当前主队在此前三次直接交锋都获胜，包含双方主客互换；多平指此前五次直接交锋至少三次平局。','',
           '原模型列是对这些特定比赛相应结果的平均预测概率，不是模型单选准确率。只比较命中频率不足以证明交锋在实力或市场之外的增益，更不证明诱盘。','',
           '| 联赛 | 连胜组 下一场主胜/场数 | 原模型平均主胜概率 | 多平组 下一场平局/场数 | 原模型平均平局概率 |',
           '|---|---:|---:|---:|---:|']
    for cid,groups in cohorts.items():
        a,b=groups['home_won_last3'],groups['draw3_of5']
        text.append(f'| {NAMES[cid]} | {a["hits"]}/{a["count"]}（{pct(a["accuracy"])}） | {pct(a["baseline_mean_probability"])} | {b["hits"]}/{b["count"]}（{pct(b["accuracy"])}） | {pct(b["baseline_mean_probability"])} |')
    text+=['','两组分别只有 127 场和 154 场，实际主胜 83 场、平局 47 场。连续赢的组确实常继续赢，但原模型已给较高主胜概率；历史五场多数平局也没有转化为下一场多数打平。少量重复对阵还带来相关性，不能按独立大样本解释。下一轮应在新增比赛中预先冻结这些规则、按赛事验证交锋的额外贡献。','',
           '## 六种候选的消融结果','',
           '完整列出所有候选，避免事后只展示最好的版本。这里是各候选自身全部合格场次，不与上方 DC 交集表混为同一分母。','',
           '| 联赛 | 区间 | 样本 | 近期比分 | xG | xG+交锋 | xG+预期偏差 | xG+休息/分钟 | 全部增强 |', '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for cid,r in reports.items():
        for year,s in r['seasons'].items():
            text.append(f'| {NAMES[cid]} | {year}/{str(int(year)+1)[2:]} | {s["predicted"]} | '+' | '.join(pct(s['variants'][name]['metrics']['had']['accuracy']) for name in VARIANTS)+' |')
    text+=['','## 市场增强：独立验证和测试','',
           '训练/验证只使用 2025/26，2026/27 为冻结参数的历史测试。胜平负与整数让球独立研究；市场是实际去水报价，不冒充足球模型。加入交锋、近期预期积分偏差、同线报价变化及足球概率，正则和混合权重只在验证段决定。权重零表示增强没有通过验证，结果等于市场，不能说模型获得了市场准确率。','',
           '| 联赛 | 玩法 | 训练/验证/测试 | 增强权重 | 测试准确率 市场→选定 | 测试 Log loss 市场→选定 |', '|---|---|---:|---:|---:|---:|']
    for cid,r in reports.items():
        for code,m in r['market_experiment'].items():
            if m['status']!='completed':
                text.append(f'| {NAMES[cid]} | {LABELS[code]} | 样本不足 | — | — | — |');continue
            a,b=m['metrics']['market'],m['metrics']['selected']
            text.append(f'| {NAMES[cid]} | {LABELS[code]} | {m["selection_train_n"]}/{m["validation_n"]}/{m["test_n"]} | {pct(m["selection"]["weight"])} | {pct(a["accuracy"])}→{pct(b["accuracy"])} | {number(a["log_loss"])}→{number(b["log_loss"])} |')
    text+=['','法甲有未经验证选中的候选在当前少量测试比赛表现更好，但不能看完测试再切换赢家。西甲胜平负验证选中的混合方案在测试退步。当前没有稳定市场增益证据。','',
           '## 对提升准确率的实际判断','',
           '- 保留交锋作为候选事实，不把“连续赢/多数平”直接写成预测规则。本轮这一套窗口、收缩和进球回归实现没有证明稳定增益；不代表所有战术对阵信息都无用。',
           '- 实际表现相对预期的偏差值得继续观察，但德甲上一赛季的提升在当前小样本没有延续。不能把超预期自动解释为诱盘或必然回归。',
           '- 多种进球模型倾向相同常见比分，单靠增加相似统计量难以突破。下一轮应优先建设真正赛前人员可用性、替补质量、战术与教练变化证据，并和当前模型做独立对照，而不是继续堆同类特征。',
           '- 大模型适合抽取并核实这些情报，也可做独立预测组。历史新闻缺少可用时间快照时，只能从未来比赛开始冻结，不能让已知赛果的大模型倒做历史预测。',
           '- 盘口走势只测试了体彩已有报价，不代表多机构价格分歧、交易所成交和资金热度。既有亚洲盘缺逐次时间，因此不能冒称已测试“诱上/诱下”的全部解释。',
           '- 市场报价仍是独立基准。是否晋级看同联赛、同场、同时点的概率质量与单选准确率稳定性；本轮不发布任何“准确率已提高”的生产变更。','',
           '## 验证与重放','',
           f'- 自动复核 {checks["feature_references"]:,} 次历史输入引用，全部早于各场截止且同赛事；{checks["model_refits"]} 个模型完整重拟合参数一致；{checks["forecast_replays"]:,} 份候选预测与 {checks["market_replays"]} 份市场增强概率重放一致。',
           f'- {checks["protected_artifacts_unchanged"]} 份既有模型、数据集和选用等受保护文件的哈希在研究前后相同；未更改生产选择。',
           '- 针对性测试覆盖未来/目标信息泄漏、延迟结果、交锋主客方向、跨赛事混入、报价截止、盘口变线、五玩法结算和参数冻结。',
           f'- [原始索引](../../{index_path.relative_to(ROOT).as_posix()})；[逐玩法完整指标 CSV]({csv_path.name})。数据在本地研究目录，不随 Git 自动上传。',
           '- 首次运行在英超报告写出后因输出相对路径处理失败；修正仅涉及输出路径。完整结果来自 context-v2，未改候选、窗口或选优指标。','',
           '复现：','.venv/Scripts/python.exe scripts/research_big_five_context.py --output <新的独立研究目录>','',
           '审计：','.venv/Scripts/python.exe scripts/audit_context_backtest.py --index data/research/big-five/2026-11-09/context-v2/index.json','']
    output.write_text('\n'.join(text),encoding='utf-8')
    rows=[]
    for cid,r in reports.items():
        for year,s in r['seasons'].items():
            groups={k:v['metrics'] for k,v in s['variants'].items()}
            groups.update(dc_common=s['dc']['metrics'],selected_common=s['selected_common']['metrics'])
            for name,metrics in groups.items():
                for code,m in metrics.items():
                    rows.append({'league':cid,'season':year,'variant':name,'market':code,**m})
    keys=list(dict.fromkeys(k for r in rows for k in r))
    with csv_path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(rows)
    print(output,checks,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index',type=Path,required=True)
    args=parser.parse_args()
    with threadpool_limits(limits=1):
        index_path=args.index.resolve()
        reports,checks=audit(index_path)
        write_report(reports,checks,index_path)
