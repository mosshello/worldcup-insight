import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from tests.test_console_domain import fixture
from worldcup_mvp.console import sources, briefing, match_facts, intelligence, public_enrichment, postmatch
from worldcup_mvp.console import fundamentals
from worldcup_mvp.console.quality import assess
from worldcup_mvp.console.service import ConsoleService
from worldcup_mvp.console.store import Store


class CompletenessTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.match = sources.normalize_match(fixture())
        self.match.update(kickoff=(self.now+timedelta(hours=3)).isoformat(), captured_at=self.now.isoformat(), sale_status='selling')
        for market in self.match['markets'].values():
            market.update(sale_status='selling', updated_at=self.now.isoformat())
        self.detail = {'markets': self.match['markets'], 'history': {}, 'odds_fresh': True,
                       'captured_at': self.now.isoformat(), 'context': {'recent': [[], []], 'lineups': []}}
        self.detail['quality'] = assess(self.match, self.detail, self.now)

    def test_preview_is_readonly_and_explains_unavailable_model(self):
        from tests.test_prediction_readiness import ready_context
        self.detail['context'] = ready_context(self.match, self.now)
        self.detail['quality'] = assess(self.match, self.detail, self.now)
        before = copy.deepcopy(self.detail)
        result = briefing.build(self.match, self.detail, {'status':'unavailable','reason':'本赛事未训练'}, self.now)
        self.assertIsNotNone(result['preview'])
        self.assertFalse(result['is_archived'])
        self.assertEqual(self.detail, before)
        self.assertTrue(any(r['summary']=='本赛事未训练' for r in result['coverage']))
        self.assertAlmostEqual(sum(result['preview']['probabilities']), 100, places=1)

    def test_stale_or_inplay_never_makes_pregame_preview(self):
        for change in ({'captured_at':(self.now-timedelta(hours=1)).isoformat()},
                       {'live_score':{'state':'live'}}, {'live_score':{'state':'completed'}}):
            result = briefing.build(self.match, {**self.detail, **change}, as_of=self.now)
            self.assertIsNone(result['preview'])

    def test_recent_samples_reject_future_duplicate_and_invalid_scores(self):
        row={'id':'a','date':(self.now-timedelta(days=1)).isoformat(),'gf':2,'ga':1}
        rows=[row, row, {**row,'id':'future','date':(self.now+timedelta(days=1)).isoformat()},
              {**row,'id':'bad','gf':float('nan')}]
        self.assertEqual(briefing.rows_before(rows, self.now), [row])

    def test_date_only_does_not_invent_rest_hours(self):
        self.detail['context']['recent'] = [[{'date':'2026-11-01','date_precision':'date','gf':1,'ga':0}], []]
        value = intelligence.build(self.match, self.detail, self.now)
        self.assertNotIn('rest_hours', value['lineups'][0])

    def test_jleague_enrichment_keeps_process_and_schedule_identity(self):
        base = {'available':True, 'recent':[[{'id':'league-only'}],[]], 'coverage':'league_only'}
        extra = {'available':True, 'provider_ids':{'home':'1','away':'2'}, 'event_id':'verified',
                 'match_facts':{'events':[{'id':'goal'}]}, 'head_to_head':[{'id':'past'}],
                 'lineups':[], 'recent':[[],[]]}
        with patch('worldcup_mvp.console.league_data.historical_context', return_value=base), \
             patch('worldcup_mvp.console.sources.fetch_espn_context', return_value=extra):
            result = fundamentals._base_context({**self.match, 'league_code':'JD1'})
        self.assertEqual(result['provider_ids'], extra['provider_ids'])
        self.assertEqual(result['match_facts'], extra['match_facts'])
        self.assertEqual(result['head_to_head'], extra['head_to_head'])
        self.assertEqual(result['recent'][0], [{'id':'league-only'}])

    def test_process_rejects_other_teams_invalid_stats_and_shootouts(self):
        summary={'header':{'id':'event'},'boxscore':{'teams':[
            {'team':{'id':'1'},'statistics':[{'name':'totalShots','displayValue':'17'}, {'name':'possessionPct','displayValue':'nan'}]},
            {'team':{'id':'9'},'statistics':[{'name':'totalShots','displayValue':'99'}]}]}, 'keyEvents':[]}
        event={'id':'g','type':{'type':'goal'},'team':{'id':'1'},'period':{'number':2},'clock':{'value':200,'displayValue':"49'"}}
        summary['keyEvents']=[event,event,{**event,'id':'wrong','team':{'id':'9'}}, {**event,'id':'pen','shootout':True}]
        result=match_facts.extract(summary,self.match,{'home':{'id':'1'},'away':{'id':'2'}},self.now.isoformat())
        self.assertEqual(len(result['events']),1)
        self.assertEqual(result['statistics'],[{'code':'totalShots','label':'射门','home':'17','away':None}])

    def test_head_to_head_rejects_unrelated_or_future_fixture(self):
        event={'id':'x','date':(self.now-timedelta(days=5)).isoformat(),'competitionName':'2026-27 English Carabao Cup',
               'statusType':{'completed':True,'name':'STATUS_FULL_TIME'}, 'competitors':[
                   {'homeAway':'home','team':{'id':'2'},'score':'2'}, {'homeAway':'away','team':{'id':'1'},'score':'1'}]}
        summary={'seasonseries':[{'type':'head-to-head','events':[event, {**event,'id':'future','date':(self.now+timedelta(days=10)).isoformat()}]}]}
        result=match_facts.head_to_head(summary,self.match,{'home':{'id':'1'},'away':{'id':'2'}})
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['league'],'英联杯')
        self.assertEqual(result[0]['home'],self.match['away'])

    def test_background_sweep_covers_unvisited_fixtures_without_starvation(self):
        with tempfile.TemporaryDirectory() as folder:
            app=ConsoleService(Store(Path(folder)/'test.sqlite'))
            matches=[{**self.match, 'id':f'sporttery:{i}', 'provider_id':str(i)} for i in range(8)]
            app.store.upsert_matches(matches,self.now.isoformat())
            with patch.object(app,'detail',return_value={}) as fetch:
                self.assertEqual(app.refresh_upcoming_context(self.now,limit=3),3)
                first={c.args[0] for c in fetch.call_args_list}; fetch.reset_mock()
                app.refresh_upcoming_context(self.now,limit=3)
                second={c.args[0] for c in fetch.call_args_list}
                self.assertFalse(first & second)
            self.assertEqual(app.store.versions(),[])

    def test_european_odds_excludes_domestic_and_future_quotes(self):
        stamp=sources.parse_date(self.match['kickoff']).astimezone(sources.BEIJING).strftime('%Y-%m-%d %H:%M')
        current=self.now.astimezone(sources.BEIJING).strftime('%Y-%m-%d %H:%M:%S')
        future=(self.now+timedelta(days=1)).astimezone(sources.BEIJING).strftime('%Y-%m-%d %H:%M:%S')
        header=f'<div class="odds_header"><a href="/team/1/">主</a><a href="/team/2/">客</a>{stamp}</div>'
        def row(id,when): return f'<tr xls="row" id="{id}" data-time="{when}"><td>1</td><td title="来源公司">来源公司</td><td>2 3 4 2.2 3.2 3.5</td></tr>'
        result=public_enrichment.european_odds(self.match,{'id':'event','team_ids':['1','2']},header+row('1',current)+row('2',current)+row('3',future),self.now.isoformat())
        self.assertEqual([r['id'] for r in result['rows']],['2'])

    def test_process_enrichment_creates_new_report_but_repeat_fetch_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'test.sqlite')
            match={**self.match,'kickoff':(self.now-timedelta(hours=3)).isoformat()}
            store.upsert_matches([match],self.now.isoformat())
            detail={'official_score':'0-1','results':[], 'context':{}}
            first=postmatch.analyze(store,match,None,detail,{})
            detail['context']['match_facts']={'provider_event_id':'e','captured_at':self.now.isoformat(),'source':'ESPN',
                'statistics':[{'code':'totalShots','label':'射门','home':'17','away':'16'}], 'events':[]}
            second=postmatch.analyze(store,match,None,detail,{})
            self.assertGreater(second['id'],first['id'])
            detail['context']['match_facts']['captured_at']=(self.now+timedelta(minutes=1)).isoformat()
            third=postmatch.analyze(store,match,None,detail,{})
            self.assertEqual(third['id'],second['id'])
            self.assertNotIn('射门统计',second['missing_data'])
            self.assertEqual(store.versions(),[])

    def test_partial_primary_recent_uses_bounded_secondary_display_sample(self):
        row={'date':'2026-10-01','date_precision':'date','opponent':'球队','gf':0,'ga':0,'score_period':'unknown'}
        base={'available':True,'source':'ESPN','recent':[[row]*5,[row]*3]}
        extra={'recent':[[row]*5,[row]*5],'estimated_lineups':[],'injuries':[],'upcoming':[[],[]],
               'source':'500','source_url':'https://odds.500.com/','captured_at':self.now.isoformat(),'provider_event_id':'x'}
        with patch.object(fundamentals,'_base_context',return_value=base),patch.object(public_enrichment,'fetch',return_value=extra):
            result=fundamentals.fetch_context(self.match)
        self.assertEqual(len(result['recent'][1]),5)
        self.assertEqual(result['recent'][1][0]['score_period'],'unknown')
        self.assertIn('未确认加时口径',result['score_period_notice'])

    def test_fresh_cache_does_not_wait_on_background_refresh_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            app=ConsoleService(Store(Path(folder)/'test.sqlite'))
            app.store.upsert_matches([self.match],self.now.isoformat())
            app.store.save_detail(self.match['id'],self.detail)
            with patch.object(app,'lock',side_effect=AssertionError('fresh cache should not wait')):
                result=app.detail(self.match['id'])
            self.assertIn('briefing',result)

    def test_finished_index_still_requires_teams_and_time(self):
        match={**self.match,'league':'芬超','league_code':'FVL','home':'国际图尔','away':'赫尔火花','home_code':'ITU','away_code':'GNI'}
        stamp=sources.parse_date(match['kickoff']).astimezone(sources.BEIJING).strftime('%m-%d %H:%M')
        cells=['芬超','第9轮',stamp,'完','<a href="/team/1327/">国际图尔库</a>','4-3','<a href="/team/2687/">格尼斯坦</a>','1-1']
        html='<tr id="a123" gy="芬超,国际图尔库,格尼斯坦">'+''.join('<td>'+s+'</td>' for s in cells)+'</tr>'
        self.assertEqual(public_enrichment.fixture_identity(match,html),{'id':'123','team_ids':['1327','2687']})
        with self.assertRaises(sources.SourceError): public_enrichment.fixture_identity(match,html.replace(stamp,'01-01 00:00'))


if __name__=='__main__': unittest.main()
