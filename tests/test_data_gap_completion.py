import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from tests import test_console_public_enrichment as public_fixtures
from tests.test_league_data import fixture, history_row, match
from worldcup_mvp.console import context_history, supplemental_facts as facts, public_enrichment as public
from worldcup_mvp.console import competition_workspace, fundamentals, personnel
from worldcup_mvp.console.sources import SourceError
from worldcup_mvp.console.store import fingerprint


class GapCompletionTests(unittest.TestCase):
    def seed(self):
        data = {'status': 'collected', 'collected_at': '2026-01-01T00:00:00Z',
                'matches': [history_row()], 'fixtures': [fixture()],
                'coverage': [{'competition_id': 'jp.j1', 'season': '2026-27', 'issues': []}],
                'sources': [{'competition_id': 'jp.j1', 'season': '2026-27'}]}
        data['digest'] = fingerprint({k: data[k] for k in ('matches', 'coverage')})
        data['fixture_digest'] = fingerprint(data['fixtures'])
        return data

    def test_history_refresh_replaces_only_same_season_without_mutating_seed(self):
        seed = self.seed(); before = copy.deepcopy(seed)
        current = self.seed(); current['matches'] = [history_row(99)]
        result = context_history.merge(seed, current, 'jp.j1')
        self.assertEqual([r['id'] for r in result['matches']], ['500:99'])
        self.assertEqual(seed, before)
        self.assertFalse(result['training_eligible'])
        self.assertEqual(result['purpose'], 'display_only')
        for bad in ({'status': 'collecting'}, {'fixtures': []},
                    {'coverage': [{'competition_id': 'kr.k1', 'season': '2026', 'issues': []}]},
                    {'coverage': [{'competition_id': 'jp.j1', 'season': '2026-27', 'issues': ['request failed']}]},
                    {'matches': [{**history_row(), 'competition_id': 'kr.k1'}]}):
            with self.assertRaises(SourceError): context_history.merge(seed, {**current, **bad}, 'jp.j1')

    def test_failed_refresh_does_not_overwrite_snapshot_or_retry_on_every_request(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'jp.j1.json'; data = self.seed()
            path.write_text(json.dumps(data), encoding='utf-8'); before = path.read_bytes()
            with patch.object(context_history, 'DIRECTORY', Path(directory)), \
                 patch.object(context_history, '_attempts', {}), \
                 patch('worldcup_mvp.console.league_data.load_history', return_value=data), \
                 patch.object(context_history, 'collect', side_effect=ValueError('source failed')) as collect:
                self.assertEqual(context_history.display_history(match()), data)
                self.assertEqual(context_history.display_history(match()), data)
                self.assertEqual(collect.call_count, 1)
            self.assertEqual(path.read_bytes(), before)

    def test_history_match_id_bypasses_rolling_index_but_not_detail_validation(self):
        sample = public_fixtures.PublicEnrichmentTests(); sample.setUp()
        m = {**sample.match, 'league_code': 'JD1'}
        f = {**fixture(), 'id': '500:123', 'home_id': '500:11', 'away_id': '500:22'}
        def page(url):
            self.assertNotIn('live.500.com', url)
            return sample.detail(), datetime.now(timezone.utc).isoformat()
        with patch('worldcup_mvp.console.league_data.load_history', return_value={}), \
             patch('worldcup_mvp.console.league_data.match_fixture', return_value=f), \
             patch.object(public, 'page', side_effect=page):
            result = public.fetch(m)
            self.assertEqual(len(result['estimated_lineups'][0]['players']), 11)
            self.assertTrue(result['estimated_lineups'][0]['observed_before_kickoff'])
            with patch.object(public, 'page', return_value=(sample.detail().replace('/team/22/', '/team/99/'), '2026-01-01T00:00:00Z')):
                with self.assertRaises(SourceError): public.fetch(m)

    def test_duplicate_estimated_players_are_not_a_complete_lineup(self):
        sample = public_fixtures.PublicEnrichmentTests(); sample.setUp()
        html = sample.detail().replace('<span class="td_sp3">11</span>球员11', '<span class="td_sp3">1</span>球员1')
        result = public.parse_page(sample.match, sample.identity, html, '2026-01-01T00:00:00Z')
        self.assertEqual(result['estimated_lineups'], [])

    def xg_html(self):
        return '''<div class="vsHeader"><table><tr><td class="tName r">主队</td><td class="tName l">客队</td></tr></table></div>
            <div class="swiper-slide sel">2026/27 J1. 3位</div><p>2026.11.1 16:00 Kick Off</p>
            <table><tr><td>9.999</td><td>-</td><td>1.561</td><td>ゴール期待値</td><td>1.573</td><td>-</td><td>8.888</td></tr></table>'''

    def test_xg_selects_match_values_not_season_averages_and_preserves_provenance(self):
        mapping = {'teams': ['主队', '客队'], 'season': '2026/27', 'source_url': 'https://example.com/'}
        m = {'kickoff': '2026-11-01T15:00:00+08:00'}
        r = facts.parse_xg(self.xg_html(), m, mapping, '2026-11-04T00:00:00Z')
        self.assertEqual((r['home'], r['away']), ('1.561', '1.573'))
        self.assertFalse(r['commercial_use_permitted'])
        self.assertFalse(r['training_eligible'])
        for html in [self.xg_html().replace('16:00', '15:00'), self.xg_html().replace('J1.', 'J2.'),
                     self.xg_html().replace('主队', '另一队'), self.xg_html().replace('1.561', 'NaN'), self.xg_html()*2]:
            with self.assertRaises(SourceError): facts.parse_xg(html, m, mapping, '2026-11-04T00:00:00Z')

    def suspension_html(self):
        return '''<h1>出場停止のお知らせ 2026/10/29</h1><table><tr><td>一森 純</td><td>Ｇ大阪</td><td></td><td>J1(a)</td><td>２０２６／２７明治安田Ｊ１リーグ第１３節第２日(11/01)</td></tr></table>'''

    def test_suspension_requires_correct_season_competition_team_and_date(self):
        m = {**match(), 'home_code': 'MCZ', 'away_code': 'GAB'}
        html = self.suspension_html()
        r = facts.parse_suspensions(html, m, 'https://example.com/', '2026-11-04T00:00:00Z')
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]['status'], '官方确认停赛')
        self.assertFalse(r[0]['observed_before_kickoff'])
        for h in [html.replace('11/01', '11/08'), html.replace('Ｊ１', 'Ｊ２'), html.replace('Ｇ大阪', '浦和'),
                  html.replace('２０２６／２７', '２０２５／２６'), html.replace('2026/10/29', '2026/11/02')]:
            self.assertEqual(facts.parse_suspensions(h, m, 'url', '2026-11-04T00:00:00Z'), [])

    def test_injury_announcement_does_not_claim_match_absence_and_expires(self):
        html = '<section id="news"><h3>馬場 晴也選手の負傷について</h3>2026年10月16日「右膝前十字靭帯断裂」と診断</section>'
        m = {'kickoff': '2026-10-31T15:00:00+08:00'}
        row = facts.parse_reysol_injury(html, m, 'home', 'url', '2026-11-04T00:00:00Z')
        self.assertEqual(row['status'], '伤情已公告，本场状态待核实')
        self.assertFalse(row['observed_before_kickoff'])
        for date in ['2026-10-01T15:00:00+08:00', '2026-12-31T15:00:00+08:00']:
            self.assertIsNone(facts.parse_reysol_injury(html, {'kickoff': date}, 'home', 'url', '2026-11-04T00:00:00Z'))

    def test_coverage_does_not_equate_one_side_or_statistics_with_complete_facts(self):
        result = competition_workspace.coverage_summary([{'context': {
            'recent': [[{}], []], 'venue_recent': [[], []], 'h2h_checked': True,
            'lineups': [{'side': 'home', 'status': 'confirmed', 'players': [{}]*11}],
            'estimated_lineups': [{'observed_before_kickoff': False}],
            'match_facts': {'statistics': [{'code': 'totalShots'}], 'events': []}}}])
        self.assertEqual(result['recent_both'], 0)
        self.assertEqual(result['confirmed_both'], 0)
        self.assertEqual(result['h2h_checked'], 1)
        self.assertEqual(result['statistics'], 1)
        self.assertEqual(result['events'], 0)
        self.assertEqual(result['estimated_after'], 1)


if __name__ == '__main__': unittest.main()
