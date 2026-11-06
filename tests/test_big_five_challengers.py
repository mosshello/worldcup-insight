import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from research_big_five_actual_lines import asian_class, matched, quote
from research_big_five_goal_challengers import grid, distribution, team_features
from research_big_five_score_prices import football, quote as category_quote
from verify_big_five_forward import market_results
from worldcup_mvp.console.sources import EXPECTED_OPTIONS


class ChallengersTest(unittest.TestCase):
    def test_quarter_lines_settle_split_legs_from_home_perspective(self):
        delta=np.array([-1,0,1])
        for line,expected in [(-.75,[0,0,3]),(-.25,[0,1,4]),(0,[0,2,4]),(.25,[0,3,4]),(.75,[1,4,4])]:
            self.assertEqual(asian_class(delta,line).tolist(),expected)
        with self.assertRaises(ValueError):
            asian_class(delta,.33)

    def test_official_quote_uses_beijing_cutoff_and_rejects_conflicts(self):
        q=dict(h="2.6",d="3.7",a="1.9",goalLine="-1",updateDate="2026-10-31")
        at=datetime(2026,10,31,12,tzinfo=timezone.utc)
        rows=[dict(q,updateTime="19:59:59"),dict(q,updateTime="20:00:01",goalLine="-2")]
        self.assertEqual(quote(rows,at)["line"],-1)
        self.assertIsNone(quote(rows+[dict(rows[0],h="2.7")],at))

    def test_identity_uses_source_local_day_and_vetoes_score_conflict(self):
        rows=[];source=[]
        for i in range(16):
            t=datetime(2026,8,1,20,tzinfo=timezone.utc)+timedelta(days=3*i)
            h,a=("A","B") if i%2==0 else ("B","A")
            source.append(dict(home=h.lower(),away=a.lower(),date=(t+timedelta(hours=8)).date(),goals=[1,0]))
            rows.append(dict(home_id=h,away_id=a,kickoff=t.isoformat(),home_goals=1,away_goals=0,id=str(i)))
        source[-1]["goals"]=[0,0]
        matches,info=matched(source,rows,8)
        self.assertEqual(len(matches),15)
        self.assertEqual(info["excluded"]["score_conflict"],1)
        self.assertFalse(matched(source,rows,0)[0])

    def test_forward_inputs_stop_at_capture_even_if_match_is_days_later(self):
        capture=datetime(2026,11,6,tzinfo=timezone.utc)
        kickoff=capture+timedelta(days=2)
        rows=[]
        for i in range(7):
            at=capture-timedelta(days=42-6*i)
            rows.append(dict(id=str(i),time=at,available=at+timedelta(days=7),advanced_at=at+timedelta(days=7),
                             gf=1,ga=2,xg=[1.,2.],load=.08,observed=None))
        earlier=team_features(rows,kickoff,capture)
        rows[-1].update(gf=100,xg=[100.,0.],load=1.)
        self.assertEqual(team_features(rows,kickoff,capture),earlier)
        self.assertNotIn("6",earlier[1]["results"])
        self.assertEqual(earlier[1]["rest_from"],"6")

    def test_dispersed_grid_keeps_probability_mass_and_tail(self):
        m={"fields":["home"],"mean":[0.],"scale":[1.],"coef":[.2],"intercept":.3,"dispersion":.23}
        item={"vectors":[{"home":1},{"home":0}]}
        mat=grid(m,item);d=distribution(mat)
        self.assertAlmostEqual(mat.sum(),1)
        self.assertAlmostEqual(sum(d["had"]),1)
        self.assertAlmostEqual(sum(d["goals"]),1)

    def test_score_prices_and_other_score_bins_are_compatible(self):
        keys=list(EXPECTED_OPTIONS["crs"])
        at=datetime(2026,10,31,12,tzinfo=timezone.utc)
        odds={k.replace("s1s","s-1s") if k.startswith("s1s") else k:"16" for k in keys}
        odds.update(updateDate="2026-10-31",updateTime="19:59:59")
        self.assertAlmostEqual(sum(category_quote([odds],keys,at)["p"]),1)
        mat=np.zeros((8,8));mat[7,0]=.6;mat[0,0]=.4
        probs=football(mat,"crs")
        self.assertAlmostEqual(probs[keys.index("s1sh")],.6)
        archive={"markets":{"crs":{"keys":keys,"blend":probs,"quote":{"p":probs}},
                            "upset":{"probability":.25,"underdog":0,"alarm":True}}}
        settled=market_results(archive,7,0,at)
        self.assertEqual(settled["crs"]["outcome"],"s1sh")
        self.assertEqual(settled["upset"]["occurred"],1)
