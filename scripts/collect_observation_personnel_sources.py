"""Capture actual all-competition club schedules for current five-league teams."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from worldcup_mvp.console import model_registry
from worldcup_mvp.console.observation_personnel import SLUGS
from worldcup_mvp.console.observation_personnel_sources import fetch_team_schedule,fpl_team_mapping
from worldcup_mvp.console.observation_personnel_models import immutable
from worldcup_mvp.console.store import ROOT
from worldcup_mvp.console.sources import get_json


def main():
    dest=ROOT/'data/research/observation-personnel-sources';teams={};by_league={}
    for cid in SLUGS:
        rows=model_registry.dataset(cid,admission=False)['matches'];season=max(int(r['season']) for r in rows)
        current={r[s+'_id']:r[s+'_name'] for r in rows if int(r['season'])==season for s in ('home','away')}
        by_league[cid]={'season':season,'teams':current}
        for ident,name in current.items():teams[ident]={'season':season,'competition_id':cid,'name':name}
    report={'started_at':datetime.now(timezone.utc).isoformat(),'by_league':by_league,'teams':{},'failures':[]}
    def one(ident,meta):
        normalized,raw=fetch_team_schedule(ident,meta['season'])
        reference=immutable({'competition_id':meta['competition_id'],'captured_at':normalized['captured_at'],
            'source_url':normalized['source_url'],'data':raw},dest/'raw')
        normalized.update(competition_id=meta['competition_id'],raw_reference=reference)
        path=immutable(normalized,dest/'schedules')
        return ident,{'name':meta['name'],'competition_id':meta['competition_id'],'path':path,
            'observed_matches':len(normalized['rows']),'outside_own_league':sum(r['source_competition_slug']!=SLUGS[meta['competition_id']] for r in normalized['rows']),
            'source_leagues':sorted({r['source_competition_slug'] for r in normalized['rows'] if r['source_competition_slug']}),'captured_at':normalized['captured_at']}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(one,i,m):i for i,m in teams.items()}
        for future in as_completed(futures):
            try:
                ident,r=future.result();report['teams'][ident]=r
            except Exception as exc:report['failures'].append({'team_id':futures[future],'error':str(exc)[:200]})
    url='https://fantasy.premierleague.com/api/bootstrap-static/'
    try:
        fpl=get_json(url,timeout=20);capture=datetime.now(timezone.utc).isoformat()
        mapping,unmapped=fpl_team_mapping(fpl,by_league['eng.premier']['teams'])
        report['fpl']={'raw_reference':immutable({'competition_id':'eng.premier','captured_at':capture,'source_url':url,'data':fpl},dest/'raw'),
            'captured_at':capture,'team_mapping':mapping,'unmapped':unmapped,'players':len(fpl.get('elements',[])),
            'reported_news_or_non_available':sum(r.get('status')!='a' or bool(r.get('news')) for r in fpl.get('elements',[]))}
    except Exception as exc:report['fpl']={'error':str(exc)[:200]}
    report['finished_at']=datetime.now(timezone.utc).isoformat()
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S');path=dest/('collection-'+stamp+'.json')
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (dest/'latest.json').write_text(json.dumps({'file':str(path.relative_to(ROOT)).replace('\\','/')},indent=2),encoding='utf-8')
    print(json.dumps({'report':str(path.relative_to(ROOT)),'teams':len(report['teams']),'target':len(teams),
        'observed_team_matches':sum(r['observed_matches'] for r in report['teams'].values()),
        'outside_own_league_team_matches':sum(r['outside_own_league'] for r in report['teams'].values()),
        'failures':report['failures'],'fpl':report['fpl']},ensure_ascii=True))


if __name__=='__main__':main()
