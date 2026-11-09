import json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from worldcup_mvp.console.store import ROOT
from worldcup_mvp.console.observation_personnel_trees import FIELDS,fit,probabilities
from worldcup_mvp.console.observation_personnel_models import immutable,load


class PersonnelTreeTests(unittest.TestCase):
    def setUp(self):
        self.at=datetime(2026,11,9,tzinfo=timezone.utc);self.samples=[]
        for i in range(120):
            h,a=[(2,0),(1,1),(0,2)][i%3]
            vs=[]
            for side in (0,1):
                v={k:float((i+side)%7)/7 for k in FIELDS};v.update(home=1-side,gf=1+i%3,opp_gf=1+(i+1)%3);vs.append(v)
            self.samples.append({'id':str(i),'competition_id':'eng.premier','available_at':(self.at-timedelta(days=1)).isoformat(),
                'vectors':vs,'goals':[h,a],'half_goals':[h//2,a//2]})
        self.samples.append(dict(self.samples[0],id='future',available_at=(self.at+timedelta(days=1)).isoformat()))

    def test_native_model_roundtrip_capability_and_future_label_exclusion(self):
        for kind in ('catboost_had','lightgbm_goals'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory(dir=ROOT/'.ai/tmp') as directory:
                artifact=fit(self.samples,'eng.premier',self.at,kind,{'features':'frozen-reference'})
                self.assertEqual(artifact['train_count'],120)
                self.assertNotIn('future',artifact['train_ids'])
                p=probabilities(artifact,self.samples[0]['vectors'],handicap=-1)
                self.assertEqual(set(p),{'had'} if kind=='catboost_had' else {'had','hhad','ttg','crs','hafu'})
                for market in p.values():self.assertAlmostEqual(sum(market.values()),1,places=7)
                path=immutable(artifact,Path(directory));restored=load(path,'eng.premier')
                q=probabilities(restored,self.samples[0]['vectors'],handicap=-1)
                for market,values in p.items():
                    for key,v in values.items():self.assertAlmostEqual(v,q[market][key],places=10)
                tampered=json.loads((ROOT/path).read_text(encoding='utf-8'));tampered['train_count']=999
                (ROOT/path).write_text(json.dumps(tampered),encoding='utf-8')
                with self.assertRaises(ValueError):load(path,'eng.premier')

    def test_foreign_league_fit_rejected(self):
        with self.assertRaises(ValueError):fit(self.samples,'esp.laliga',self.at,'catboost_had',{})


if __name__=='__main__':unittest.main()
