import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worldcup_mvp.console.history_pipeline import (
    collect, normalize, prepare_import, season_quality, stage_rounds, staging_status,
)
from worldcup_mvp.console.store import fingerprint


def fixture(fid=1, when="2024-02-01 13:00"):
    return dict(fid=fid, hid=1, gid=2, status=5, stime=when, hscore=2,
                gscore=1, hhalfscore=1, ghalfscore=0, hname="A", gname="B")


def normalized(fid=1, when="2024-02-01 13:00"):
    return normalize(fixture(fid, when), "jp.j1", "2024", "regular", "https://example.com",
                     "2026-01-01T00:00:00+00:00")


class HistoryPipelineTests(unittest.TestCase):
    def test_period_scores_and_no_unstamped_odds(self):
        r = fixture(); r.update(win=2.0, draw=3.0, lost=4.0)
        out = normalize(r, 'jp.j1', '2024', 'regular', 'https://example.com', '2026-01-01T00:00:00Z')
        self.assertNotIn('odds', out)
        self.assertIsNone(out['result_available_at'])
        self.assertFalse(out['training_eligible'])
        self.assertEqual(out['home_half_goals'], 1)

    def test_unfinished_future_wrong_season_and_impossible_half(self):
        r = fixture(); r['status'] = 1
        self.assertIsNone(normalize(r, 'jp.j1', '2024', 'regular', '', '2026-01-01T00:00:00Z'))
        for r, season, captured in [(fixture(), '2023', '2026-01-01T00:00:00Z'),
                                     (fixture(), '2024', '2024-01-01T00:00:00Z'),
                                     ({**fixture(), 'hhalfscore': 3}, '2024', '2026-01-01T00:00:00Z')]:
            with self.assertRaises(ValueError):
                normalize(r, 'jp.j1', season, 'regular', '', captured)

    def test_season_counts_include_team_counts(self):
        rows = [normalized(), normalized(2, '2024-02-02 13:00')]
        spec = ('jp.j1', '2024', '1', 2, 2, 2)
        self.assertTrue(season_quality(rows, spec, [])['structure_ok'])
        self.assertFalse(season_quality(rows[:1], spec, [])['structure_ok'])
        self.assertFalse(season_quality(rows, spec, ['request failed'])['structure_ok'])
        rows[0]['kickoff_review_required'] = True
        self.assertFalse(season_quality(rows, spec, [])['structure_ok'])

    def test_stage_round_definitions_checked(self):
        self.assertEqual(stage_rounds('allRounds = [1,2,3]'), [1, 2, 3])
        for text in ('allRounds=[]', 'allRounds=[1,1]', 'allRounds=[100]'):
            with self.assertRaises(ValueError): stage_rounds(text)
        self.assertEqual(stage_rounds("allRounds=[]; roundType='other'", playoff=True), [0])
        with self.assertRaises(ValueError): stage_rounds('allRounds=[]', playoff=True)

    def test_special_season_requires_own_stage_and_date_range(self):
        row = fixture(when='2026-02-07 12:30')
        out = normalize(row, 'jp.j1', '2026-special', 'special_east', '', '2026-11-01T00:00:00Z')
        self.assertEqual(out['season'], '2026-special')
        for league, stage, when in [('jp.j1', 'regular', '2026-02-07 12:30'),
                                     ('kr.k1', 'special_east', '2026-02-07 12:30'),
                                     ('jp.j1', 'special_east', '2026-10-07 12:30')]:
            with self.assertRaises(ValueError):
                normalize(fixture(when=when), league, '2026-special', stage, '', '2026-11-01T00:00:00Z')

    def test_special_format_checks_regional_distribution_not_only_total(self):
        rows = []
        for group, offset in [('special_east', 0), ('special_west', 10)]:
            for home in range(offset, offset + 10):
                for away in range(offset, offset + 10):
                    if home != away:
                        rows.append(dict(home_id=str(home), away_id=str(away), stage=group,
                                         home_half_goals=0, independent_verification='pending'))
        for index in range(10):
            for home, away in [(index, index + 10), (index + 10, index)]:
                rows.append(dict(home_id=str(home), away_id=str(away), stage=f'playoff_{index*2+1}_{index*2+2}',
                                 home_half_goals=0, independent_verification='pending'))
        spec = ('jp.j1', '2026-special', '19524', 200, 20, 20)
        self.assertTrue(season_quality(rows, spec, [])['structure_ok'])
        rows[0]['stage'] = 'special_west'
        quality = season_quality(rows, spec, [])
        self.assertFalse(quality['structure_ok'])
        self.assertTrue(any('分组' in problem for problem in quality['issues']))

    def test_empty_and_corrupt_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'staging.json'
            self.assertFalse(staging_status(p)['available'])
            p.write_text('{')
            self.assertEqual(staging_status(p)['status'], '数据文件无效')

    def test_collection_is_quarantined_and_repeatable(self):
        class FakeArchive:
            def __init__(self, *args, **kwargs): self.entries = []
            def fetch(self, url):
                self.entries.append({'collected_at': '2026-01-01T00:00:00Z'})
                if 'getmatch' in url: return json.dumps([fixture(), fixture(2, '2024-02-02 13:00')])
                if 'jifen' in url: return 'stageId = 9; allRounds = [1];'
                return '<title>2024 日职</title><a href="/zuqiu-1/jifen-9/">查看完整赛程</a>'
        with tempfile.TemporaryDirectory() as tmp, patch('worldcup_mvp.console.history_pipeline.Archive', FakeArchive):
            p = Path(tmp) / 'staging.json'
            spec = [('jp.j1', '2024', '1', 2, 2, 2)]
            first = collect(p, specs=spec)
            second = collect(p, specs=spec)
            self.assertEqual(first['digest'], second['digest'])
            self.assertEqual(len(second['matches']), 2)
            self.assertEqual(second['rights_status'], 'unverified')
            self.assertFalse((Path(tmp) / 'club_history.json').exists())

    def test_admission_checks_version_permission_and_time_assumption(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp); p, m, out = [folder / x for x in ('staging.json', 'manifest.json', 'prepared.json')]
            data = dict(status='collected', matches=[normalized()], coverage=[dict(structure_ok=True, conflicts=0)])
            data['digest'] = fingerprint({'matches': data['matches'], 'coverage': data['coverage']})
            approval = dict(dataset_digest=data['digest'], training_permitted=True, quality_accepted=True,
                            license_reference='test only', source_url='https://example.com',
                            availability_policy='kickoff_plus_7_days_assumption')
            p.write_text(json.dumps(data)); m.write_text(json.dumps(approval))
            candidate = prepare_import(p, m, out)
            self.assertEqual(candidate['matches'][0]['result_available_at'], '2024-02-08T13:00:00+08:00')
            self.assertEqual(candidate['matches'][0]['availability_basis'], 'assumed_7_day_lag')
            for key, value in [('dataset_digest', 'wrong'), ('training_permitted', False),
                               ('quality_accepted', False), ('license_reference', ''), ('availability_policy', 'reported')]:
                m.write_text(json.dumps({**approval, key: value}))
                with self.assertRaises(ValueError): prepare_import(p, m, out)
            m.write_text(json.dumps(approval)); data['matches'][0]['home_goals'] = 5
            p.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, '摘要'): prepare_import(p, m, out)

    def test_unresolved_time_is_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp); p, m, out = [folder / x for x in ('staging.json', 'manifest.json', 'prepared.json')]
            r = normalized(); r['kickoff_review_required'] = True
            data = dict(status='collected', matches=[r], coverage=[dict(structure_ok=True, conflicts=0)])
            data['digest'] = fingerprint({'matches': data['matches'], 'coverage': data['coverage']})
            p.write_text(json.dumps(data)); m.write_text(json.dumps(dict(dataset_digest=data['digest'],
                training_permitted=True, quality_accepted=True, license_reference='test', source_url='https://example.com',
                availability_policy='kickoff_plus_7_days_assumption')))
            with self.assertRaisesRegex(ValueError, '时间差异'): prepare_import(p, m, out)

    def test_complete_season_can_be_selected_without_failed_current_season(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp); p, m, out = [folder / x for x in ('staging.json', 'manifest.json', 'prepared.json')]
            data = dict(status='collected', matches=[normalized()], coverage=[
                dict(id='jp.j1:2024', structure_ok=True, conflicts=0),
                dict(id='kr.k1:2026', structure_ok=False, conflicts=0)])
            data['digest'] = fingerprint({'matches': data['matches'], 'coverage': data['coverage']})
            approval = dict(dataset_digest=data['digest'], seasons=['jp.j1:2024'],
                training_permitted=True, quality_accepted=True, license_reference='test', source_url='https://example.com',
                availability_policy='kickoff_plus_7_days_assumption')
            p.write_text(json.dumps(data)); m.write_text(json.dumps(approval))
            self.assertEqual(len(prepare_import(p, m, out)['matches']), 1)
            approval['seasons'] = ['kr.k1:2026']; m.write_text(json.dumps(approval))
            with self.assertRaises(ValueError): prepare_import(p, m, out)


if __name__ == '__main__': unittest.main()
