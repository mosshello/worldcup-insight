"""Competition-only candidate protocol. JSON artifacts, explicit market capabilities."""
import json
import math
from pathlib import Path
import tempfile

import numpy as np

from . import training as t
from .model_benchmark import available, cutoff, features, JointDC
from .sources import parse_date

NAMES = {'poisson': '攻防泊松', 'dc': '两阶段低比分修正', 'joint_dc': '联合 Dixon–Coles',
         'elo_logistic': 'Elo 逻辑回归', 'catboost': 'CatBoost 胜平负',
         'lightgbm_goals': 'LightGBM 进球', 'dynamic_gamma': '动态 Gamma–Poisson'}
CLASSIFIERS = {'elo_logistic', 'catboost'}
FEATURE_VERSION = 'elo-recent-results-weekly-v1'
FEATURE_NAMES = ['elo_difference', 'home_recent_goals', 'home_recent_conceded', 'home_sample_size',
                 'away_recent_goals', 'away_recent_conceded', 'away_sample_size']


def capabilities(kind):
    return ['had'] if kind in CLASSIFIERS else ['had', 'hhad', 'ttg', 'crs', 'hafu']


def configs():
    return ([{'mode': 'separate', 'half_life': days, 'kind': kind}
             for days in (180, 365) for kind in ('poisson', 'dc')]
            + [{'mode': 'separate', 'half_life': 180, 'kind': kind}
               for kind in ('joint_dc', 'elo_logistic', 'catboost', 'lightgbm_goals', 'dynamic_gamma')])


def fit(config, rows, at, competition, vectors=None):
    if config['mode'] != 'separate':
        raise ValueError('候选模型仅支持独立赛事')
    if config['kind'] in ('poisson', 'dc'):
        return t.GoalModel('separate', config['half_life'], config['kind']).fit(rows, at, competition)
    return Candidate(config['kind'], config['half_life']).fit(rows, at, competition, vectors)


def restore(value):
    return Candidate.from_dict(value) if value.get('adapter') == 'candidate-json-v1' else t.GoalModel.from_dict(value)


