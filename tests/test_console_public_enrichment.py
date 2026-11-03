"""Public adapters reject identity drift and never promote predicted players to confirmed."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from worldcup_mvp.console import public_enrichment as public, schedules, personnel, sources, fundamentals
from worldcup_mvp.console.competition_labels import label


class PublicEnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.kickoff = datetime.now(timezone.utc)+timedelta(days=1)
        self.match = {'id':'sporttery:sample','provider_id':'sample','home':'主队','away':'客队',
                      'league':'欧冠','league_code':'UCL','kickoff':self.kickoff.isoformat()}
        self.identity = {'id':'123','team_ids':['11','22']}

    def index(self):
        stamp=self.kickoff.astimezone(sources.BEIJING).strftime('%m-%d %H:%M')
        cells=['001','欧冠','第4轮',stamp,'未','<a href="/team/11/">主队</a>','','<a href="/team/22/">客队</a>']
        return '<tr gy="欧冠,主队,客队" fid="123">'+''.join('<td>'+s+'</td>' for s in cells)+'</tr>'

    def detail(self):
        stamp=self.kickoff.astimezone(sources.BEIJING).strftime('%Y-%m-%d %H:%M')
        header=f'<div class="odds_header"><a href="/team/11/">主队</a><a href="/team/22/">客队</a>{stamp}</div>'
        players=''.join(f'<tr><td><span class="td_sp3">{i}</span>球员{i}(中场)</td><td></td></tr>' for i in range(1,12))
        return header+'<div class="M_box starting"><div class="team_a"><table><tr><th>首发</th><th>替补</th></tr>'+players+'<tr><th>伤病</th><th>停赛</th></tr><tr><td></td><td></td></tr></table></div></div>'

    def test_unique_league_sides_kickoff_required(self):
        text=self.index()
        self.assertEqual(public.fixture_identity(self.match,text),self.identity)
        for bad in [text.replace('欧冠,','联赛,'),text.replace('主队,客队','客队,主队'),text+text]:
            with self.assertRaises(sources.SourceError):public.fixture_identity(self.match,bad)

    def test_full_date_and_team_ids_checked_in_detail(self):
        with self.assertRaises(sources.SourceError):
            public.parse_page(self.match,self.identity,self.detail().replace('/team/22/','/team/99/'),'2026-01-01T00:00:00Z')
        value=public.parse_page(self.match,self.identity,self.detail(),'2026-01-01T00:00:00Z')
        self.assertEqual(value['estimated_lineups'][0]['status'],'estimated')
        self.assertEqual(len(value['estimated_lineups'][0]['players']),11)
        self.assertEqual(value['injuries'],[])  # Empty rows are not a clean bill of health.

    def test_official_bulletin_only_applies_to_exact_match_and_flags_conflict(self):
        match={'provider_id':'2042499','home':'马竞','away':'拜仁','kickoff':'2026-11-04T04:00:00+08:00'}
        context={'estimated_lineups':[{'side':'away','players':[{'name':'泽格·格纳布里'}]}]}
        personnel.annotate(context,match)
        self.assertEqual(len(context['injuries']),7)
        self.assertFalse(context['injuries_verified'])
        self.assertEqual(context['estimated_lineups'][0]['conflicts'],['泽格·格纳布里'])
        self.assertEqual(personnel.official_bulletins({**match,'kickoff':'2026-12-04T04:00:00+08:00'}),[])

    def test_future_schedule_team_identity_and_competition_type(self):
        data={'team':{'id':'11'},'events':[{'id':'event','date':self.kickoff.isoformat(),
            'league':{'name':'German Cup'},'competitions':[{'competitors':[
                {'homeAway':'home','team':{'id':'11','displayName':'Bayern Munich'}},
                {'homeAway':'away','team':{'id':'22','displayName':'Barcelona'}}]}]}]}
        r=schedules.normalize(data,'11',datetime.now(timezone.utc))
        self.assertEqual(r[0]['league'],'德国杯')
        self.assertEqual(r[0]['competition_type'],'国内杯赛')
        self.assertEqual(r[0]['opponent'],'巴塞罗那')
        with self.assertRaises(sources.SourceError):schedules.normalize(data,'99',datetime.now(timezone.utc))
        self.assertEqual(schedules.normalize(data,'11',self.kickoff),[])
        self.assertEqual(label('AFC Champions League Elite')['competition_type'],'洲际杯赛')

    def test_500_fallback_survives_primary_source_failure(self):
        data={'estimated_lineups':[],'injuries':[],'upcoming':[[],[]],
              'recent':[[{'opponent':'测试球队','date':'2026-01-01','gf':1,'ga':0}],[]],
              'source':'500','source_url':'https://odds.500.com/','captured_at':'2026-01-01T00:00:00Z','provider_event_id':'123'}
        with patch.object(fundamentals,'_base_context',side_effect=sources.SourceError('unavailable')), \
             patch.object(public,'fetch',return_value=data):
            result=fundamentals.fetch_context(self.match)
        self.assertTrue(result['available'])
        self.assertEqual(result['source'],'500')
        self.assertEqual(result['coverage'],'mixed_display_only')
        self.assertFalse(result['injuries_verified'])

    def test_asian_league_mapping_is_scoped(self):
        self.assertEqual(sources.LEAGUES['ACL'],'afc.champions')
        self.assertEqual(sources.ALIASES['afc.champions']['NCJ'],'NEW')
        self.assertNotIn('NCJ',sources.ALIASES['eng.1'])


if __name__=='__main__': unittest.main()
