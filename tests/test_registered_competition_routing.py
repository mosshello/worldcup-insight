import copy
import unittest
from unittest.mock import patch

from worldcup_mvp.console import sources, live


class RegisteredCompetitionRoutingTests(unittest.TestCase):
    def test_registered_provider_mapping_is_dynamic_and_can_disable_legacy_route(self):
        spec = {'id':'new.competition','providers':{'sporttery':'NEW','espn':'new.slug','espn_id':'123'}}
        with patch('worldcup_mvp.console.catalog.catalog',return_value=[spec]):
            self.assertEqual(sources.league_for('NEW'),'new.slug')
            self.assertEqual(live.group_key({'league_code':'NEW','kickoff':'2026-11-05T01:00:00+08:00'}),('new.slug','20261104'))
            self.assertEqual(sources.league_for('ED1'),'eng.2')
        with patch('worldcup_mvp.console.catalog.catalog',return_value=[{'providers':{'sporttery':'ED1'}}]):
            self.assertIsNone(sources.league_for('ED1'))

    def test_board_and_event_bind_verified_competition_id(self):
        spec={'providers':{'espn':'new.slug','espn_id':'123'}}
        board={'leagues':[{'id':'123','slug':'new.slug'}],
               'events':[{'id':'456','uid':'s:600~l:123~e:456'}]}
        with patch('worldcup_mvp.console.catalog.catalog',return_value=[spec]):
            with patch.object(live,'get_json',return_value=board):
                self.assertEqual(live.fetch_group(('new.slug','20261104')),board['events'])
            for change in (lambda b:b['leagues'][0].update(id='999'),lambda b:b['events'][0].update(uid='s:600~l:999~e:456')):
                wrong=copy.deepcopy(board);change(wrong)
                with patch.object(live,'get_json',return_value=wrong),self.assertRaises(sources.SourceError):
                    live.fetch_group(('new.slug','20261104'))

    def test_verified_aliases_are_competition_scoped(self):
        self.assertEqual(sources.league_for('UEC'),'uefa.europa')
        for league,home,away,eh,ea in [('eng.2','BRT','WLV','BRC','WOL'),('uefa.europa','REN','OLP','REN','OLY')]:
            match={'home_code':home,'away_code':away,'kickoff':'2026-11-04T20:00:00Z'}
            event={'id':'1','date':match['kickoff'],'competitions':[{'competitors':[
                {'homeAway':'home','team':{'abbreviation':eh}}, {'homeAway':'away','team':{'abbreviation':ea}}]}]}
            self.assertEqual(sources.match_event(match,[event],league),event)
            self.assertIsNone(sources.match_event(match,[event],'other.league'))
