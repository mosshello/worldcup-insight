import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from worldcup_mvp.console.espn_history import parse_event, prepare, fetch_receipt


def fixtures():
    teams = [{'homeAway': s, 'score': str(g), 'team': {'id': t, 'displayName': t}}
             for s, g, t in [('home', 2, '11'), ('away', 1, '12')]]
    status = {'type': {'name': 'STATUS_FULL_TIME', 'completed': True}, 'period': 2}
    comp = {'id': '123', 'date': '2024-02-03T10:00Z', 'competitors': teams,
            'status': status, 'format': {'regulation': {'periods': 2}}}
    event = {'id': '123', 'uid': 's:600~l:3902~e:123', 'date': comp['date'],
             'season': {'year': 2023, 'slug': 'group-stage'}, 'competitions': [comp]}
    summary = {'header': {'id': '123', 'uid': event['uid'], 'season': {'year': 2023},
               'competitions': [dict(copy.deepcopy(comp), neutralSite=False)]}}
    return event, summary


class EspnHistoryTests(unittest.TestCase):
    def test_season_and_availability_are_not_calendar_or_live_claims(self):
        e, s = fixtures()
        row, issue = parse_event(e, s, '2026-11-04T12:00:00+00:00')
        self.assertIsNone(issue)
        self.assertEqual(row['season'], '2023')
        self.assertEqual(row['result_available_at'], '2024-02-10T10:00:00+00:00')
        self.assertEqual(row['availability_basis'], 'assumed_7_day_lag')
        self.assertIsNone(row['home_half_goals'])

    def test_identity_neutrality_result_and_time_fail_closed(self):
        mutations = [
            lambda e,s: e.update(uid='s:600~l:wrong~e:123'),
            lambda e,s: s['header']['competitions'][0].pop('neutralSite'),
            lambda e,s: s['header']['competitions'][0].update(neutralSite=True),
            lambda e,s: s['header']['competitions'][0]['competitors'][0]['team'].update(id='99'),
            lambda e,s: s['header']['competitions'][0]['competitors'][0].update(score='3'),
            lambda e,s: e['competitions'][0]['status'].update(period=4),
            lambda e,s: e['competitions'][0]['status']['type'].update(name='STATUS_FINAL_PEN'),
            lambda e,s: e['season'].update(slug='final'),
        ]
        for mutate in mutations:
            e,s=fixtures();mutate(e,s)
            row,issue=parse_event(e,s,'2026-11-04T12:00:00+00:00')
            self.assertIsNone(row); self.assertTrue(issue['reason'])
        e,s=fixtures()
        self.assertIsNone(parse_event(e,s,'2024-02-03T10:30:00+00:00')[0])

    def test_exact_duplicate_dedup_but_conflicting_duplicate_rejected(self):
        e,s=fixtures()
        board={'leagues':[{'id':'3902','slug':'afc.champions'}],'events':[e,e]}
        result=prepare([board],{'123':s},'2026-11-04T12:00:00+00:00','user-local-research')
        self.assertEqual(len(result['matches']),1)
        self.assertFalse(result['manifest']['commercial_use_permitted'])
        other=copy.deepcopy(e);other['season']['year']=2022
        board['events'].append(other)
        with self.assertRaisesRegex(ValueError,'duplicate_event_conflict'):
            prepare([board],{'123':s},'2026-11-04T12:00:00+00:00','request')
        board['leagues'][0]['slug']='afc.cup'
        with self.assertRaisesRegex(ValueError,'competition_mismatch'):
            prepare([board],{},'2026-11-04T12:00:00+00:00','request')

    def test_transport_failure_creates_failed_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            with patch('worldcup_mvp.console.espn_history.subprocess.run',side_effect=TimeoutError('timed out')):
                with self.assertRaises(TimeoutError):fetch_receipt('summary?event=123',td)
            receipt=json.loads(next(Path(td).glob('*.receipt.json')).read_text())
            self.assertEqual(receipt['status'],'failed')
            self.assertIn('observed_at',receipt)

    def test_centralised_editions_override_provider_false_default(self):
        for year in (2020, 2021, 2022):
            e,s=fixtures()
            e['season']['year']=year
            s['header']['season']['year']=year
            row,issue=parse_event(e,s,'2026-11-04T12:00:00+00:00')
            self.assertIsNone(row)
            self.assertEqual(issue['reason'],'centralised_edition_venue_unverified')

    def test_summary_optional_period_does_not_invent_one(self):
        e,s=fixtures()
        s['header']['competitions'][0]['status'].pop('period')
        self.assertIsNotNone(parse_event(e,s,'2026-11-04T12:00:00+00:00')[0])
        e['competitions'][0]['status'].pop('period')
        self.assertIsNone(parse_event(e,s,'2026-11-04T12:00:00+00:00')[0])

    def test_http_receipts_preserve_denial_and_empty_success(self):
        for code in (403,429,200):
            def transport(args,**kwargs):
                Path(args[args.index('-o')+1]).write_text('{"events":[]}')
                return SimpleNamespace(returncode=0,stdout=str(code).encode())
            with tempfile.TemporaryDirectory() as td, patch('worldcup_mvp.console.espn_history.subprocess.run',side_effect=transport):
                if code==200:
                    data,receipt=fetch_receipt('scoreboard?dates=2024',td)
                    self.assertEqual(receipt['status'],'succeeded_empty')
                else:
                    with self.assertRaisesRegex(ValueError,'provider_request_failed'):
                        fetch_receipt('scoreboard?dates=2024',td)
                saved=json.loads(next(Path(td).glob('*.receipt.json')).read_text())
                self.assertEqual(saved['http_status'],str(code))
                self.assertTrue(saved['sha256'])
