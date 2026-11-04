import copy
import json
import tempfile
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from tests.test_espn_history import fixtures
from worldcup_mvp.console.espn_competition_history import parse_event,prepare,fetch,collect_competition


def example(slug='uefa.champions',league='775',stage='group-stage'):
    e,s=fixtures();e['uid']=f's:600~l:{league}~e:123';s['header']['uid']=e['uid'];e['season']['slug']=stage
    return {'leagues':[{'id':league,'slug':slug}],'events':[e]},s


class CompetitionHistoryTests(unittest.TestCase):
    def test_explicit_source_identity_stage_and_era_isolation(self):
        b,s=example();e=b['events'][0]
        row,issue=parse_event(e,s,'2026-11-04T12:00:00+00:00','uefa.champions')
        self.assertIsNone(issue);self.assertEqual(row['competition_id'],'uefa.ucl')
        self.assertEqual(row['rules_era'],'group-stage');self.assertIsNone(row['home_half_goals'])
        self.assertIsNone(parse_event(e,s,'2026-11-04T12:00:00+00:00','uefa.europa')[0])
        e['season']['year']=2024;s['header']['season']['year']=2024
        self.assertEqual(parse_event(e,s,'2026-11-04T12:00:00+00:00','uefa.champions')[1]['reason'],'era_stage_conflict')
        with self.assertRaisesRegex(ValueError,'competition_mismatch'):
            prepare('uefa.europa',[b],{'123':s},'2026-11-04T12:00:00+00:00','request')

    def test_qualifying_opt_in_and_two_leg_proof_are_required(self):
        b,s=example('uefa.champions_qual','19874','first-round');e=b['events'][0]
        self.assertEqual(parse_event(e,s,'2026-11-04T12:00:00+00:00','uefa.champions_qual')[1]['reason'],'qualifying_policy_not_enabled')
        self.assertEqual(parse_event(e,s,'2026-11-04T12:00:00+00:00','uefa.champions_qual',allow_qualifying=True)[1]['reason'],'qualifying_leg_unverified')
        e['competitions'][0]['leg']={'value':1};s['header']['competitions'][0]['leg']={'value':1}
        row,issue=parse_event(e,s,'2026-11-04T12:00:00+00:00','uefa.champions_qual',allow_qualifying=True)
        self.assertIsNone(issue);self.assertEqual(row['stage'],'qualifying:first-round')
        e['competitions'][0]['status']['period']=4
        self.assertIsNone(parse_event(e,s,'2026-11-04T12:00:00+00:00','uefa.champions_qual',allow_qualifying=True)[0])

    def test_runtime_candidate_and_receipts_no_import(self):
        b,s=example();progress=[]
        with tempfile.TemporaryDirectory() as td:
            transport=lambda source,endpoint,raw:(b if endpoint.startswith('scoreboard') else s,{'url':endpoint,'raw_path':str(raw),'observed_at':datetime.now(timezone.utc).isoformat()})
            result=collect_competition('uefa.ucl',[2023],td,progress=progress.append,fetcher=transport)
            self.assertEqual(len(result['matches']),1)
            self.assertEqual(len(result['manifest']['source_receipts']),2)
            self.assertTrue((Path(td)/'candidate.json').exists())
            self.assertEqual(len(progress),2)
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(ValueError,'source_empty'):
                collect_competition('uefa.ucl',[2023],td,fetcher=lambda *args:({'events':[]},{'observed_at':datetime.now(timezone.utc).isoformat()}))

    def test_runtime_requires_explicit_qualifying_years_and_rejects_future_or_truncation(self):
        b,s=example()
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(ValueError,'explicit_qualifying_years'):
                collect_competition('uefa.ucl',[2023,2026],td,allow_qualifying=True)
            with self.assertRaisesRegex(ValueError,'receipt_time_invalid'):
                collect_competition('uefa.ucl',[2023],td,fetcher=lambda *args:(b,{'observed_at':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()}))
            with self.assertRaisesRegex(ValueError,'possibly_truncated'):
                collect_competition('uefa.ucl',[2023],td,fetcher=lambda *args:({'events':b['events']*1000},{'observed_at':datetime.now(timezone.utc).isoformat()}))

    def test_fetch_preserves_attempts_and_refetches_corrupt_cache(self):
        def transport(args,**kwargs):
            Path(args[args.index('-o')+1]).write_text('{"events":[]}')
            return SimpleNamespace(returncode=0,stdout=b'200')
        with tempfile.TemporaryDirectory() as td,patch('worldcup_mvp.console.espn_competition_history.subprocess.run',side_effect=transport) as run:
            _,receipt=fetch('uefa.champions','scoreboard?dates=2023',td)
            fetch('uefa.champions','scoreboard?dates=2023',td);self.assertEqual(run.call_count,1)
            Path(receipt['raw_path']).write_text('corrupted')
            _,new=fetch('uefa.champions','scoreboard?dates=2023',td)
            self.assertEqual(run.call_count,2);self.assertNotEqual(receipt['raw_path'],new['raw_path'])
            self.assertEqual(len(list(Path(td).glob('*.receipt.json'))),2)
