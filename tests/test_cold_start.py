import copy
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from worldcup_mvp.console import cold_start, training, model_evaluation
from worldcup_mvp.console.model_methods import fit, restore
from worldcup_mvp.console.sources import parse_date
from worldcup_mvp.console.store import fingerprint
from tests.test_console_football_model import history, dataset


class ColdStartTests(unittest.TestCase):
    def test_minimum_and_baseline_are_joint_and_stratum_specific(self):
        rows=[{'probabilities':[.7,.2,.1],'league_prior':[.4,.3,.3],'outcome':0} for _ in range(30)]
        self.assertFalse(cold_start.comparison(rows[:29])['passed'])
        self.assertTrue(cold_start.comparison(rows)['passed'])
        self.assertFalse(cold_start.comparison([{**r,'outcome':2} for r in rows])['passed'])
        policy={'policy':cold_start.POLICY,'passed':True,'allowed_strata':['3-4']}
        self.assertTrue(cold_start.allowed(policy,[3,20]))
        for counts in ([0,20],[2,20],[-1,20],[3],[]):self.assertFalse(cold_start.allowed(policy,counts))
        self.assertFalse(cold_start.allowed(None,[3,20]))

    def test_cold_fold_replays_and_uses_no_future_or_other_competition(self):
        rows=history(45)
        target=copy.deepcopy(rows[-1]);target['away_id']='new-team'
        artifacts=[]
        def sink(value):
            artifacts.append(value)
            return {'digest':fingerprint(value),'kind':'fold'}
        records=model_evaluation.walk(rows,[target],cold_start.CONFIG,None,exclusions=[],artifact_sink=sink,
                                      minimum_team_samples=0,record_extra=cold_start.extra)
        record=records[0];value=artifacts[0]
        model=restore(value['model'])
        forecast=model.forecast(target)
        self.assertEqual(record['probabilities'],[forecast['markets']['had'][k] for k in 'hda'])
        self.assertEqual(record['league_prior'],cold_start.extra(model,target)['league_prior'])
        self.assertEqual(record['team_samples'][1],0)
        self.assertTrue(all(parse_date(r['result_available_at'])<parse_date(record['trained_before']) for r in model.history))
        self.assertEqual(set(value['input_receipt']['row_ids']),{r['id'] for r in model.history})
        with self.assertRaises(ValueError):model.forecast({**target,'competition_id':'other'})
        self.assertTrue(all(low<high for low,high in record['uncertainty']['goal_rate_intervals']))
        standard=model_evaluation.walk(rows,[target],cold_start.CONFIG,None,exclusions=[])
        self.assertEqual(standard,[])

    def test_unknown_and_unbound_policy_rejected(self):
        policy={'policy':cold_start.POLICY,'passed':True,'allowed_strata':['3-4'],
                'dataset_digest':'d','report_id':'r','report_digest':'hash'}
        bundle={'cold_start_policy':policy,'dataset_digest':'d','models':{'jp.j1':{'kind':'dynamic_gamma','half_life':180}}}
        cold_start.validate_binding(bundle,{'cold_start_policy':policy})
        for value in ({**bundle,'dataset_digest':'other'},{**bundle,'models':{'jp.j1':{'kind':'poisson','half_life':180}}}):
            with self.assertRaises(ValueError):cold_start.validate_binding(value,{'cold_start_policy':policy})

    def test_qualifier_registration_preserves_adapter_phase_prefix(self):
        from worldcup_mvp.console.catalog import catalog
        spec=next(c for c in catalog() if c['id']=='uefa.ucl')
        expected={'qualifying:'+v for v in ('first-round','second-round','third-round','playoff-round')}
        self.assertTrue(expected.issubset(spec['training_stage_policy']['2026']))
        self.assertEqual(spec['prediction_stage_policy']['2026'],['league-phase'])
        self.assertEqual(spec['research_policy']['qualifying_years'],[2026])