class Candidate:
    def __init__(self, kind, half_life=180):
        if kind not in NAMES or kind in ('poisson', 'dc'):
            raise ValueError('未知候选方法')
        self.kind, self.half_life, self.mode = kind, half_life, 'separate'

    def fit(self, rows, at, competition, vectors=None):
        self.history = available(rows, at, competition)
        if not self.history:
            raise ValueError('无截止时点前数据')
        self.competition = competition
        self.trained_before = at.isoformat()
        self.sample_count = len(self.history)
        self.team_counts = {}
        for row in self.history:
            for side in ('home', 'away'):
                key = competition + ':' + row[side+'_id']
                self.team_counts[key] = self.team_counts.get(key, 0) + 1
        halves = [r for r in self.history if r.get('home_half_goals') is not None]
        self.half_samples = len(halves)
        # Existing conditional module, explicitly separate from the goal algorithm.
        self.half_shares = None
        if len(halves) >= 100 and all(sum(r[s+'_goals'] for r in halves) >= 30 for s in ('home', 'away')):
            weights = [2**(-(at-parse_date(r['kickoff'])).total_seconds()/86400/self.half_life) for r in halves]
            self.half_shares = [(1+sum(w*r[s+'_half_goals'] for w,r in zip(weights,halves)))/
                                (2+sum(w*r[s+'_goals'] for w,r in zip(weights,halves))) for s in ('home','away')]
        self.payload = {}
        if self.kind == 'joint_dc':
            model = JointDC().fit(self.history, at, competition)
            self.payload = {'teams': model.teams, 'attack': model.attack.tolist(),
                            'defence': model.defence.tolist(), 'intercept': float(model.x[0]),
                            'home': float(model.x[1]), 'rho': float(model.rho)}
        elif self.kind == 'dynamic_gamma':
            self._fit_gamma(at)
        else:
            x = np.asarray([vectors[r['id']] if vectors is not None else features(self.history,r,cutoff(r)) for r in self.history])
            y = np.asarray([0 if r['home_goals'] > r['away_goals'] else 2 if r['home_goals'] < r['away_goals'] else 1 for r in self.history])
            if self.kind == 'lightgbm_goals':
                import lightgbm as lgb
                self.boosters = []
                for side in ('home', 'away'):
                    model = lgb.LGBMRegressor(objective='poisson', n_estimators=100, learning_rate=.03,
                        max_depth=3, num_leaves=7, min_child_samples=30, reg_lambda=10,
                        random_state=42, n_jobs=1, verbosity=-1)
                    model.fit(x, [r[side+'_goals'] for r in self.history])
                    self.boosters.append(model.booster_)
                self.payload = {'boosters': [b.model_to_string() for b in self.boosters]}
            elif self.kind == 'elo_logistic':
                from sklearn.linear_model import LogisticRegression
                model = LogisticRegression(C=.1, max_iter=1000).fit(x,y)
                self.payload = {'classes': model.classes_.tolist(), 'coef': model.coef_.tolist(), 'intercept': model.intercept_.tolist()}
            else:
                from catboost import CatBoostClassifier
                self.cat = CatBoostClassifier(iterations=120,depth=3,learning_rate=.03,l2_leaf_reg=10,
                    loss_function='MultiClass',random_seed=42,thread_count=1,verbose=False,allow_writing_files=False).fit(x,y)
                with tempfile.TemporaryDirectory() as folder:
                    path = Path(folder)/'model.json'
                    self.cat.save_model(str(path),format='json')
                    self.payload = {'model': path.read_text(encoding='utf-8'), 'classes': self.cat.classes_.tolist()}
        return self

    def _fit_gamma(self, at):
        """Discounted conjugate filter, opponent effects plugged in at their prior means.

        This is an approximate Bayesian filter, not joint MCMC or a paper replication.
        Gamma attack/defence multipliers start at shape=rate=8 (mean 1).
        Historical updates use pre-game states; all matches at the same time update together.
        """
        from itertools import groupby
        states = {}
        league = {'home': [12., 8.], 'away': [10., 8.]}
        last = parse_date(self.history[0]['kickoff'])
        def decay(until):
            nonlocal last
            discount = 2**(-max(0,(until-last).total_seconds())/86400/self.half_life)
            for state in states.values():
                for part in state.values():
                    part[:] = [8+(v-8)*discount for v in part]
            for side, prior in (('home',(12,8)),('away',(10,8))):
                league[side] = [p+(v-p)*discount for p,v in zip(prior,league[side])]
            last = until
        for kickoff, matches in groupby(self.history, key=lambda r:r['kickoff']):
            decay(parse_date(kickoff))
            updates = []
            for row in matches:
                for side in ('home','away'):
                    states.setdefault(row[side+'_id'], {'attack':[8.,8.], 'defence':[8.,8.]})
                for side, other in (('home','away'),('away','home')):
                    attack = states[row[side+'_id']]['attack']
                    defence = states[row[other+'_id']]['defence']
                    base = league[side][0]/league[side][1]
                    updates.append((attack, row[side+'_goals'], base*defence[0]/defence[1]))
                    updates.append((defence, row[side+'_goals'], base*attack[0]/attack[1]))
                    updates.append((league[side], row[side+'_goals'], 1.))
            for state, goals, exposure in updates:
                state[0] += goals
                state[1] += exposure
        decay(at)
        self.payload = {'states':states, 'league':league, 'inference':'discounted-gamma-mean-field',
                        'prior_strength':8, 'draws':512, 'seed':42}

    def _grid(self, row):
        if self.kind == 'joint_dc':
            p = self.payload
            indices = {team:i for i,team in enumerate(p['teams'])}
            def value(side, field):
                return p[field][indices[row[side+'_id']]] if row[side+'_id'] in indices else 0.
            rates = [math.exp(p['intercept']+p['home']+value('home','attack')+value('away','defence')),
                     math.exp(p['intercept']+value('away','attack')+value('home','defence'))]
            return rates, t.score_grid(*rates,p['rho'])
        if self.kind == 'lightgbm_goals':
            x = np.asarray([features(self.history,row,parse_date(self.trained_before))])
            rates = [float(b.predict(x,num_threads=1)[0]) for b in self.boosters]
            return rates, t.score_grid(*rates)
        # Integrate posterior rate uncertainty rather than just using posterior means.
        rng = np.random.default_rng(self.payload['seed'])
        draws = self.payload['draws']
        rates = []
        for side, other in (('home','away'),('away','home')):
            a = self.payload['states'].get(row[side+'_id'],{}).get('attack',[8,8])
            d = self.payload['states'].get(row[other+'_id'],{}).get('defence',[8,8])
            base = self.payload['league'][side]
            rates.append(rng.gamma(a[0],1/a[1],draws)*rng.gamma(d[0],1/d[1],draws)*base[0]/base[1])
        from scipy.stats import poisson
        maximum = max(float(np.max(r)) for r in rates)
        limit = max(30, int(math.ceil(maximum+12*math.sqrt(maximum)+12)))
        if limit > 200:
            raise ValueError('后验进球分布异常，拒绝截断预测')
        support = np.arange(limit+1)
        home = poisson.pmf(support[None,:], rates[0][:,None])
        away = poisson.pmf(support[None,:], rates[1][:,None])
        grid = home.T @ away / draws
        if grid.sum() < 1-1e-10:
            raise ValueError('比分尾部质量不足')
        return [float(np.mean(r)) for r in rates], (grid/grid.sum()).tolist()

    def forecast(self,row,handicap=None):
        if row['competition_id'] != self.competition:
            raise ValueError('禁止跨赛事预测')
        if self.kind in CLASSIFIERS:
            x = np.asarray([features(self.history,row,parse_date(self.trained_before))])
            if self.kind == 'catboost':
                probs = self.cat.predict_proba(x)[0]
            else:
                logits = (x @ np.asarray(self.payload['coef']).T + self.payload['intercept'])[0]
                if len(logits) == 1:
                    p = 1/(1+np.exp(-logits[0])); probs = np.array([1-p,p])
                else:
                    probs = np.exp(logits-np.max(logits)); probs /= probs.sum()
            p = [0.,0.,0.]
            for label,value in zip(self.payload['classes'],probs): p[int(label)] = float(value)
            return {'markets':{'had':dict(zip('hda',p))},'expected_goals':None,'half_samples':0,'half_method':None}
        rates, grid = self._grid(row)
        markets = t.market_probabilities(grid,handicap,self.half_shares)
        return {'markets':markets,'expected_goals':rates,'half_samples':self.half_samples,
                'half_method':'conditional-binomial' if 'hafu' in markets else None}

    def to_dict(self):
        return {'adapter':'candidate-json-v1','schema':t.MODEL_SCHEMA,'feature_version':FEATURE_VERSION,
                'supported_markets':[k for k in capabilities(self.kind) if k != 'hafu' or self.half_shares],
                'input_description':('Elo及同赛事近期进失球' if self.kind in CLASSIFIERS or self.kind == 'lightgbm_goals'
                                     else '同赛事历史进失球、球队身份、主客场和时间'),
                **{key:getattr(self,key) for key in
                ('kind','mode','half_life','competition','trained_before','sample_count','team_counts',
                 'half_samples','half_shares','payload','history')}}

    @classmethod
    def from_dict(cls,value):
        if value.get('mode') != 'separate' or value.get('schema') != t.MODEL_SCHEMA or value.get('feature_version') != FEATURE_VERSION:
            raise ValueError('候选模型版本不兼容')
        model = cls(value['kind'],value['half_life'])
        for key in ('competition','trained_before','sample_count','team_counts','half_samples','half_shares','payload','history'):
            setattr(model,key,value[key])
        if any(r['competition_id'] != model.competition or parse_date(r['result_available_at']) >= parse_date(model.trained_before) for r in model.history):
            raise ValueError('候选模型包含其他赛事或未来数据')
        if model.kind == 'lightgbm_goals':
            import lightgbm as lgb
            model.boosters = [lgb.Booster(model_str=s) for s in model.payload['boosters']]
        elif model.kind == 'catboost':
            from catboost import CatBoostClassifier
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder)/'model.json'
                path.write_text(model.payload['model'],encoding='utf-8')
                model.cat = CatBoostClassifier().load_model(str(path),format='json')
        return model
