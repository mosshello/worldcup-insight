import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from worldcup_mvp.console.history_audit import (
    Archive, compare_500, enrich_j, enrich_k, k_stage_id, k_provider_round,
    parse_500_odds, parse_j_schedule, parse_k_schedule,
)


def j_schedule():
    fields = ['2024', 'Ｊ１', '第１節第１日', '24/02/23(金)', '14:06',
              '<a href="https://www.jleague.jp/club/hiroshima/profile/">広島</a>',
              '<a href="/SFMS02/?match_card_id=1">2-0</a>',
              '<a href="https://www.jleague.jp/club/urawa/profile/">浦和</a>', '場', '100', 'TV']
    return '<table><tr>' + ''.join('<td>' + f + '</td>' for f in fields) + '</tr></table>'


def j_detail():
    return '''<table><tr><th id="team-name-l"><a href="/club/hiroshima/profile/">広島</a></th>
    <td class="score">2</td><td><dl><dd>1</dd><dt>前半</dt><dd>0</dd></dl>
    <dl><dd>1</dd><dt>後半</dt><dd>0</dd></dl></td><td class="score">0</td>
    <th id="team-name-r"><a href="/club/urawa/profile/">浦和</a></th></tr></table>'''


def k_schedule():
    return {'resultCode': '200', 'data': {'scheduleList': [dict(
        year=2024, leagueId=1, meetSeq=1, gameStatus='FE', endYn='Y', gameDate='2024.03.01',
        gameTime='14:00', roundId=1, gameId=1, homeGoal=1, awayGoal=0, homeTeam='K01',
        awayTeam='K03', homeTeamName='울산', awayTeamName='포항', codeName='regular')]}}


def k_event(name, side='HOME'):
    return dict(eventName=name, homeOrAway=side, year=2024, gameId=1, leagueId=1)


def k_detail():
    return {'resultCode': '200', 'data': dict(gameStatus='FE', homeGoal=1, awayGoal=0,
        firstHalf=[k_event('전반종료')], secondHalf=[k_event('득점'), k_event('후반종료')],
        EfirstHalf=[], EsecondHalf=[], homeScorer=[{'isOwnGoal': False}])}


