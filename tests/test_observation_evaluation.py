import tempfile
import json
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from worldcup_mvp.console.store import Store, encode
from worldcup_mvp.console.observations import ObservationWorkspace
from worldcup_mvp.console.observation_evaluation import assess_pair, holm_family, evaluate, actual_result, PROTOCOL
from worldcup_mvp.console.observation_insights import read_evidence


class EvaluationTests(unittest.TestCase):
    def seed(self,store,n=200):
        w=ObservationWorkspace(store);config={'evaluation_protocol':PROTOCOL,'evaluation_family':'audit-fixed-v1'}
        exp=w.pin_experiment('eng.premier',config,'2026-11-10T00:00:00Z')
        config={**config,'competition_id':'eng.premier'}
        with store.connect() as db:
            for i,row in enumerate(self.rows(n)):
                kickoff=datetime.fromisoformat(row['kickoff']);due=kickoff-timedelta(hours=1);finished=due+timedelta(seconds=1)
                fixture={'id':str(i),'competition_id':'eng.premier','kickoff':row['kickoff']}
                for source in ('base','ai'):
                    ident=f'{source}-{i}';job={'id':ident,'fixture':fixture,'experiment':config}
                    result={'strict_node':True,'cutoff':due.isoformat(),'created_at':finished.isoformat(),'probabilities':{'had':row['base' if source=='base' else 'candidate']}}
                    db.execute('INSERT INTO observation_jobs(id,fixture_id,revision,experiment_id,node,source,due_at,deadline,status,payload,result,created_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (ident,str(i),'r1',exp,'t60',source,due.isoformat(),(due+timedelta(seconds=180)).isoformat(),'completed',encode(job),encode(result),due.isoformat(),finished.isoformat()))
                score={'period':'regular_time','home_goals':1,'away_goals':0}
                db.execute('INSERT INTO observation_outcomes VALUES(?,?,?,?,?)',(str(i),str(i),'r1',(kickoff+timedelta(hours=2)).isoformat(),encode(score)))
        return exp

    def ai_cell(self,result):return next(r for r in result['cells'] if r['source']=='ai')

    def test_missing_candidate_is_retained_in_full_target_denominator(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db');self.seed(store)
            with store.connect() as db:db.execute("DELETE FROM observation_jobs WHERE id='ai-10'")
            r=self.ai_cell(evaluate(store,'2028-01-01T00:00:00Z'))
            self.assertEqual((r['n'],r['targets']),(199,200));self.assertEqual(r['states']['missing'],1)

    def test_future_jobs_late_completions_and_forged_experiment_are_excluded(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db');self.seed(store,4)
            with store.connect() as db:
                db.execute("UPDATE observation_jobs SET created_at='2029-01-01T00:00:00+00:00' WHERE fixture_id='0'")
                db.execute("UPDATE observation_jobs SET finished_at='2028-01-01T00:00:00+00:00' WHERE id='ai-1'")
                row=db.execute("SELECT payload FROM observation_jobs WHERE id='ai-2'").fetchone();p=json.loads(row[0]);p['experiment']['evaluation_family']='fake'
                db.execute("UPDATE observation_jobs SET payload=? WHERE id='ai-2'",(encode(p),))
            result=evaluate(store,'2028-01-01T00:00:00Z');r=self.ai_cell(result)
            self.assertEqual((r['n'],r['targets']),(1,3));self.assertEqual(result['excluded']['experiment_mismatch'],1)

    def test_invalid_probability_and_pre_match_score_are_not_scored(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db');self.seed(store,2)
            with store.connect() as db:
                row=db.execute("SELECT result FROM observation_jobs WHERE id='ai-0'").fetchone();p=json.loads(row[0]);p['probabilities']['had']={'h':.9,'d':.9,'a':.9}
                db.execute("UPDATE observation_jobs SET result=? WHERE id='ai-0'",(encode(p),))
                db.execute("UPDATE observation_outcomes SET created_at='2026-12-31T00:00:00+00:00' WHERE fixture_id='1'")
            self.assertEqual(self.ai_cell(evaluate(store,'2028-01-01T00:00:00Z'))['n'],0)

    def test_strict_node_flag_cannot_override_actual_lateness(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db');self.seed(store,1)
            with store.connect() as db:
                for row in db.execute('SELECT id,result,due_at FROM observation_jobs').fetchall():
                    result=json.loads(row['result']);due=datetime.fromisoformat(row['due_at'])
                    result['cutoff']=(due+timedelta(seconds=70)).isoformat();result['created_at']=(due+timedelta(seconds=71)).isoformat()
                    db.execute('UPDATE observation_jobs SET result=?,finished_at=? WHERE id=?',(encode(result),result['created_at'],row['id']))
            self.assertEqual(self.ai_cell(evaluate(store,'2028-01-01T00:00:00Z'))['n'],0)

    def test_fixed_report_and_evidence_survive_append_correction(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db');self.seed(store)
            before=self.ai_cell(evaluate(store,'2028-01-01T00:00:00Z'))
            self.assertTrue(before['frozen'])
            with store.connect() as db:
                decision=json.loads(db.execute('SELECT payload FROM observation_evaluation_families WHERE id=?',(before['family_decision_id'],)).fetchone()[0])
                self.assertEqual(decision['family_size'],75);self.assertFalse(decision['production_changes'])
                evidence=json.loads(db.execute('SELECT payload FROM observation_evaluation_evidence WHERE id=?',(before['check_id'],)).fetchone()[0])
                self.assertEqual(len(evidence['samples']),200);self.assertEqual(evidence['samples'][0]['actual'],'h')
                db.execute('INSERT INTO observation_outcomes VALUES(?,?,?,?,?)',('correction','0','r1','2028-01-02T00:00:00+00:00',encode({'period':'regular_time','home_goals':0,'away_goals':5})))
            after=self.ai_cell(evaluate(store,'2028-01-03T00:00:00Z'))
            self.assertEqual(before,after)
            replay=self.ai_cell(evaluate(store,'2027-12-31T00:00:00Z'))
            self.assertTrue(replay['historical_replay']);self.assertFalse(replay['frozen'])

    def test_holm_does_not_allow_family_shrink_and_bad_score(self):
        with self.assertRaises(ValueError):holm_family([],family_size=1)
        self.assertIsNone(actual_result({'period':'regular_time','home_goals':-1,'away_goals':0},'had'))
        self.assertIsNone(actual_result({'period':'regular_time','home_goals':1,'away_goals':0,'home_half_goals':2,'away_half_goals':0},'hafu'))

    def test_crossleague_holm_family_and_no_pre_registration_samples(self):
        cells=[{'competition_id':cid,'p_value':.0005+i*.0001,'conditions':{'gain':True},'selected':False}
               for i,cid in enumerate(('eng.premier','esp.laliga','ita.serie_a','fra.ligue1','ger.bundesliga'))]
        holm_family(cells)
        self.assertAlmostEqual(cells[0]['adjusted_p_value'],.0375)
        self.assertEqual(cells[-1]['status'],'not_promoted')
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db');self.seed(store,1)
            with store.connect() as db:db.execute("UPDATE observation_jobs SET due_at='2026-11-08T00:00:00+00:00'")
            result=evaluate(store,'2028-01-01T00:00:00Z')
            self.assertEqual(result['cells'],[]);self.assertEqual(result['excluded']['before_registration'],2)

    def rows(self, n=200):
        return [dict(fixture_id=str(i), kickoff=(datetime(2027,1,1,tzinfo=timezone.utc)+timedelta(days=i)).isoformat(),
                     actual='h',base={'h':.4,'d':.3,'a':.3},candidate={'h':.5+(i%7)*.01,'d':.25-(i%7)*.005,'a':.25-(i%7)*.005}) for i in range(n)]

    def test_fixed_first_200_and_failure_coverage(self):
        result=assess_pair(self.rows(201),230)
        self.assertEqual(result['n'],200)
        self.assertAlmostEqual(result['coverage'],200/230)
        self.assertFalse(result['conditions']['coverage'])
        self.assertEqual(result['fixture_ids'][-1],'199')
        self.assertEqual(assess_pair(self.rows(199),200)['status'],'observing')

    def test_holm_keeps_unobserved_family_and_never_selects(self):
        rows=[{'p_value':.001,'conditions':{'gain':True},'selected':False},
              {'p_value':.0001,'conditions':{'gain':True},'selected':False}]
        holm_family(rows)
        self.assertAlmostEqual(rows[1]['adjusted_p_value'],.0075)
        self.assertAlmostEqual(rows[0]['adjusted_p_value'],.074)
        self.assertEqual(rows[0]['status'],'not_promoted')
        self.assertFalse(any(r['selected'] for r in rows))

    def test_no_posthoc_extension_for_short_calendar(self):
        rows=self.rows()
        for r in rows: r['kickoff']='2027-01-01T12:00:00+00:00'
        self.assertEqual(assess_pair(rows,200)['status'],'not_promoted')

    def test_regular_time_and_market_capability(self):
        score={'period':'extra_time','home_goals':2,'away_goals':1}
        self.assertIsNone(actual_result(score,'had'))
        score['period']='regular_time'
        self.assertIsNone(actual_result(score,'hafu'))
        self.assertIsNone(actual_result(score,'hhad',.5))
        self.assertEqual(actual_result(score,'hhad',-1),'d')
        self.assertIsNone(read_evidence(None,None))

    def test_registered_common_cohort_and_future_denominator(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'test.sqlite');w=ObservationWorkspace(store)
            now=datetime(2026,11,10,12,tzinfo=timezone.utc)
            w.pin_experiment('eng.premier',{'evaluation_protocol':PROTOCOL,'evaluation_family':'test-v1'},now-timedelta(hours=1))
            f=dict(id='espn:1',competition_id='eng.premier',home_id='1',away_id='2',home='A',away='B',
                   kickoff=(now+timedelta(hours=1)).isoformat(),status='scheduled')
            w.ingest_fixture(f,now);w.schedule(now)
            w.run_pending(lambda job:{'probabilities':{'had':{'h':.5,'d':.3,'a':.2}}},now,20)
            self.assertEqual(evaluate(store,now)['cells'],[])
            w.settle(f['id'],{'period':'regular_time','home_goals':1,'away_goals':0},now+timedelta(hours=3))
            result=evaluate(store,now+timedelta(hours=3))
            matched=[r for r in result['cells'] if r['node']=='t60' and r['market']=='had']
            self.assertEqual(len(matched),3)
            self.assertTrue(all(r['n']==1 and r['targets']==1 for r in matched))
            # Another cutoff must not be compared with the frozen baseline.
            with store.connect() as db:
                row=db.execute("SELECT id,result FROM observation_jobs WHERE node='t60' AND source='ai'").fetchone()
                import json
                payload=json.loads(row['result']);payload['cutoff']=(now+timedelta(seconds=1)).isoformat()
                db.execute('UPDATE observation_jobs SET result=? WHERE id=?',(encode(payload),row['id']))
            result=evaluate(store,now+timedelta(hours=3))
            self.assertEqual(next(r for r in result['cells'] if r['node']=='t60' and r['source']=='ai')['n'],0)


if __name__=='__main__': unittest.main()
