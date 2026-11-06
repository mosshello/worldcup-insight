from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from scripts.collect_big_five_pitchradar import associate,verify_history,load_verified,SOURCE
from scripts.supplement_big_five import digest
from tests.test_big_five_dataset import canonical
from worldcup_mvp.console.big_five_dataset import prepare


def match():
    return {'id':'31180','date':'2026-08-21 19:00','home':'Arsenal','away':'Coventry',
            'gh':3,'ga':0,'xgh':1.85,'xga':0.56}

def target():
    row=canonical();row.update(season='2026',home_name='Arsenal',away_name='Coventry City',
        kickoff='2026-08-21T19:00Z',home_goals=3,away_goals=0)
    return row

def history():
    return {'Arsenal':[{'date':'2026-08-21','home':True,'gf':3,'ga':0,'xg':1.85,'xga':0.56}],
            'Coventry':[{'date':'2026-08-21','home':False,'gf':0,'ga':3,'xg':0.56,'xga':1.85}]}


class PitchRadarTests(unittest.TestCase):
    def test_source_fallback_cannot_masquerade_as_understat(self):
        r=target();self.assertIs(associate('eng.1',match(),[r]),r)
        for change in [{'id':'bsd31180'},{'src':'bsd'},{'src':None},{'xgh':float('nan')},{'xga':-1}]:
            with self.assertRaises(ValueError):associate('eng.1',{**match(),**change},[r])

    def test_fixture_identity_score_date_and_competition_remain_strict(self):
        for change in [{'date':'2026-08-22 19:00'},{'home':'Coventry','away':'Arsenal'},{'gh':2}]:
            with self.assertRaises(ValueError):associate('eng.1',{**match(),**change},[target()])
        with self.assertRaises(ValueError):associate('esp.1',match(),[target()])
        with self.assertRaises(ValueError):associate('eng.1',match(),[target(),target()])
        row=target();row['training_eligible']=False
        with self.assertRaises(ValueError):associate('eng.1',match(),[row])

    def test_both_club_histories_must_agree(self):
        verify_history(match(),history())
        for key,value in [('xga',0.9),('gf',2),('home',False),('date','2026-08-22')]:
            h=history();h['Arsenal'][0][key]=value
            with self.assertRaises(ValueError):verify_history(match(),h)
        h=history();h['Coventry'].append(deepcopy(h['Coventry'][0]))
        with self.assertRaises(ValueError):verify_history(match(),h)

    def test_partial_responses_and_tampered_bodies_are_not_admitted(self):
        with TemporaryDirectory() as tmp:
            folder=Path(tmp);path=folder/'matches.json';body=json.dumps({'meta':{'source':'understat.com','season':'2026/27'}}).encode();path.write_bytes(body)
            receipt={'url':'https://example.test','http_status':'200','transport_exit':0,'sha256':digest(body)}
            meta=folder/'matches.json.receipt.json';meta.write_text(json.dumps(receipt))
            self.assertEqual(load_verified(folder,'matches.json','https://example.test')[0]['meta']['source'],'understat.com')
            receipt['transport_exit']=28;meta.write_text(json.dumps(receipt))
            with self.assertRaises(ValueError):load_verified(folder,'matches.json','https://example.test')
            receipt['transport_exit']=0;meta.write_text(json.dumps(receipt));path.write_bytes(body+b' ')
            with self.assertRaises(ValueError):load_verified(folder,'matches.json','https://example.test')

    def test_import_preserves_unknown_timezone_and_disables_feature(self):
        r=target();r['supplemental_postmatch_facts']={SOURCE:{'xg':[1.85,0.56],'archive_timezone':None,
            'archive_timestamp':'2026-08-21 19:00','precision_decimal_places':2,'approved_as_training_feature':True,
            'identity_exception':{'exact_kickoff_verified':False}}}
        d={'manifest':{'competition_id':'eng.premier','source':'eng.1'},'matches':[r],'coverage':[]}
        fact=prepare(d,'a'*64,'user')['matches'][0]['postmatch_xg_sources'][SOURCE]
        self.assertIsNone(fact['archive_timezone']);self.assertFalse(fact['approved_as_training_feature'])
        self.assertFalse(fact['identity_exception']['exact_kickoff_verified'])

if __name__=='__main__':unittest.main()
