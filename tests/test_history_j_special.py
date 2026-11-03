import json
import unittest
from unittest.mock import patch

from worldcup_mvp.console.history_j_special import parse_official_periods, verify_special


def page(home=("44'",), away=("45+1'",), extended=False, **updates):
    def team(code, goals):
        return dict(teamCode=code, score=len(goals), playerScoreList=[dict(scoreTime=m) for m in goals])
    props = dict(type="post-game", variant="game-details", tournamentName="明治安田Ｊ１百年構想リーグ",
                 homeTeam=team("ftokyo", home), awayTeam=team("kashima", away),
                 isExtended=extended, date="$D2026-02-07T04:30:00.000Z",
                 penalty=dict(homeTeamScore=5, awayTeamScore=4))
    props.update(updates)
    frame = [1, '14:' + json.dumps(["$", "$component", None, props]) + '\n']
    return '<script>self.__next_f.push(' + json.dumps(frame) + ')</script>'


class SpecialPeriodTests(unittest.TestCase):
    def test_shootout_is_not_part_of_score_and_first_half_includes_added_time(self):
        row = parse_official_periods(page())
        self.assertEqual([row['home_goals'], row['away_goals']], [1, 1])
        self.assertEqual([row['home_half_goals'], row['away_half_goals']], [1, 1])
        self.assertEqual(row['shootout']['homeTeamScore'], 5)

    def test_extra_time_is_excluded_but_ninety_plus_is_included(self):
        row = parse_official_periods(page(home=("90+7'", "100'", "113'"), away=("93'",), extended=True))
        self.assertEqual([row['home_goals'], row['away_goals']], [1, 0])
        self.assertEqual(row['home_after_extra_goals'], 3)
        self.assertEqual(row['home_half_goals'], 0)
        zero = parse_official_periods(page(home=("100'", "113'"), away=("93'",), extended=True))
        self.assertEqual([zero['home_goals'], zero['away_goals']], [0, 0])

    def test_incomplete_events_unknown_minutes_or_wrong_competition_are_rejected(self):
        for html in [page(homeTeam=dict(teamCode='ftokyo', score=2, playerScoreList=[])),
                     page(home=('unknown',)), page(home=("93'",)), page(type='live'),
                     page(tournamentName='Ｊ２'), page(isExtended=None), page() + page(), '<html></html>']:
            with self.subTest(html=html[:100]), self.assertRaises(ValueError):
                parse_official_periods(html)

    def test_cross_check_uses_team_and_date_before_comparing_scores(self):
        class FakeArchive:
            entries = [dict(collected_at='2026-11-01T00:00:00Z', sha256='test')]
            def fetch(self, url): return page()
        baseline = dict(id='500:1', season='2026-special', home='东京FC', away='鹿岛鹿角',
                        kickoff='2026-02-07T12:30:00+08:00', home_goals=1, away_goals=1,
                        home_half_goals=1, away_half_goals=1)
        with patch('worldcup_mvp.console.history_j_special.OFFICIAL_MATCHES', ('020702',)):
            row = dict(baseline)
            self.assertEqual(verify_special([row], FakeArchive()), [])
            self.assertEqual(row['independent_verification'], 'agree')
            self.assertEqual(row['period_verification']['home_id'], 'jleague:ftokyo')
            for change in [dict(home='鹿岛鹿角', away='东京FC'), dict(kickoff='2026-02-08T12:30:00+08:00')]:
                row = {**baseline, **change}
                self.assertTrue(verify_special([row], FakeArchive()))
                self.assertNotIn('independent_verification', row)
            row = {**baseline, 'home_goals': 5}
            self.assertTrue(verify_special([row], FakeArchive()))
            self.assertEqual(row['independent_verification'], 'conflict')


if __name__ == '__main__': unittest.main()
