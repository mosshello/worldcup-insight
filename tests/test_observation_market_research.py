import copy
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from worldcup_mvp.console.observation_market_research import analyze_timeline,fit_rule,apply_rule,save_artifact

class MarketResearchTests(unittest.TestCase):
    def setUp(self):
        self.start=datetime(2026,11,1,10,tzinfo=timezone.utc)
        self.f={'id':'f1','competition_id':'eng.premier','kickoff':(self.start+timedelta(hours=1)).isoformat()}
    def quote(self,seconds,price=2,**extra):
        at=(self.start+timedelta(seconds=seconds)).isoformat()
        return {'market':'asian_handicap','source':'book','provider_market_id':'938','period':'regular_time','line':-.25,
            'captured_at':at,'source_checked_at':at,'options':[{'key':'h','odds':price},{'key':'a','odds':1.9}],**extra}
    def test_observed_exposure_no_duplicate_or_gap_interpolation(self):
        quotes=[self.quote(0),self.quote(60),self.quote(120,source_checked_at=self.start.isoformat()),self.quote(600),self.quote(660)]
        r=analyze_timeline(self.f,quotes,self.f['kickoff'])
        self.assertEqual(r['covered_seconds'],120)
        self.assertFalse(r['continuous_coverage_claim'])
        self.assertEqual(sum(b['observed_fixture_seconds'] for b in r['buckets']),120)
    def test_line_provider_and_suspension_isolation(self):
        rows=[self.quote(0),self.quote(60,suspended=True),self.quote(120),self.quote(180,line=-.5),self.quote(240,source='other')]
        self.assertEqual(analyze_timeline(self.f,rows,self.f['kickoff'])['covered_seconds'],0)
    def test_persistence_reversal_and_censoring(self):
        rows=[self.quote(0,2),self.quote(60,1.9),self.quote(120,1.9),self.quote(180,1.95),self.quote(240,1.95)]
        r=analyze_timeline(self.f,rows,self.f['kickoff'],horizon_seconds=120)
        e=r['events'][0]
        self.assertAlmostEqual(e['amplitude'],.1);self.assertEqual(e['sampled_persistence_lower_seconds'],60)
        self.assertIsNotNone(e['reversal_interval']);self.assertFalse(e['target_persisted'])
        self.assertTrue(r['events'][1]['reversal_from_previous']);self.assertIsNone(r['events'][1]['target_persisted'])
        short=analyze_timeline(self.f,rows,self.start+timedelta(seconds=120),horizon_seconds=120)
        self.assertIsNone(short['events'][0]['target_persisted'])
    def events(self):
        rows=[]
        for phase,day in [('train',1),('val',3)]:
            for i in range(40):
                at=datetime(2026,11,day,10,tzinfo=timezone.utc)+timedelta(seconds=i)
                rows.append({'fixture_id':f'{phase}{i}','competition_id':'eng.premier','observed_at':at.isoformat(),
                    'label_available_at':(at+timedelta(minutes=5)).isoformat(),'kickoff':(at+timedelta(hours=1)).isoformat(),
                    'amplitude':.1 if i<20 else .03,'target_persisted':i<20,'direction':'drop','side':'h'})
        return rows
    def fit(self,rows):return fit_rule(rows,'eng.premier','2026-11-02T00:00:00Z','2026-11-04T00:00:00Z')
    def test_train_validation_and_immutable_artifact(self):
        r=self.fit(self.events());self.assertTrue(r['monitoring_validated']);self.assertFalse(r['production_enabled'])
        self.assertEqual(r['threshold'],.05)
        self.assertFalse(set(r['train_fixture_ids'])&set(r['validation_fixture_ids']))
        self.assertEqual(r,self.fit(list(reversed(self.events()))))
        with tempfile.TemporaryDirectory() as tmp:
            p=save_artifact(r,tmp);self.assertEqual(p,save_artifact(r,tmp))
            bad=copy.deepcopy(r);bad['threshold']=.001
            with self.assertRaises(ValueError):save_artifact(bad,tmp)
        e={'competition_id':'eng.premier','observed_at':'2026-11-05T00:00:00Z','direction':'drop','amplitude':.1}
        self.assertTrue(apply_rule(r,e));self.assertFalse(apply_rule(r,dict(e,competition_id='esp.laliga')))
        self.assertFalse(apply_rule(r,dict(e,observed_at='2026-11-03T00:00:00Z')))
    def test_validation_cannot_choose_threshold_and_insufficient_disabled(self):
        events=self.events();old=self.fit(events)
        for e in events[40:]:e['target_persisted']=not e['target_persisted']
        r=self.fit(events);self.assertEqual(r['threshold'],old['threshold']);self.assertFalse(r['monitoring_validated'])
        empty=self.fit([]);self.assertEqual(empty['status'],'insufficient_training');self.assertIsNone(empty['threshold'])
        with self.assertRaises(ValueError):fit_rule([], 'eng.premier','2026-11-02T00:00:00Z','2026-11-04T00:00:00Z',{'min_train':1})
    def test_future_crossleague_and_fixture_duplicates_excluded(self):
        events=self.events();events+=copy.deepcopy(events)
        events.append(dict(events[0],fixture_id='foreign',competition_id='esp.laliga'))
        events.append(dict(events[0],fixture_id='future',label_available_at='2027-01-01T00:00:00Z'))
        r=self.fit(events);self.assertEqual(r['train']['n'],40);self.assertEqual(r['validation_baseline']['n'],40)
        self.assertEqual(r['rejected']['other_competition'],1)

if __name__=='__main__':unittest.main()