class HistoryAuditTests(unittest.TestCase):
    def test_j_schedule_identity_and_timezone(self):
        r = parse_j_schedule(j_schedule(), 2024)[0]
        self.assertEqual(r['home_id'], 'jleague:hiroshima')
        self.assertEqual(r['kickoff'], '2024-02-23T14:06:00+09:00')
        self.assertEqual(r['kickoff_basis'], 'official_actual')
        self.assertEqual(enrich_j(r, j_detail())['home_half_goals'], 1)

    def test_j_mismatch_and_extra_time_rejected(self):
        r = parse_j_schedule(j_schedule(), 2024)[0]
        for html in (j_detail().replace('/club/urawa/', '/club/kashiwa/'),
                     j_detail().replace('<dd>1</dd><dt>後半', '<dd>0</dd><dt>後半')):
            with self.assertRaises(ValueError):
                enrich_j(r, html)

    def test_j_unfinished_wrong_year_and_duplicate_rejected(self):
        for html, year in ((j_schedule().replace('2-0', 'vs'), 2024),
                           (j_schedule(), 2023), (j_schedule() * 2, 2024)):
            with self.assertRaises(ValueError):
                parse_j_schedule(html, year)

    def test_k_halves_require_complete_events(self):
        r = parse_k_schedule(k_schedule(), 2024, '03')[0]
        self.assertEqual(enrich_k(r, k_detail())['home_half_goals'], 0)
        payload = k_detail()
        payload['data']['firstHalf'] = []
        self.assertIsNone(enrich_k(r, payload)['home_half_goals'])

    def test_k_own_goal_not_guessed(self):
        r = parse_k_schedule(k_schedule(), 2024, '03')[0]
        payload = k_detail()
        payload['data']['homeScorer'][0]['isOwnGoal'] = True
        self.assertIsNone(enrich_k(r, payload)['home_half_goals'])

    def test_k_alternate_end_marker_and_stage_rounds(self):
        r = parse_k_schedule(k_schedule(), 2024, '03')[0]
        payload = k_detail()
        payload['data']['secondHalf'][-1]['eventName'] = '경기종료'
        self.assertEqual(enrich_k(r, payload)['home_half_goals'], 0)
        self.assertEqual(k_stage_id({'冠军附加': '123', '联赛': '111'}, 'final_a'), '123')
        self.assertEqual(k_stage_id({'联赛赛程': '222'}, 'regular'), '222')
        self.assertEqual(k_provider_round(35, [1, 2, 3, 4, 5]), 2)
        self.assertEqual(k_provider_round(35, [34, 35, 36, 37, 38]), 35)
        with self.assertRaises(ValueError):
            k_provider_round(35, [1, 2])

    def test_k_identity_score_and_extra_time_rejected(self):
        r = parse_k_schedule(k_schedule(), 2024, '03')[0]
        variants = []
        p = k_detail(); p['data']['firstHalf'][0]['gameId'] = 2; variants.append(p)
        p = k_detail(); p['data']['homeGoal'] = 2; variants.append(p)
        p = k_detail(); p['data']['EfirstHalf'] = [k_event('득점')]; variants.append(p)
        for p in variants:
            with self.assertRaises(ValueError):
                enrich_k(r, p)

    def test_k_wrong_month_and_incomplete_match_rejected(self):
        with self.assertRaises(ValueError):
            parse_k_schedule(k_schedule(), 2024, '11')
        p = k_schedule(); p['data']['scheduleList'][0]['gameStatus'] = '1S'
        with self.assertRaises(ValueError):
            parse_k_schedule(p, 2024, '03')

    def test_odds_timestamp_and_invalid_values(self):
        html = '<table><tr><td>2.1</td><td>3.1</td><td>4.1</td><td>2026-10-30 11:05</td></tr></table>'
        self.assertEqual(parse_500_odds(html)[0]['source_updated_at'], '2026-10-30T11:05:00+08:00')
        with self.assertRaises(ValueError):
            parse_500_odds(html.replace('2.1', 'nan'))

    def test_cross_source_requires_both_teams_and_reports_conflict(self):
        r = enrich_j(parse_j_schedule(j_schedule(), 2024)[0], j_detail())
        c = dict(fid=123, hid=845, gid=1164, status=5, stime='2024-02-23 13:00',
                 hname='广岛三箭', gname='浦和红钻', hscore=2, gscore=0, hhalfscore=1, ghalfscore=0)
        result = compare_500(r, [c], 'https://example.com')
        self.assertEqual(result['status'], 'agree')
        self.assertEqual(result['kickoff_difference_minutes'], 6)
        self.assertFalse(result['odds_usable_for_backtest'])
        c['gname'] = '新潟天鹅'
        self.assertEqual(compare_500(r, [c], '')['status'], 'unmatched')
        c['gname'], c['hscore'] = '浦和红钻', 3
        self.assertEqual(compare_500(r, [c], '')['status'], 'conflict')

    def test_cross_source_duplicate_and_missing_half(self):
        r = enrich_j(parse_j_schedule(j_schedule(), 2024)[0], j_detail())
        c = dict(fid=123, hid=845, gid=1164, status=5, stime='2024-02-23 13:00',
                 hname='广岛三箭', gname='浦和红钻', hscore=2, gscore=0)
        self.assertEqual(compare_500(r, [c, c], '')['status'], 'unmatched')
        self.assertIsNone(compare_500(r, [c], '')['half_score_agrees'])
        c['stime'] = '2024-02-23 12:00'
        self.assertTrue(compare_500(r, [c], '')['kickoff_review_required'])

    def test_archive_cache_hash_and_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Archive(tmp)
            response = Mock()
            response.read.return_value = b'example'
            response.headers.get_content_charset.return_value = 'utf-8'
            response.status, response.url = 200, 'https://example.com'
            a.opener = Mock()
            a.opener.open.return_value.__enter__ = Mock(return_value=response)
            a.opener.open.return_value.__exit__ = Mock(return_value=False)
            a.fetch('https://example.com')
            a.offline = True
            self.assertEqual(a.fetch('https://example.com'), 'example')
            with self.assertRaises(ValueError):
                a.fetch('https://example.com/uncached')
            self.assertEqual(a.opener.open.call_count, 1)
            self.assertEqual(a.entries[0]['collected_at'], a.entries[1]['collected_at'])
            Path(a.entries[0]['raw_file']).write_text('tampered')
            with self.assertRaises(ValueError):
                a.fetch('https://example.com')

    def test_access_denial_is_archived_without_automatic_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Archive(tmp)
            a.opener = Mock()
            a.opener.open.side_effect = HTTPError('https://example.com', 403, 'Forbidden', {}, None)
            with self.assertRaises(HTTPError):
                a.fetch('https://example.com')
            with self.assertRaisesRegex(ValueError, 'no automatic retry'):
                a.fetch('https://example.com')
            self.assertEqual(a.opener.open.call_count, 1)

    def test_three_server_failures_pause_further_requests(self):
        with tempfile.TemporaryDirectory() as tmp, patch('worldcup_mvp.console.history_audit.time.sleep'):
            a = Archive(tmp)
            a.opener = Mock()
            a.opener.open.side_effect = lambda *args, **kwargs: (_ for _ in ()).throw(
                HTTPError('https://example.com', 503, 'Unavailable', {}, None))
            for i in range(3):
                with self.assertRaises(HTTPError): a.fetch(f'https://example.com/{i}')
            with self.assertRaisesRegex(ValueError, '暂停'):
                a.fetch('https://example.com/4')
            self.assertEqual(a.opener.open.call_count, 3)


if __name__ == '__main__':
    unittest.main()
