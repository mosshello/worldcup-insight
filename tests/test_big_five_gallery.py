from copy import deepcopy
import unittest
from scripts.collect_big_five_gallery import parse_page, match_fixture
from tests.test_big_five_dataset import canonical


def match():
    return {'k':'EPL-2024-us:1','u':'epl-2024-us-1','c':'EPL','h':'home','a':'away',
            'hc':'us:1','ac':'us:2','d':'2025-01-01','t':'2025-01-01T15:00Z',
            'hg':2,'ag':1,'aet':None,'so':None}


def page():
    return b'''<script type="application/ld+json">{"@type":"SportsEvent","homeTeam":{"name":"home"},"awayTeam":{"name":"away"},"startDate":"2025-01-01T15:00Z","url":"https://iyush.dev/football/gallery/match/epl-2024-us-1"}</script>
<dt>Match ID</dt><dd>EPL-2024-us:1</dd><span class="sr-only">2 to 1</span>
<tr><th scope="row">xG<small>Understat \xc2\xb7 context only, not in the rating</small></th><td class="num">0.00</td><td class="num">1.20</td></tr>'''


class GalleryTests(unittest.TestCase):
    def test_explicit_provider_row_and_zero_are_required(self):
        self.assertEqual(parse_page(page(),match()),[0,1.2])
        for invalid in [page().replace(b'Understat',b'Custom model'),page().replace(b'0.00',b'NA'),page()+page()]:
            with self.assertRaises(ValueError):parse_page(invalid,match())

    def test_page_crosschecks_identity_and_score(self):
        for invalid in [page().replace(b'2 to 1',b'2 to 2'),page().replace(b'us:1',b'us:2'),page().replace(b'15:00Z',b'16:00Z')]:
            with self.assertRaises(ValueError):parse_page(invalid,match())

    def test_exact_kickoff_score_and_competition_join(self):
        m=match();r=canonical()
        self.assertEqual(match_fixture('eng.1',m,[r],{})['id'],r['id'])
        for change in [{'t':'2025-01-01T15:15Z'},{'hg':3},{'c':'La_Liga'},{'aet':'2-1'}]:
            with self.assertRaises(ValueError):match_fixture('eng.1',{**m,**change},[r],{})
        with self.assertRaises(ValueError):match_fixture('eng.1',m,[r,r],{})

    def test_known_source_team_id_cannot_be_reassigned(self):
        with self.assertRaisesRegex(ValueError,'mapping_conflict'):
            match_fixture('eng.1',match(),[canonical()],{'understat:1':{'espn_id':'espn:wrong'}})
        r=deepcopy(canonical());r['training_eligible']=False
        with self.assertRaisesRegex(ValueError,'not_eligible'):match_fixture('eng.1',match(),[r],{})

    def test_import_retains_rounded_provenance_but_never_enables_feature(self):
        from worldcup_mvp.console.big_five_dataset import prepare
        r=canonical();r['supplemental_postmatch_facts']={'understat_gallery_archive':{'xg':[0,1.2],
            'precision_decimal_places':2,'source_kind':'third_party_display_archive_of_understat',
            'index_receipt':{'sha256':'a'*64},'approved_as_training_feature':True}}
        data={'manifest':{'competition_id':'eng.premier','source':'eng.1'},'matches':[r],'coverage':[]}
        value=prepare(data,'b'*64,'user request')['matches'][0]['postmatch_xg_sources']['understat_gallery_archive']
        self.assertFalse(value['approved_as_training_feature']);self.assertEqual(value['precision_decimal_places'],2)
        self.assertEqual(value['index_receipt']['sha256'],'a'*64)


if __name__=='__main__':unittest.main()
