import copy
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch

from tests.test_console_football_model import history, dataset
from worldcup_mvp.console import model_methods as m, model_evaluation as e, training as t


class MethodTests(unittest.TestCase):
    def test_market_observations_do_not_enter_fitted_parameters_replay_history_or_forecast(self):
        rows=history(100,half=True);at=datetime(2024,1,1,tzinfo=timezone.utc)
        changed=[{**r,'odds':{'h':1.05,'d':20,'a':40},'prematch_market_archive':{'quotes':['unrelated']},
                  'postmatch_xg_sources':{'unused':{'xg':[18,0]}}} for r in rows]
        for kind in ('poisson','dynamic_gamma','elo_logistic'):
            config={'mode':'separate','half_life':180,'kind':kind}
            a=m.fit(config,rows,at,'jp.j1');b=m.fit(config,changed,at,'jp.j1')
            self.assertEqual(a.to_dict(),b.to_dict())
            self.assertEqual(a.forecast(rows[0]),b.forecast(changed[0]))
            for r in b.to_dict().get('history',[]):
                self.assertNotIn('odds',r);self.assertNotIn('prematch_market_archive',r)

    def test_new_artifacts_roundtrip_capabilities_and_isolation(self):
        rows = history(120,half=True)
        at = datetime(2024,1,1,tzinfo=timezone.utc)
        row = rows[0]
        for kind in ('lightgbm_goals','dynamic_gamma','elo_logistic','catboost','joint_dc'):
            with self.subTest(kind=kind):
                model = m.Candidate(kind).fit(rows,at,'jp.j1')
                expected = model.forecast(row,1)
                restored = m.restore(json.loads(json.dumps(model.to_dict())))
                self.assertEqual(expected,restored.forecast(row,1))
                self.assertEqual(set(expected['markets']), set(m.capabilities(kind)))
                for market in expected['markets'].values():
                    self.assertAlmostEqual(sum(market.values()),1,places=9)
                    self.assertTrue(all(0<=p<=1 for p in market.values()))
                if 'hafu' in expected['markets']:
                    for key in 'hda':
                        self.assertAlmostEqual(expected['markets']['had'][key], sum(p for k,p in expected['markets']['hafu'].items() if k[1]==key))
                changed = copy.deepcopy(row); changed['competition_id']='kr.k1'
                with self.assertRaises(ValueError): restored.forecast(changed)

    def test_delayed_and_other_competition_results_cannot_change_models(self):
        rows = history(60,half=True)
        at = datetime(2024,1,1,tzinfo=timezone.utc)
        late = copy.deepcopy(rows[0]); late['home_goals']=99; late['result_available_at']='2027-01-01T00:00:00+00:00'
        augmented = rows+[late]+history(70,league='kr.k1',half=True)
        for kind in ('lightgbm_goals','dynamic_gamma'):
            first = m.Candidate(kind).fit(rows,at,'jp.j1').forecast(rows[0])
            second = m.Candidate(kind).fit(augmented,at,'jp.j1').forecast(rows[0])
            self.assertEqual(first,second)

    def test_validation_selection_is_not_changed_by_test_results(self):
        rows=history(400,half=True)
        candidates=m.configs()[:2]
        def walk(all_rows,targets,config,vectors):
            is_validation=targets[0]['id']==rows[200]['id']
            better=(config['kind']=='poisson') == is_validation
            p=[.8,.1,.1] if better else [.1,.8,.1]
            return [dict(id='x',competition_id='jp.j1',season='2025',stage='regular',
                probabilities=p,outcome=0,market=None,markets={'had':dict(zip('hda',p))},actual_markets={'had':'h'})]
        with patch.object(t,'validate_dataset',return_value=rows),patch.object(t,'split_dataset',return_value=(['jp.j1'],'2025','2026',rows[200:300],rows[300:])),patch.object(e,'walk',side_effect=walk):
            report=e.evaluate(dataset(rows),candidates=candidates)
        self.assertEqual(report['selected']['kind'],'poisson')
        self.assertEqual(report['status'],'research_only')
        self.assertEqual(report['market_comparison_count'],0)

    def test_restored_model_rejects_future_history(self):
        rows=history(60,half=True)
        model=m.Candidate('dynamic_gamma').fit(rows,datetime(2024,1,1,tzinfo=timezone.utc),'jp.j1')
        value=model.to_dict();value['history'][0]['result_available_at']='2027-01-01T00:00:00Z'
        with self.assertRaises(ValueError):m.restore(value)

    def test_full_time_target_does_not_select_a_classifier(self):
        rows=history(400,half=True)
        configs=[m.configs()[0],m.configs()[5]]
        def walk(all_rows,targets,config,vectors):
            p=[.9,.05,.05] if config['kind']=='elo_logistic' else [.5,.25,.25]
            return [dict(id='x',competition_id='jp.j1',season='2025',stage='regular',probabilities=p,
                outcome=0,market=None,markets={'had':dict(zip('hda',p))},actual_markets={'had':'h'})]
        with patch.object(t,'validate_dataset',return_value=rows),patch.object(t,'split_dataset',return_value=(['jp.j1'],'2025','2026',rows[200:300],rows[300:])),patch.object(e,'walk',side_effect=walk):
            self.assertEqual(e.evaluate(dataset(rows),candidates=configs)['selected']['kind'],'poisson')
            self.assertEqual(e.evaluate(dataset(rows),candidates=configs,target='had')['selected']['kind'],'elo_logistic')


if __name__=='__main__':unittest.main()
