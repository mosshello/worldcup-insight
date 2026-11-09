import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from datetime import datetime,timedelta,timezone
from worldcup_mvp.console.store import Store
from worldcup_mvp.console.observations import ObservationWorkspace,validate_result

class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.w=ObservationWorkspace(Store(Path(self.tmp.name)/'db.sqlite'))
        self.now=datetime(2026,11,9,12,tzinfo=timezone.utc)
        self.f={'id':'espn:1','competition_id':'eng.premier','home_id':'1','away_id':'2','home':'甲','away':'乙','kickoff':(self.now+timedelta(hours=1)).isoformat(),'status':'scheduled'}
        self.w.ingest_fixture(self.f,self.now)
        self.w.pin_experiment('eng.premier',{'base':'fixed-v1'},self.now-timedelta(hours=1))
    def result(self,job):
        return {'probabilities':{'had':{'h':.5,'d':.3,'a':.2}},'selections':{'had':['h']},'model_id':'fixed-v1'}

    def test_enrollment_preserves_archives_and_opens_only_on_new_quote(self):
        self.f['kickoff']=(self.now+timedelta(hours=2)).isoformat();self.w.ingest_fixture(self.f,self.now)
        q={'source':'test','market':'had','captured_at':self.now.isoformat(),'options':[{'key':'h','odds':2},{'key':'d','odds':3},{'key':'a','odds':4}]}
        self.w.ingest_quotes(self.f['id'],q,self.now);self.w.schedule(self.now)
        self.w.run_pending(lambda job:{'status':'failed','error':'old failure'} if job['source']=='ai' else self.result(job),self.now,4)
        with self.w.store.connect() as db:
            old=[dict(r) for r in db.execute("SELECT * FROM observation_jobs WHERE node='opening' ORDER BY id")]
        at=self.now+timedelta(seconds=2);new=self.w.pin_experiment('eng.premier',{'base':'v3'},at)
        enrollment=self.w.enroll_experiment(self.f['id'],new,at,'用户要求启用新事实实验')
        self.assertEqual(len(enrollment['retired_job_ids']),8)
        self.assertEqual(enrollment,self.w.enroll_experiment(self.f['id'],new,at,'retry'))
        self.assertEqual(self.w.schedule(at),8)
        with self.w.store.connect() as db:
            self.assertEqual(old,[dict(r) for r in db.execute("SELECT * FROM observation_jobs WHERE node='opening' ORDER BY id")])
            self.assertEqual(db.execute("SELECT count(*) FROM observation_jobs WHERE status='pending' AND experiment_id!=?",(new,)).fetchone()[0],0)
        q['captured_at']=(at+timedelta(seconds=1)).isoformat();self.w.ingest_quotes(self.f['id'],q,at+timedelta(seconds=1))
        self.assertEqual(self.w.schedule(at+timedelta(seconds=1)),4)
        self.w.run_pending(self.result,at+timedelta(seconds=1),4)
        jobs=[j for j in self.w.detail(self.f['id'])['observations'] if j['experiment_id']==new and j['node']=='opening']
        self.assertEqual(len(jobs),4);self.assertEqual({j['label'] for j in jobs},{'本实验首次采集'})
        self.assertEqual(len({j['result']['cutoff'] for j in jobs}),1)
        self.assertEqual(ObservationWorkspace(self.w.store).schedule(at+timedelta(seconds=2)),0)

    def test_enrollment_rejects_running_crossleague_started_and_missing_reason(self):
        self.w.schedule(self.now);new=self.w.pin_experiment('eng.premier',{'base':'v3'},self.now)
        foreign=self.w.pin_experiment('esp.laliga',{'base':'foreign'},self.now)
        with self.assertRaises(ValueError):self.w.enroll_experiment(self.f['id'],foreign,self.now,'switch')
        with self.assertRaises(ValueError):self.w.enroll_experiment(self.f['id'],new,self.now,'')
        with self.w.store.connect() as db:db.execute("UPDATE observation_jobs SET status='running' WHERE node='t60' AND source='base'")
        with self.assertRaises(ValueError):self.w.enroll_experiment(self.f['id'],new,self.now,'switch')
        with self.w.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM observation_enrollments').fetchone()[0],0)
            self.assertEqual(db.execute("SELECT count(*) FROM observation_jobs WHERE status='cancelled'").fetchone()[0],0)
        with self.assertRaises(ValueError):self.w.enroll_experiment(self.f['id'],new,self.now+timedelta(hours=2),'switch')

    def test_late_enrollment_records_missed_node_without_backfill(self):
        self.w.schedule(self.now)
        at=self.now+timedelta(minutes=10);new=self.w.pin_experiment('eng.premier',{'base':'v3'},at)
        self.w.enroll_experiment(self.f['id'],new,at,'switch')
        unscheduled=self.w.read(node='t60')['rows'][0]['observations']['t60']
        self.assertEqual(unscheduled['status'],'missed');self.assertIn('登记前',unscheduled['error'])
        self.w.schedule(at)
        jobs=[j for j in self.w.detail(self.f['id'])['observations'] if j['experiment_id']==new]
        self.assertEqual({j['status'] for j in jobs if j['node']=='t60'},{'missed'})
        self.assertTrue(all('登记前' in j['result']['error'] for j in jobs if j['node']=='t60'))
        self.assertEqual({j['status'] for j in jobs if j['node']=='t15'},{'pending'})
        self.assertEqual(self.w.run_pending(self.result,at,20),[])
    def test_schedule_idempotent_and_fixed_results(self):
        self.assertEqual(self.w.schedule(self.now),8)
        self.assertEqual(self.w.schedule(self.now),0)
        self.assertEqual(len(self.w.run_pending(self.result,self.now,limit=10)),4)
        self.assertEqual(self.w.run_pending(self.result,self.now),[])
        self.assertEqual(self.w.read()['summary']['completed'],1)
        self.assertEqual(len(self.w.detail(self.f['id'])['observations']),8)
    def test_late_does_not_impersonate_node(self):
        self.w.schedule(self.now+timedelta(seconds=120)); self.w.run_pending(self.result,self.now+timedelta(seconds=120))
        self.w.settle(self.f['id'],{'period':'regular_time','home_goals':1,'away_goals':0},self.now+timedelta(hours=3))
        s=self.w.read()['summary']; self.assertEqual(s['late'],1); self.assertEqual(s['scored'],0)
    def test_missed_and_no_postmatch_forecast(self):
        self.w.schedule(self.now+timedelta(minutes=5))
        self.assertEqual(self.w.read()['summary']['missed'],1)
        self.w.schedule(self.now+timedelta(hours=2))
        self.assertEqual(self.w.run_pending(self.result,self.now+timedelta(hours=2)),[])
    def test_identity_changes_cancel_only_old_jobs(self):
        self.w.schedule(self.now)
        changed={**self.f,'kickoff':(self.now+timedelta(hours=5)).isoformat()}
        self.w.ingest_fixture(changed,self.now)
        self.w.schedule(self.now)
        jobs=self.w.detail(self.f['id'])['observations']
        self.assertEqual(sum(j['status']=='cancelled' for j in jobs),8)
        self.assertEqual(sum(j['status']=='pending' for j in jobs),8)
        with self.assertRaises(ValueError): self.w.ingest_fixture({**self.f,'competition_id':'esp.laliga'},self.now)
    def quote(self,**kwargs):
        return dict(source='test',market='had',line=None,options=[{'key':'h','odds':2},{'key':'d','odds':3},{'key':'a','odds':4}],captured_at=self.now.isoformat(),**kwargs)
    def test_quote_timestamps_line_and_first_seen(self):
        q=self.quote(published_at=None)
        self.w.ingest_quotes(self.f['id'],q,self.now); self.w.ingest_quotes(self.f['id'],q,self.now)
        self.w.schedule(self.now)
        details=self.w.detail(self.f['id']); self.assertEqual(len(details['quotes']),1)
        self.assertIsNone(details['quotes'][0]['published_at'])
        self.assertTrue(all(j['label']=='首次采集' for j in details['observations'] if j['node']=='opening'))
        q2={**q,'line':1,'captured_at':(self.now+timedelta(seconds=1)).isoformat()}
        self.w.ingest_quotes(self.f['id'],q2,self.now+timedelta(seconds=1))
        self.assertEqual(self.w.detail(self.f['id'])['events'][0]['kind'],'line_change')
        with self.assertRaises(ValueError): self.w.ingest_quotes(self.f['id'],{**q,'published_at':(self.now+timedelta(seconds=1)).isoformat()},self.now)
    def test_single_multi_and_revision_settlement(self):
        self.w.schedule(self.now); self.w.run_pending(self.result,self.now)
        self.w.settle(self.f['id'],{'period':'regular_time','home_goals':2,'away_goals':1},self.now+timedelta(hours=3))
        summary=self.w.read()['summary']; self.assertEqual(summary['single_accuracy'],1); self.assertEqual(summary['single_scored'],1); self.assertEqual(summary['multi_scored'],0)
        self.w.settle(self.f['id'],{'period':'regular_time','home_goals':0,'away_goals':1},self.now+timedelta(hours=4))
        self.assertEqual(self.w.read()['summary']['single_accuracy'],0)
        self.assertEqual(self.w.read(node='t15')['summary']['scored'],0)
    def test_invalid_probability_and_failure_not_fake_prediction(self):
        with self.assertRaises(ValueError): validate_result({'probabilities':{'had':{'h':.5,'d':.3,'a':.3}}})
        self.w.schedule(self.now)
        self.w.run_pending(lambda _: {'probabilities':{}},self.now)
        self.assertEqual(self.w.read()['summary']['failed'],1)
    def test_restart_recovers_expired_lease(self):
        self.w.schedule(self.now)
        with self.w.store.connect() as db:
            db.execute("UPDATE observation_jobs SET status='running',lease_until=? WHERE node='t60'",((self.now-timedelta(seconds=1)).isoformat(),))
        again=ObservationWorkspace(self.w.store)
        self.assertEqual(len(again.run_pending(self.result,self.now,10)),4)
    def test_two_competitions_do_not_mix(self):
        other={**self.f,'id':'espn:2','competition_id':'esp.laliga'}
        self.w.ingest_fixture(other,self.now); self.w.pin_experiment('esp.laliga',{'base':'spain'},self.now)
        self.w.schedule(self.now)
        seen=[]
        def run(job): seen.append((job['fixture']['competition_id'],job['experiment']['base'])); return self.result(job)
        self.w.run_pending(run,self.now,20)
        self.assertEqual(set(seen),{('eng.premier','fixed-v1'),('esp.laliga','spain')})
        self.assertEqual(self.w.read(competition='eng.premier')['summary']['targets'],1)

    def test_common_comparison_does_not_mix_cutoffs(self):
        self.w.schedule(self.now); self.w.run_pending(self.result,self.now,20)
        self.w.settle(self.f['id'],{'period':'regular_time','home_goals':2,'away_goals':1},self.now+timedelta(hours=3))
        self.assertEqual(self.w.compare()['common_n'],1)
        with self.w.store.connect() as db:
            row=db.execute("SELECT id,result FROM observation_jobs WHERE source='ai' AND node='t60'").fetchone()
            import json
            r=json.loads(row['result']); r['cutoff']=(self.now+timedelta(seconds=1)).isoformat()
            db.execute('UPDATE observation_jobs SET result=? WHERE id=?',(json.dumps(r),row['id']))
        self.assertEqual(self.w.compare()['common_n'],0)
        self.assertEqual(self.w.compare()['pairs']['market']['base']['n'],1)

    def test_multiselect_and_unsupported_denominators(self):
        self.w.schedule(self.now)
        def runner(job):
            r=self.result(job); r['selections']['had']=['h','d']; return r
        self.w.run_pending(runner,self.now,20)
        self.w.settle(self.f['id'],{'period':'regular_time','home_goals':0,'away_goals':0},self.now+timedelta(hours=3))
        s=self.w.read()['summary']; self.assertIsNone(s['single_accuracy']); self.assertEqual(s['multi_coverage'],1)
        self.assertEqual(self.w.read(market='hafu')['summary']['unsupported'],1)

    def test_fixture_changes_during_runner_rejects_result(self):
        self.w.schedule(self.now)
        def runner(job):
            self.w.ingest_fixture({**self.f,'status':'live'},self.now); return self.result(job)
        self.w.run_pending(runner,self.now,1)
        observations=self.w.detail(self.f['id'])['observations']
        self.assertEqual(sum(j['status']=='completed' for j in observations),0)

    def test_market_and_prediction_invalid_inputs(self):
        for value in (float('nan'),True,0,1):
            q=self.quote(); q['options'][0]['odds']=value
            with self.assertRaises(ValueError): self.w.ingest_quotes(self.f['id'],q,self.now)
        with self.assertRaises(ValueError): self.w.settle(self.f['id'],{'home_goals':1,'away_goals':0,'period':'extra_time'},self.now+timedelta(hours=3))
        with self.assertRaises(ValueError): self.w.ingest_fixture({**self.f,'kickoff':'2026-11-09T13:00:00'},self.now)

    def test_shared_cutoff_quotes_survive_multiple_batches(self):
        self.w.schedule(self.now)
        self.w.ingest_quotes(self.f['id'],self.quote(),self.now)
        seen=[]
        def run(job): seen.append(job); return self.result(job)
        self.w.run_pending(run,self.now+timedelta(seconds=25),1)
        later={**self.quote(),'captured_at':(self.now+timedelta(seconds=35)).isoformat(),'options':[{'key':'h','odds':1.9},{'key':'d','odds':3},{'key':'a','odds':4}]}
        self.w.ingest_quotes(self.f['id'],later,self.now+timedelta(seconds=35))
        self.w.run_pending(run,self.now+timedelta(seconds=45),20)
        t60=[j for j in seen if j['node']=='t60']
        self.assertEqual(len(t60),4)
        self.assertEqual(len({j['cutoff'] for j in t60}),1)
        self.assertTrue(all(len(j['quotes'])==1 for j in t60))
        self.w.settle(self.f['id'],{'period':'regular_time','home_goals':2,'away_goals':0},self.now+timedelta(hours=3))
        self.assertEqual(self.w.compare()['common_n'],1)

    def test_elapsed_work_uses_real_batch_progress(self):
        self.w.schedule(self.now); elapsed=[0.0]
        def run(job): elapsed[0]+=40; return self.result(job)
        with patch('worldcup_mvp.console.observations.time.monotonic',side_effect=lambda:elapsed[0]):
            self.w.run_pending(run,self.now,4)
        jobs=[j for j in self.w.detail(self.f['id'])['observations'] if j['node']=='t60']
        from worldcup_mvp.console.observations import stamp
        completed=sorted((stamp(j['result']['created_at'])-self.now).total_seconds() for j in jobs)
        self.assertEqual(completed,[40,80,120,160])
        self.assertEqual(len({j['result']['cutoff'] for j in jobs}),1)

    def test_deadline_uses_completion_and_errors_are_safe(self):
        self.w.schedule(self.now); elapsed=[0.0]
        def run(job): elapsed[0]+=181; return self.result(job)
        with patch('worldcup_mvp.console.observations.time.monotonic',side_effect=lambda:elapsed[0]):
            self.assertEqual(self.w.run_pending(run,self.now,1)[0]['status'],'missed')
        def broken(job): raise ValueError('secret=do-not-show')
        self.w.run_pending(broken,self.now,1)
        failures=[j for j in self.w.detail(self.f['id'])['observations'] if j['status']=='failed']
        self.assertTrue(failures)
        self.assertNotIn('secret',failures[0]['result']['error'])
        self.assertIn('校验失败',failures[0]['result']['error'])

    def test_read_invalid_filters_and_finished_without_jobs(self):
        for args in ({'day':'2026-02-30'},{'day':'2026-1-2'},{'competition':'wrong'}):
            with self.assertRaises(ValueError): self.w.read(**args)
        self.w.ingest_fixture(dict(self.f,status='finished'),self.now)
        view=self.w.read();self.assertEqual(view['summary']['missed'],1)
        self.assertEqual(view['rows'][0]['observations']['t60']['status'],'missed')

    def test_freshness_metadata_is_not_price_movement(self):
        q={'source':'test','market':'asian_handicap','line':-.25,'captured_at':self.now.isoformat(),'options':[{'key':'h','odds':1.9,'polled_at':'old'},{'key':'a','odds':1.95}]}
        self.w.ingest_quotes(self.f['id'],q,self.now)
        later=self.now+timedelta(seconds=30)
        q['captured_at']=later.isoformat();q['options'][0]['polled_at']='new'
        self.w.ingest_quotes(self.f['id'],q,later)
        self.assertEqual(self.w.detail(self.f['id'])['events'],[])
        q['options'][0]['odds']=1.85
        self.w.ingest_quotes(self.f['id'],q,later)
        event=self.w.detail(self.f['id'])['events'][0]
        self.assertAlmostEqual(event['price_deltas']['h'],-.05)
        self.assertIsNone(event['duration_seconds'])

    def test_experiment_is_pinned_across_nodes_for_fixture_revision(self):
        self.w.schedule(self.now)
        self.w.pin_experiment('eng.premier',{'base':'new-v2'},self.now+timedelta(seconds=1))
        self.assertEqual(self.w.schedule(self.now+timedelta(seconds=2)),0)
        jobs=self.w.detail(self.f['id'])['observations']
        self.assertEqual(len({j['experiment_id'] for j in jobs}),1)

    def test_detail_active_experiment_is_separate_from_archived_versions(self):
        self.w.schedule(self.now)
        first=self.w.detail(self.f['id'])['active_experiment_id']
        future=self.now+timedelta(seconds=2)
        second=self.w.pin_experiment('eng.premier',{'base':'detail-v2'},future)
        self.w.enroll_experiment(self.f['id'],second,reason='new evidence protocol',now=future)
        detail=self.w.detail(self.f['id'])
        self.assertEqual(detail['active_experiment_id'],second)
        self.assertEqual({r['id'] for r in detail['experiments']},{first,second})
        self.assertEqual([r['id'] for r in detail['experiments'] if r['active']],[second])
        self.assertTrue(all(r['experiment_id']==first for r in detail['observations']))

    def test_parallel_filtered_workers_claim_once_and_share_snapshot(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier, Lock
        self.w.schedule(self.now)
        barrier=Barrier(4); lock=Lock(); claimed=[]
        def run(job):
            with lock: claimed.append((job['id'],job['source'],job['cutoff']))
            barrier.wait(timeout=10)
            return self.result(job)
        with ThreadPoolExecutor(max_workers=4) as pool:
            calls=[pool.submit(self.w.run_pending,run,self.now,1,[source]) for source in ('base','market','enhanced','ai')]
            outputs=[call.result(timeout=15) for call in calls]
        self.assertEqual(len({row[0] for row in claimed}),4)
        self.assertEqual(len({row[2] for row in claimed}),1)
        self.assertTrue(all(output[0]['status']=='completed' for output in outputs))
        self.assertEqual(self.w.run_pending(self.result,self.now,10,['base','market']),[])
        with self.assertRaises(ValueError): self.w.run_pending(self.result,self.now,sources=["base') OR 1=1--"])

    def test_same_source_parallel_workers_do_not_duplicate_claim(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        self.w.schedule(self.now); started=Event(); release=Event()
        def run(job):
            started.set(); self.assertTrue(release.wait(10)); return self.result(job)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(self.w.run_pending,run,self.now,1,['ai'])
            self.assertTrue(started.wait(10))
            second=pool.submit(self.w.run_pending,self.result,self.now,1,['ai'])
            self.assertEqual(second.result(timeout=10),[])
            release.set(); self.assertEqual(first.result(timeout=10)[0]['status'],'completed')

if __name__=='__main__': unittest.main()
