"""Reproducible competition-only challengers; never changes observation pointers."""
import argparse
import json
import math
from datetime import timedelta
from uuid import uuid4

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit

from . import training as t, model_registry as registry
from .sources import parse_date
from .store import fingerprint, now_iso


def cutoff(row):
    at = parse_date(row['kickoff']) - timedelta(minutes=60)
    return (at - timedelta(days=at.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


def available(rows, at, competition):
    return sorted((r for r in rows if r['competition_id'] == competition
                   and parse_date(r['result_available_at']) < at
                   and parse_date(r['kickoff']) < at), key=lambda r: (r['kickoff'], r['id']))


def features(rows, row, at):
    """Only results available at this cutoff; no target labels, future lineups or odds."""
    history = available(rows, at, row['competition_id'])
    ratings, recent = {}, {}
    for r in history:
        h, a = r['home_id'], r['away_id']
        rh, ra = ratings.get(h, 1500.), ratings.get(a, 1500.)
        expected = 1 / (1 + 10 ** ((ra-rh-60)/400))
        observed = 1 if r['home_goals'] > r['away_goals'] else 0 if r['home_goals'] < r['away_goals'] else .5
        change = 20*(observed-expected)
        ratings[h], ratings[a] = rh+change, ra-change
        recent.setdefault(h, []).append((r['home_goals'], r['away_goals']))
        recent.setdefault(a, []).append((r['away_goals'], r['home_goals']))
    h, a = row['home_id'], row['away_id']
    out = [(ratings.get(h, 1500)-ratings.get(a, 1500))/400]
    for team in (h, a):
        form = recent.get(team, [])[-10:]
        out.extend([(sum(x[i] for x in form)+3)/(len(form)+2) for i in (0, 1)])
        out.append(min(len(recent.get(team, [])), 30)/30)
    return out


class JointDC:
    """Joint penalized likelihood, centered attack/defence and feasible rho."""
    def fit(self, rows, at, competition):
        rows = available(rows, at, competition)
        self.teams = sorted({r[k] for r in rows for k in ('home_id', 'away_id')})
        n = len(self.teams)
        idx = {v:i for i,v in enumerate(self.teams)}
        h = np.array([idx[r['home_id']] for r in rows]); a = np.array([idx[r['away_id']] for r in rows])
        gh = np.array([r['home_goals'] for r in rows]); ga = np.array([r['away_goals'] for r in rows])
        w = np.array([2**(-(at-parse_date(r['kickoff'])).total_seconds()/86400/180) for r in rows])
        def unpack(x):
            attack = np.r_[x[2:n+1], -sum(x[2:n+1])]
            defence = np.r_[x[n+1:2*n], -sum(x[n+1:2*n])]
            # Bound rho for every possible pair, not only observed low scores.
            largest = float(np.exp(x[0]+max(0,x[1])+max(attack)+max(defence)))
            lo, hi = -.999/largest, min(.999, .999/largest**2)
            return attack, defence, lo+(hi-lo)*expit(x[-1])
        def objective(x):
            attack, defence, rho = unpack(x)
            lh = x[0]+x[1]+attack[h]+defence[a]; la = x[0]+attack[a]+defence[h]
            mh, ma = np.exp(lh), np.exp(la)
            tau = np.ones(len(rows))
            for mask, v in (((gh==0)&(ga==0), 1-mh*ma*rho), ((gh==0)&(ga==1), 1+mh*rho),
                            ((gh==1)&(ga==0), 1+ma*rho), ((gh==1)&(ga==1), np.full(len(rows),1-rho))):
                tau[mask] = v[mask]
            return float(np.sum(w*(mh+ma-gh*lh-ga*la-np.log(tau))) + .35*(sum(attack**2)+sum(defence**2)))
        x = np.zeros(2*n+1); x[0] = math.log(max(.2,float(np.average(np.r_[gh,ga],weights=np.r_[w,w]))))
        fitted = minimize(objective, x, method='L-BFGS-B', bounds=[(-1,1.5),(-.7,.7)]+[(-1,1)]*(2*n-2)+[(-8,8)], options={'maxiter':600,'ftol':1e-9,'maxfun':50000})
        if not fitted.success:
            # Retry the SAME likelihood with a different constrained solver.
            fitted = minimize(objective, fitted.x, method='SLSQP',
                              bounds=[(-1,1.5),(-.7,.7)]+[(-1,1)]*(2*n-2)+[(-8,8)],
                              options={'maxiter':600,'ftol':1e-7})
        if not fitted.success or not np.isfinite(fitted.fun):
            raise ValueError('Joint DC did not converge: '+str(fitted.message))
        self.x = fitted.x
        self.attack, self.defence, self.rho = unpack(self.x)
        half = [r for r in rows if r.get('home_half_goals') is not None]
        self.half = [(1+sum(r[s+'_half_goals'] for r in half))/(2+sum(r[s+'_goals'] for r in half)) for s in ('home','away')] if len(half)>=100 else None
        return self

    def forecast(self, row):
        indices = {v:i for i,v in enumerate(self.teams)}
        def strength(team, field):
            return float(field[indices[team]]) if team in indices else 0.
        h, a = row['home_id'], row['away_id']
        mh = math.exp(self.x[0]+self.x[1]+strength(h,self.attack)+strength(a,self.defence))
        ma = math.exp(self.x[0]+strength(a,self.attack)+strength(h,self.defence))
        return t.market_probabilities(t.score_grid(mh,ma,float(self.rho)), row.get('handicap'), self.half)


def walk(rows, targets, kind, training_features):
    models, output = {}, []
    for row in targets:
        at = cutoff(row); cid = row['competition_id']; key = (cid,at)
        past = available(rows,at,cid)
        if key not in models:
            if kind == 'joint_dc':
                models[key] = JointDC().fit(past,at,cid)
            else:
                from sklearn.linear_model import LogisticRegression
                from catboost import CatBoostClassifier
                model = (LogisticRegression(C=.1,max_iter=1000) if kind=='elo_logistic' else
                         CatBoostClassifier(iterations=120,depth=3,learning_rate=.03,l2_leaf_reg=10,
                                            loss_function='MultiClass',random_seed=42,thread_count=1,
                                            verbose=False,allow_writing_files=False))
                x = [training_features[r['id']] for r in past]
                y = [0 if r['home_goals']>r['away_goals'] else 2 if r['home_goals']<r['away_goals'] else 1 for r in past]
                model.fit(x,y); models[key] = model
        model = models[key]
        if kind == 'joint_dc':
            markets = model.forecast(row); p = [markets['had'][k] for k in 'hda']
        else:
            probs = model.predict_proba([features(past,row,at)])[0]
            p = [0.,0.,0.]
            for label, value in zip(model.classes_,probs): p[int(label)] = float(value)
            markets = {'had':dict(zip('hda',p))}
        h,a = row['home_goals'],row['away_goals']
        output.append({'id':row['id'],'competition_id':cid,'season':row['season'],'stage':row['stage'],
                       'trained_before':at.isoformat(),'probabilities':p,'markets':markets,
                       'outcome':0 if h>a else 2 if a>h else 1,
                       'actual_markets':{'had':t.outcome(h,a),'ttg':f's{min(h+a,7)}','crs':t.score_key(h,a),
                           **({'hhad':t.outcome(h+row['handicap'],a)} if row.get('handicap') is not None else {}),
                           **({'hafu':t.outcome(row['home_half_goals'],row['away_half_goals'])+t.outcome(h,a)} if row.get('home_half_goals') is not None else {})}})
    return output


def run(competition, target='full_time'):
    # CLI and console now invoke the same candidate training service.
    return registry.train(competition, lambda update: print(update, flush=True), target=target)


def legacy_run(competition):
    """Retained only for audit of the earlier five-method experiment."""
    with registry.training_lock(competition):
        data = registry.dataset(competition); rows = t.validate_dataset(data)
        if {r['competition_id'] for r in rows} != {competition}: raise ValueError('Mixed competitions')
        _, vs, ts, validation, test = t.split_dataset(rows)
        vectors = {r['id']:features(rows,r,cutoff(r)) for r in rows}
        report = {'competition_id':competition,'dataset_digest':fingerprint(data),'created_at':now_iso(),
                  'validation_start':str(vs),'test_start':str(ts),'status':'research_only',
                  'limitations':['测试段曾用于既有模型评估，本次属于历史探索比较，仍需前瞻验证',
                                 '结果可用时间沿用历史数据假设；未输入外盘、伤停、阵容或xG',
                                 '分类模型只输出胜平负；不得冒充五玩法模型'], 'models':[]}
        for kind in ('poisson','dc','joint_dc','elo_logistic','catboost'):
            print(competition,kind,flush=True)
            def evaluate(targets):
                return (t.walk_forward(rows,targets,'separate',180,kind) if kind in ('poisson','dc') else walk(rows,targets,kind,vectors))
            val = evaluate(validation); rec = evaluate(test)
            report['models'].append({'kind':kind,'validation':t.metrics(val),'test':t.metrics(rec),
                                     'markets':t.market_metrics(rec),'records':rec})
        report['selected_on_validation'] = min(report['models'],key=lambda m:m['validation']['log_loss'])['kind']
        report['uniform_baseline'] = {'log_loss':math.log(3),'brier':2/3}
        report['market_comparison_count'] = sum(bool(r.get('market')) for r in report['models'][0]['records'])
        run_id = uuid4().hex
        path = registry.directory(competition)/'benchmarks'/run_id/'report.json'
        t.atomic_json(path,report)
        publish_summary(competition, report, run_id)
        print(str(path),flush=True)
        return report


def publish_summary(competition, report, run_id):
    summary = {k:v for k,v in report.items() if k not in ('models', 'records')}
    summary['run_id'] = run_id
    summary['models'] = [{k:v for k,v in m.items() if k != 'records'} for m in report['models']]
    t.atomic_json(registry.directory(competition)/'benchmarks'/'latest.json', summary)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--competition',required=True)
    parser.add_argument('--target',choices=('full_time','had'),default='full_time')
    args = parser.parse_args()
    run(args.competition, args.target)
