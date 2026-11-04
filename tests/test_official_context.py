import copy
import json
import unittest
from unittest.mock import patch

from worldcup_mvp.console import official_context as official, fundamentals
from worldcup_mvp.console.sources import SourceError


IDENTITY = {'source_url': 'https://www.kleague.com/match.do?year=2026&leagueId=1&gameId=200&meetSeq=1',
            'source_home': 'K22', 'source_away': 'K35', 'provider_event_id': '2026:1:200',
            'state': 'completed', 'score': '1-1'}
MATCH = {'id': 'sporttery:2042388', 'league_code': 'KD1', 'home': '光州FC', 'away': '金泉尚武',
         'kickoff': '2026-10-31T15:30:00+08:00'}


def k_html():
    fields = {'MEET_YEAR': '2026', 'MEET_SEQ': '1', 'GAME_ID': '200', 'homeTeam': 'K22', 'awayTeam': 'K35'}
    html = '<script>' + ''.join(f"formData.append('{k}', '{v}');" for k, v in fields.items()) + '</script>'
    html += '<div class="match-lineup-player">'
    for team in ('K22', 'K35'):
        html += '<table><tr><td>-</td><td>감독</td><td>Coach</td></tr>'
        for i in range(11):
            html += f'''<tr onclick="playerDetailPop('{team}', '{100+i}')"><td>{i+1}</td><td>GK</td><td>Player {i}</td></tr>'''
        html += '</table><table></table>'
    return html + '</div>'


def event(**kwargs):
    return dict({'year': 2026, 'gameId': 200, 'leagueId': 1, 'meetSeq': None, 'halfType': 2,
                 'eventName': '득점', 'teamId': 'K22', 'timeMin': 47, 'timeSec': 12, 'num': 1,
                 'playerName': 'Player'}, **kwargs)


class OfficialContextTests(unittest.TestCase):
    def test_k_lineup_requires_eleven_distinct_players_and_matching_teams(self):
        rows = official.k_lineups(k_html(), IDENTITY)
        self.assertEqual([len(r['players']) for r in rows], [11, 11])
        self.assertEqual(rows[0]['players'][0]['id'], 'kleague:100')
        rows = official.k_lineups(k_html().replace("'110'", "'100'"), IDENTITY)
        self.assertTrue(all(r['status'] == 'unconfirmed' for r in rows))
        with self.assertRaises(official.IdentityError):
            official.k_lineups(k_html().replace("'GAME_ID', '200'", "'GAME_ID', '201'"), IDENTITY)

    def test_k_events_preserve_periods_stoppage_and_ignore_unknown_events(self):
        rows = official.k_events({'secondHalf': [event(), event(), event(num=2, eventName='unknown')],
                                  'EfirstHalf': [event(num=3, halfType=3, timeMin=16)]}, IDENTITY, MATCH)
        self.assertEqual([(r['period'], r['clock']) for r in rows], [(2, '90+2′'), (3, '105+1′')])
        self.assertEqual(rows[0]['seconds'], 5532)
        for bad in ({'gameId': 201}, {'leagueId': 2}, {'halfType': 1}, {'meetSeq': 2}):
            with self.assertRaises(official.IdentityError):
                official.k_events({'secondHalf': [event(**bad)]}, IDENTITY, MATCH)

    def fetch_k(self, identity=IDENTITY, html=None, fail_stats=False):
        def response(url, params=None):
            if 'match.do?' in url: return html or k_html()
            self.assertEqual(params, {'year': '2026', 'meetSeq': '1', 'gameId': '200'})
            if 'matchRecord' in url:
                if fail_stats: raise SourceError('unavailable')
                data = {'home': {'attempts': 9, 'expectedGoal': 0}, 'away': {'attempts': 14, 'expectedGoal': 0}}
            else: data = {'secondHalf': [event()]}
            return json.dumps({'resultCode': '200', 'data': data})
        with patch('worldcup_mvp.console.live.group_key', return_value=('kleague',)), \
             patch('worldcup_mvp.console.live.fetch_group', return_value=[]), \
             patch('worldcup_mvp.console.live.normalize', return_value=identity), \
             patch.object(official, 'read_page', side_effect=response):
            return official.fetch_kleague(MATCH)

    def test_k_partial_failure_and_zero_xg_never_claim_coverage(self):
        result = self.fetch_k()
        self.assertEqual([r['code'] for r in result['match_facts']['statistics']], ['totalShots'])
        partial = self.fetch_k(fail_stats=True)
        self.assertEqual(len(partial['lineups'][0]['players']), 11)
        self.assertEqual(partial['match_facts']['statistics'], [])
        self.assertTrue(partial['source_errors'])
        with self.assertRaises(official.IdentityError):
            self.fetch_k(html=k_html().replace("'GAME_ID', '200'", "'GAME_ID', '201'"))

    def test_pre_match_default_numbers_are_not_match_facts(self):
        result = self.fetch_k(identity={**IDENTITY, 'state': 'upcoming'})
        self.assertEqual(result['match_facts']['statistics'], [])
        self.assertEqual(result['match_facts']['events'], [])

    def test_statistics_require_two_finite_nonnegative_numbers(self):
        for values in ([None, 1], [True, 1], ['nan', 1], [-1, 1], ['', 1]):
            self.assertIsNone(official.statistic('totalShots', values))
        self.assertIsNone(official.statistic('possessionPct', [101, 1]))

    def test_j_identity_rejects_cup_team_and_time_conflicts(self):
        header = {'variant': 'game-details', 'tournament': 'j1', 'tournamentName': '明治安田Ｊ１リーグ',
                  'date': '$D2026-11-01T07:00:00.000Z', 'homeTeam': {'teamCode': 'machida'},
                  'awayTeam': {'teamCode': 'gosaka'}}
        match = {'kickoff': '2026-11-01T15:00:00+08:00'}
        mapping = {'teams': ['machida', 'gosaka']}
        self.assertEqual(official.j_identity([header], match, mapping), header)
        for changed in ({'tournament': 'cup'}, {'date': '$D2026-11-01T06:00:00Z'}, {'homeTeam': {'teamCode': 'other'}}):
            with self.assertRaises(SourceError): official.j_identity([{**header, **changed}], match, mapping)
        with self.assertRaises(SourceError): official.j_identity([header, header], match, mapping)
        chunk = '1:' + json.dumps({'child': header}, ensure_ascii=False) + '\n'
        html = ''.join('<script>self.__next_f.push(' + json.dumps([1, c]) + ')</script>' for c in (chunk[:20], chunk[20:]))
        self.assertIn(header, official.j_records(html))

    def test_official_fallback_keeps_historical_scope_and_has_no_espn_team_ids(self):
        base = {'available': True, 'recent': [[{'id': 'past'}], []], 'coverage': 'league_only', 'source': '500'}
        extra = self.fetch_k()
        for code in ('JD1', 'KD1'):
            with patch('worldcup_mvp.console.league_data.historical_context', return_value=copy.deepcopy(base)), \
                 patch('worldcup_mvp.console.sources.fetch_espn_context', side_effect=SourceError('time conflict')), \
                 patch.object(official, 'fetch_jleague', return_value=extra), \
                 patch.object(official, 'fetch_kleague', return_value=extra):
                result = fundamentals._base_context({**MATCH, 'league_code': code})
            self.assertEqual(result['recent'], base['recent'])
            self.assertEqual(result['coverage'], 'league_only')
            self.assertEqual(result['lineup_captured_at'], extra['captured_at'])
            self.assertNotIn('provider_ids', result)

    def test_official_still_works_when_historical_source_is_unavailable(self):
        with patch('worldcup_mvp.console.league_data.historical_context', side_effect=SourceError('missing')), \
             patch.object(official, 'fetch_kleague', return_value=self.fetch_k()):
            result = fundamentals._base_context(MATCH)
        self.assertTrue(result['available'])
        self.assertEqual(result['recent'], [[], []])

    def test_j_timeline_preserves_substitutions_at_same_time_and_added_time(self):
        def player(pid, kind, team='machida'):
            return {'name': pid, 'eventType': kind, 'legacyPlayerPhotoLookup': {'teamNameKey': team, 'playerId': pid}}
        row = {'period': '後半', 'time': 31, 'homePlayers': [player('a', 'sub-in'), player('b', 'sub-out')]}
        other = {**row, 'homePlayers': [player('c', 'sub-in'), player('d', 'sub-out')]}
        added = {'period': '後半', 'time': 45, 'extraTime': 2, 'awayPlayers': [player('e', 'yellow-card', 'gosaka')]}
        mapping = {'teams': ['machida', 'gosaka'], 'source_url': 'https://example.com/'}
        rows = official.j_events([row, row, other, added, {**added, 'period': 'PK'},
                                  {'period': {'label': 'unrelated'}}], MATCH, mapping)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[-1]['clock'], '90+2′')
        self.assertEqual(rows[-1]['seconds'], 5520)
        with self.assertRaises(official.IdentityError):
            official.j_events([{**row, 'homePlayers': [player('wrong', 'yellow-card', 'other')]}], MATCH, mapping)


if __name__ == '__main__':
    unittest.main()
